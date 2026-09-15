"""Stage 2: Camelot 2.0 with the Table Transformer backend (flavor="ml").

The model only decides where rows and columns are. Cell text still comes from the PDF
text layer, so this stage cannot invent a number. Falls back to the classic "lattice"
flavor when the ml extra is not installed.
"""

import threading
from pathlib import Path

from pdf2sdmx.core.table import ExtractedTable

METHOD = "camelot_ml"
FALLBACK_METHOD = "camelot_lattice"

# transformers' lazy model loading is not thread safe: two concurrent first calls end with
# "Cannot copy out of meta tensor". Camelot caches the models after the first call.
_ML_LOCK = threading.Lock()


def extract(pdf_path: Path, page_number: int) -> list[ExtractedTable]:
    import camelot

    flavor, method = ("ml", METHOD) if _ml_available() else ("lattice", FALLBACK_METHOD)
    with _ML_LOCK:
        tables = camelot.read_pdf(str(pdf_path), pages=str(page_number), flavor=flavor)
    out = []
    for table in tables:
        cells = table.df.astype(str).values.tolist()
        report = dict(table.parsing_report)
        report["flavor"] = flavor
        out.append(ExtractedTable(cells, page_number, method, report))
    return out


def _ml_available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


def warm_up(sample_pdf: Path, page_number: int) -> str:
    """Run one real extraction so Camelot loads and caches the Table Transformer models
    before the first user request."""
    if not _ml_available():
        return "ml extra not installed, lattice fallback in use"
    if not sample_pdf.exists():
        return f"no sample at {sample_pdf}, models will load on first request"
    extract(sample_pdf, page_number)
    return "table transformer models ready"
