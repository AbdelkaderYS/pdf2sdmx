"""Run the extraction stages in order, keep the stage with the most clean tables, then
repair its broken rows from the other stages when that lowers the failure count.

A page can hold several tables. Each stage returns all of them; tables are matched
across stages by their data column headers and the overlap of their row labels.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from pdf2sdmx.core import validate
from pdf2sdmx.core.ingest import camelot_stage, docling_stage, pdfplumber_stage
from pdf2sdmx.core.numbers import parse_number
from pdf2sdmx.core.quality import GateResult, gate_table
from pdf2sdmx.core.table import ExtractedTable

log = logging.getLogger(__name__)

Stage = Callable[[Path, int], list[ExtractedTable]]

STAGES: list[tuple[str, Stage]] = [
    (pdfplumber_stage.METHOD, pdfplumber_stage.extract),
    (camelot_stage.METHOD, camelot_stage.extract),
    (docling_stage.METHOD, docling_stage.extract),
]


@dataclass
class Attempt:
    method: str
    tables_found: int
    gate: GateResult | None
    seconds: float
    failed_checks: int | None = None
    error: str = ""


@dataclass
class Candidate:
    method: str
    frame: pd.DataFrame
    gate: GateResult
    failed_checks: int


@dataclass
class ResolvedTable:
    frame: pd.DataFrame
    gate: GateResult
    method: str
    row_methods: dict[str, str] = field(default_factory=dict)

    @property
    def resolved_by(self) -> str:
        helpers = sorted({m for m in self.row_methods.values() if m != self.method})
        return "+".join([self.method, *helpers])


@dataclass
class CascadeResult:
    tables: list[ResolvedTable]
    attempts: list[Attempt] = field(default_factory=list)
    rejected: pd.DataFrame | None = None  # best attempt when nothing passed the gate

    @property
    def accepted(self) -> bool:
        return bool(self.tables)

    @property
    def frame(self) -> pd.DataFrame:
        """First accepted table, or the rejected attempt, or nothing."""
        if self.tables:
            return self.tables[0].frame
        return self.rejected if self.rejected is not None else pd.DataFrame()

    @property
    def resolved_by(self) -> str:
        if not self.tables:
            return "manual"
        return "+".join(sorted({t.resolved_by for t in self.tables}))


def run(pdf_path: Path, page_number: int, stages: list[tuple[str, Stage]] | None = None) -> CascadeResult:
    """Try each stage on one page. Stops early at a stage whose tables all pass with zero failures."""
    attempts: list[Attempt] = []
    per_stage: dict[str, list[Candidate]] = {}
    best_rejected: Candidate | None = None

    for name, stage in stages or STAGES:
        if name == docling_stage.METHOD and not docling_stage.available():
            attempts.append(Attempt(name, 0, None, 0.0, error="not installed"))
            continue
        started = time.perf_counter()
        try:
            tables = stage(pdf_path, page_number)
        except Exception as exc:  # a broken stage must not stop the cascade
            log.warning("stage %s failed on page %s: %s", name, page_number, exc)
            attempts.append(Attempt(name, 0, None, time.perf_counter() - started, error=str(exc)[:200]))
            continue

        accepted, rejected = _gate_all(name, tables)
        seconds = time.perf_counter() - started
        if rejected and (best_rejected is None or rejected.gate.score > best_rejected.gate.score):
            best_rejected = rejected
        if not accepted:
            attempts.append(Attempt(name, len(tables), rejected.gate if rejected else None, seconds))
            continue
        failures = sum(c.failed_checks for c in accepted)
        attempts.append(Attempt(name, len(tables), accepted[0].gate, seconds, failures))
        per_stage[name] = accepted
        if failures == 0:
            break

    if not per_stage:
        return CascadeResult([], attempts, best_rejected.frame if best_rejected else None)

    winner = min(per_stage, key=lambda m: (-len(per_stage[m]), sum(c.failed_checks for c in per_stage[m])))
    donors = [c for m, cs in per_stage.items() if m != winner for c in cs]
    resolved = []
    for candidate in per_stage[winner]:
        donor = _matching_table(candidate, donors)
        frame, row_methods = repair_rows(candidate, [donor] if donor else [])
        resolved.append(ResolvedTable(frame, candidate.gate, winner, row_methods))
    return CascadeResult(resolved, attempts)


def _gate_all(method: str, tables: list[ExtractedTable]) -> tuple[list[Candidate], Candidate | None]:
    """Accepted candidates in page order, plus the best rejected one for display."""
    accepted, rejected = [], []
    for table in tables:
        frame = table.to_frame()
        if frame.shape[1] < 2:  # every data column was blank, nothing to gate
            continue
        gate = gate_table(frame)
        if gate.accepted:
            failed = sum(c.status == "fail" for c in validate.run_checks(frame))
            accepted.append(Candidate(method, frame, gate, failed))
        else:
            rejected.append(Candidate(method, frame, gate, 10**6))
    best_rejected = max(rejected, key=lambda c: c.gate.score) if rejected else None
    return accepted, best_rejected


def _matching_table(target: Candidate, donors: list[Candidate]) -> Candidate | None:
    """Donor with the same data columns and the most cells that read the same number.

    Two tables on one page often share headers and row labels (livestock 2024 and 2025),
    so labels alone cannot tell them apart. Equal values can.
    """
    scored = [(_agreeing_cells(target.frame, d.frame), d) for d in donors]
    scored = [(n, d) for n, d in scored if n > 0]
    return max(scored, key=lambda pair: pair[0])[1] if scored else None


def _agreeing_cells(a: pd.DataFrame, b: pd.DataFrame) -> int:
    if list(a.columns[1:]) != list(b.columns[1:]):
        return 0
    rows_b = dict(zip(validate.row_labels(b), b.iloc[:, 1:].to_numpy(), strict=True))
    count = 0
    for label, row in zip(validate.row_labels(a), a.iloc[:, 1:].to_numpy(), strict=True):
        if label not in rows_b:
            continue
        for x, y in zip(row, rows_b[label], strict=True):
            px, py = parse_number(x), parse_number(y)
            if px.status == "ok" and py.status == "ok" and px.value == py.value:
                count += 1
    return count


def repair_rows(winner: Candidate, donors: list[Candidate]) -> tuple[pd.DataFrame, dict[str, str]]:
    """Swap in a donor's row for each unreadable or gappy row of the winner.

    An unreadable row is kept only if the table-level failure count goes down. A row with
    gaps (a stage that glued three rows into one leaves the next two mostly blank) is kept
    if the donor agrees on every cell already read, fills at least one gap, and the count
    does not go up. Every observation keeps the stage it came from.
    """
    frame = winner.frame.copy()
    labels = validate.row_labels(frame)
    row_methods = dict.fromkeys(labels, winner.method)
    best_failures = winner.failed_checks
    if not donors or best_failures == 0:
        return frame, row_methods

    for position, label in enumerate(labels):
        current = frame.iloc[position, 1:]
        unreadable = _has_unreadable(current)
        for donor in donors:
            row = _matching_row(donor.frame, frame.columns, label)
            if row is None or _has_unreadable(row.iloc[1:]) or _disagrees(current, row.iloc[1:]):
                continue
            if not unreadable and not _fills_gaps(current, row.iloc[1:]):
                continue
            trial = frame.copy()
            trial.iloc[position] = row.to_numpy()
            failures = sum(c.status == "fail" for c in validate.run_checks(trial))
            if failures < best_failures or (not unreadable and failures == best_failures):
                frame, best_failures, row_methods[label] = trial, failures, donor.method
                break
    return frame, row_methods


def _fills_gaps(current: pd.Series, donor: pd.Series) -> bool:
    """Donor has a number where current has a missing marker."""
    pairs = [(parse_number(a), parse_number(b)) for a, b in zip(current, donor, strict=True)]
    return any(pa.status == "missing" and pb.status == "ok" for pa, pb in pairs)


def _disagrees(current: pd.Series, donor: pd.Series) -> bool:
    """A cell both rows read as a number, with different values. Such a donor row is never used."""
    for a, b in zip(current, donor, strict=True):
        pa, pb = parse_number(a), parse_number(b)
        if pa.status == "ok" and pb.status == "ok" and pa.value != pb.value:
            return True
    return False


def _has_unreadable(cells: pd.Series) -> bool:
    return any(parse_number(c).status == "error" for c in cells)


def _matching_row(donor: pd.DataFrame, columns: pd.Index, label: str) -> pd.Series | None:
    if list(donor.columns[1:]) != list(columns[1:]):  # label column names differ between stages
        return None
    hits = [i for i, lb in enumerate(validate.row_labels(donor)) if lb == label]
    return donor.iloc[hits[0]] if hits else None
