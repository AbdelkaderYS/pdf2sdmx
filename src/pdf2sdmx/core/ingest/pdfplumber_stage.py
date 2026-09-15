"""Stage 1: read the PDF text layer with pdfplumber. Deterministic, no model."""

from pathlib import Path

import pdfplumber

from pdf2sdmx.core.table import ExtractedTable

METHOD = "pdfplumber"

# Ruled tables first, then whitespace-aligned tables. Most INS tables have ruling lines.
_STRATEGIES = (
    {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
    {"vertical_strategy": "text", "horizontal_strategy": "text"},
)


def extract(pdf_path: Path, page_number: int) -> list[ExtractedTable]:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_number - 1]
        for settings in _STRATEGIES:
            tables = page.extract_tables(table_settings=settings)
            tables = [t for t in tables if len(t) >= 2 and len(t[0]) >= 2]
            if tables:
                strategy = settings["vertical_strategy"]
                return [ExtractedTable(t, page_number, METHOD, {"strategy": strategy}) for t in tables]
    return []


def page_count(pdf_path: Path) -> int:
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def page_text(pdf_path: Path, page_number: int) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return pdf.pages[page_number - 1].extract_text() or ""
