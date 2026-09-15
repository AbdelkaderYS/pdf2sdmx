"""One call from PDF page to validated long table and SDMX files. Used by the API and the UI."""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from pdf2sdmx.config import settings
from pdf2sdmx.core import reshape, sdmx_out, validate
from pdf2sdmx.core.ingest import cascade, pdfplumber_stage
from pdf2sdmx.core.validate import Check

# "campagne 2024/2025", "2023-2024", or a lone year in the page text
PERIOD_IN_TEXT = re.compile(r"\b((?:19|20)\d{2}\s*[/-]\s*(?:19|20)?\d{2}|(?:19|20)\d{2})\b")


@dataclass
class TableResult:
    frame: pd.DataFrame
    checks: list[Check]
    long: pd.DataFrame
    resolved_by: str


@dataclass
class PageResult:
    source: str
    page: int
    resolved_by: str
    accepted: bool
    tables: list[TableResult]
    attempts: list[cascade.Attempt]
    time_period: str
    rejected: pd.DataFrame = field(default_factory=pd.DataFrame)
    sdmx_csv: str = ""
    sdmx_structure_xml: bytes = b""
    sdmx_data_xml: bytes = b""

    @property
    def table(self) -> pd.DataFrame:
        """First table, or the best rejected attempt for display."""
        return self.tables[0].frame if self.tables else self.rejected

    @property
    def checks(self) -> list[Check]:
        return [c for t in self.tables for c in t.checks]

    @property
    def long(self) -> pd.DataFrame:
        frames = [t.long.assign(TABLE=i) for i, t in enumerate(self.tables, 1)]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    @property
    def to_review(self) -> pd.DataFrame:
        return validate.failed_cells(self.checks)

    @property
    def check_counts(self) -> dict[str, int]:
        counts = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}
        for c in self.checks:
            counts[c.status] += 1
        return counts


def run_page(
    pdf_path: Path,
    page: int,
    *,
    time_period: str | None = "",
    unit: str | None = "",
    subject: str | None = "UNKNOWN",
    mapping_path: Path | None = None,
) -> PageResult:
    """Extract, validate, reshape and serialise every table on one page. Never raises on a bad page.

    Text options may arrive as None from an empty UI field.
    """
    result = cascade.run(pdf_path, page)
    source = pdf_path.name
    period = (time_period or "").strip() or detect_period(pdf_path, page)
    unit = (unit or "").strip() or "UNKNOWN"
    subject = (subject or "").strip() or "UNKNOWN"
    mapping = reshape.load_mapping(mapping_path or settings.mapping_file)

    tables = []
    for resolved in result.tables:
        checks = validate.run_checks(resolved.frame)
        long = reshape.to_long(
            resolved.frame,
            mapping=mapping,
            time_period=period,
            unit=unit,
            method=resolved.row_methods,
            source=source,
            checks=checks,
            subject=subject,
        )
        tables.append(TableResult(resolved.frame, checks, long, resolved.resolved_by))

    page_result = PageResult(
        source,
        page,
        result.resolved_by,
        result.accepted,
        tables,
        result.attempts,
        period,
        rejected=result.rejected if result.rejected is not None else pd.DataFrame(),
    )
    long = page_result.long
    if not long.empty:
        page_result.sdmx_csv = sdmx_out.to_sdmx_csv(long)
        page_result.sdmx_structure_xml, page_result.sdmx_data_xml = sdmx_out.to_sdmx_ml(long)
    return page_result


def detect_period(pdf_path: Path, page: int) -> str:
    """Most common year-like token near the top of the page, else UNKNOWN."""
    text = pdfplumber_stage.page_text(pdf_path, page)[:600]
    found = [re.sub(r"\s+", "", m) for m in PERIOD_IN_TEXT.findall(text)]
    if not found:
        return "UNKNOWN"
    # On a tie prefer a range such as 2024/2025 over a lone year from the page header.
    return max(set(found), key=lambda f: (found.count(f), len(f)))


def page_count(pdf_path: Path) -> int:
    return pdfplumber_stage.page_count(pdf_path)


def run_document(pdf_path: Path, pages: list[int] | None = None, **options) -> Iterator[PageResult]:
    """Yield one PageResult per page, in order. Pages without a table yield an empty result."""
    for page in pages or range(1, page_count(pdf_path) + 1):
        yield run_page(pdf_path, page, **options)


def combine(results: list[PageResult]) -> pd.DataFrame:
    """Long table for every page that produced observations, with a PAGE column."""
    frames = [r.long.assign(PAGE=r.page) for r in results if not r.long.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def parse_pages(text: str, n_pages: int) -> list[int]:
    """ "21", "20-25" or "12, 21, 30". Empty means every page."""
    pages: list[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            pages.extend(range(int(start), int(end) + 1))
        else:
            pages.append(int(part))
    return [p for p in pages if 1 <= p <= n_pages]


def render_page(pdf_path: Path, page: int, resolution: int = 60):
    """Small raster of one page for a preview panel."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        return pdf.pages[page - 1].to_image(resolution=resolution).original
