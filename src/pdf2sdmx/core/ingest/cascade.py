"""Run the extraction stages in order, keep the one with the fewest failed checks, then
repair its unreadable rows from the other stages when that lowers the failure count."""

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
class CascadeResult:
    frame: pd.DataFrame
    gate: GateResult | None
    method: str
    row_methods: dict[str, str] = field(default_factory=dict)
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return bool(self.gate and self.gate.accepted)

    @property
    def resolved_by(self) -> str:
        """Winning stage, plus any stage that repaired rows. "manual" when nothing passed the gate."""
        if not self.accepted:
            return "manual"
        helpers = sorted({m for m in self.row_methods.values() if m != self.method})
        return "+".join([self.method, *helpers])


def run(pdf_path: Path, page_number: int, stages: list[tuple[str, Stage]] | None = None) -> CascadeResult:
    """Try each stage on one page. Stops early at a stage with zero failed checks."""
    attempts: list[Attempt] = []
    candidates: list[Candidate] = []

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

        candidate = _best_candidate(name, tables)
        seconds = time.perf_counter() - started
        if candidate is None:
            attempts.append(Attempt(name, len(tables), None, seconds))
            continue
        attempts.append(Attempt(name, len(tables), candidate.gate, seconds, candidate.failed_checks))
        candidates.append(candidate)
        if candidate.gate.accepted and candidate.failed_checks == 0:
            break

    if not candidates:
        return CascadeResult(pd.DataFrame(), None, "manual", attempts=attempts)
    winner = min(candidates, key=lambda c: (not c.gate.accepted, c.failed_checks, -c.gate.score))
    frame, row_methods = repair_rows(winner, [c for c in candidates if c is not winner])
    return CascadeResult(frame, winner.gate, winner.method, row_methods, attempts)


def _best_candidate(method: str, tables: list[ExtractedTable]) -> Candidate | None:
    scored = []
    for table in tables:
        frame = table.to_frame()
        gate = gate_table(frame)
        failed = sum(c.status == "fail" for c in validate.run_checks(frame)) if gate.accepted else 10**6
        scored.append(Candidate(method, frame, gate, failed))
    if not scored:
        return None
    return min(scored, key=lambda c: (not c.gate.accepted, c.failed_checks, -c.gate.score))


def repair_rows(winner: Candidate, donors: list[Candidate]) -> tuple[pd.DataFrame, dict[str, str]]:
    """Swap in a donor's row for each unreadable or empty row of the winner.

    An unreadable row is kept only if the page-level failure count goes down. A row with
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
            if row is None or _has_unreadable(row.iloc[1:]):
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
    """Donor has a number where current is missing, and never disagrees where both have one."""
    filled = disagreed = 0
    for a, b in zip(current, donor, strict=True):
        pa, pb = parse_number(a), parse_number(b)
        if pa.status == "missing" and pb.status == "ok":
            filled += 1
        elif pa.status == "ok" and pb.status == "ok" and pa.value != pb.value:
            disagreed += 1
    return filled > 0 and disagreed == 0


def _has_unreadable(cells: pd.Series) -> bool:
    return any(parse_number(c).status == "error" for c in cells)


def _matching_row(donor: pd.DataFrame, columns: pd.Index, label: str) -> pd.Series | None:
    if list(donor.columns[1:]) != list(columns[1:]):  # label column names differ between stages
        return None
    hits = [i for i, lb in enumerate(validate.row_labels(donor)) if lb == label]
    return donor.iloc[hits[0]] if hits else None
