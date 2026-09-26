"""One call from PDF page to validated long table and SDMX files. Used by the API and the UI."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from pdf2sdmx.config import settings
from pdf2sdmx.core import reshape, sdmx_out, validate
from pdf2sdmx.core.ingest import cascade, pdfplumber_stage
from pdf2sdmx.core.validate import Check

# A period in the page text: a range such as "2024/2025" or "2023-2024", or a lone year.
PERIOD_IN_TEXT = re.compile(r"\b((?:19|20)\d{2}\s*[/-]\s*(?:19|20)?\d{2}|(?:19|20)\d{2})\b")
# "base 100 en 2014", "base 2014 = 100": the year an index is set to, not the table's period.
BASE_YEAR = re.compile(r"\bbase\s*(?:100\s*)?(?:=|en|in)?\s*(?:19|20)\d{2}(?:\s*=\s*100)?", re.I)

# The caption printed above a table: a number, then the title.
CAPTION = re.compile(r"^tabl(?:eau|e)\s*n?[°o]?\s*[\d.]+(?:[a-z]\.?)?\s*:", re.I)
# The unit line a report often prints under the caption, for the whole table at once.
UNIT_LINE = re.compile(r"^unit[ée]s?\s*:\s*(.+)$", re.I)


@dataclass
class TableResult:
    frame: pd.DataFrame
    checks: list[Check]
    long: pd.DataFrame
    resolved_by: str
    title: str = ""


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
        frames = [t.long.assign(TABLE=i, TABLE_TITLE=t.title) for i, t in enumerate(self.tables, 1)]
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
    given = (time_period or "").strip()
    period = given or detect_period(pdf_path, page)
    unit = (unit or "").strip()  # empty, not "UNKNOWN": reshape falls back last, after the caption
    subject = (subject or "").strip()
    mapping = reshape.load_mapping(mapping_path or settings.mapping_file)

    headings = captions_on_page(pdf_path, page)
    tables = []
    for position, resolved in enumerate(result.tables):
        title, printed_unit = headings[position] if position < len(headings) else ("", "")
        checks = validate.run_checks(resolved.frame)
        long = reshape.to_long(
            resolved.frame,
            mapping=mapping,
            # A period printed in the table itself wins over both; reshape reads it from the axes.
            time_period=given or period_in(title) or period,
            # A caption with no unit line often names the unit in its own words.
            unit=printed_unit or unit or title,
            method=resolved.row_methods,
            source=source,
            checks=checks,
            subject=subject,
            title=title,
        )
        tables.append(TableResult(resolved.frame, checks, long, resolved.resolved_by, title))

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


def captions_on_page(pdf_path: Path, page: int) -> list[tuple[str, str]]:
    """(title, unit) for each table on this page, in reading order.

    A caption sits above its table, sometimes followed by a unit line that holds for the
    whole table. Tables are matched by position, and one whose caption sits on the previous
    page gets none rather than a wrong one.
    """
    found: list[tuple[str, str]] = []
    for line in pdfplumber_stage.page_text(pdf_path, page).splitlines():
        line = line.strip()
        if CAPTION.match(line):
            found.append((line, ""))
            continue
        unit = UNIT_LINE.match(line)
        if unit and found and not found[-1][1]:
            found[-1] = (found[-1][0], unit.group(1).strip())
    return found


def period_in(caption: str) -> str:
    """The period a caption names, when it names exactly one. One page can hold tables of
    different years, so the caption knows a table's period better than the page does."""
    found = {re.sub(r"\s+", "", m) for m in PERIOD_IN_TEXT.findall(BASE_YEAR.sub(" ", caption))}
    return found.pop() if len(found) == 1 else ""


def detect_period(pdf_path: Path, page: int) -> str:
    """Most common year-like token near the top of the page, else UNKNOWN."""
    text = pdfplumber_stage.page_text(pdf_path, page)[:600]
    found = [re.sub(r"\s+", "", m) for m in PERIOD_IN_TEXT.findall(text)]
    if not found:
        return "UNKNOWN"
    # On a tie prefer a range over a lone year, which is often just the page header.
    return max(set(found), key=lambda f: (found.count(f), len(f)))


def page_count(pdf_path: Path) -> int:
    return pdfplumber_stage.page_count(pdf_path)


def combine(results: list[PageResult]) -> pd.DataFrame:
    """Long table for every page that produced observations, with a PAGE column."""
    frames = [r.long.assign(PAGE=r.page) for r in results if not r.long.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def render_page(pdf_path: Path, page: int, resolution: int = 60):
    """Small raster of one page for a preview panel."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        return pdf.pages[page - 1].to_image(resolution=resolution).original
