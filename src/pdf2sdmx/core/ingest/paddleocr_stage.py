"""Stage 3: PaddleOCR-VL 1.6 (Baidu, Apache 2.0). A vision language model that reads the
page as an image, so it is the only stage that can misread a digit. Every table it returns
is flagged for manual review, and the cascade only reaches it when the text stages fail.

The pipeline processes whole PDFs page by page with no page selection, so this stage
renders the requested page to an image first and gives the model that image.
"""

import io
import re
import tempfile
import threading
from pathlib import Path

import pandas as pd
import pdfplumber

from pdf2sdmx.core.table import ExtractedTable

METHOD = "paddleocr_vl"
RENDER_DPI = 200

_LOCK = threading.Lock()
_PIPELINE = None


def extract(pdf_path: Path, page_number: int) -> list[ExtractedTable]:
    image = _render(pdf_path, page_number)
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / f"page_{page_number}.png"
        image.save(png)
        results = list(_pipeline().predict(input=str(png)))
    out = []
    for result in results:
        for cells in tables_in(result.json):
            out.append(ExtractedTable(cells, page_number, METHOD, {"needs_manual_review": True}))
    return out


def tables_in(result_json: dict) -> list[list[list[str]]]:
    """Cell grids for every block the model labelled as a table.

    Table content arrives as an HTML <table> or, depending on the version, as a Markdown
    pipe table. Both are turned into rows of strings; the first row is the header.
    """
    data = result_json.get("res", result_json)
    grids = []
    for block in data.get("parsing_res_list", []):
        if block.get("block_label") != "table":
            continue
        content = block.get("block_content", "") or ""
        cells = _html_table(content) if "<table" in content.lower() else _markdown_table(content)
        if cells:
            grids.append(cells)
    return grids


def _html_table(content: str) -> list[list[str]]:
    try:
        frames = pd.read_html(io.StringIO(content), header=None)
    except ValueError:
        return []
    if not frames:
        return []
    frame = frames[0].fillna("").astype(str)
    return frame.values.tolist()


def _markdown_table(content: str) -> list[list[str]]:
    rows = []
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if re.fullmatch(r"\|[\s:|-]+\|", line):  # the |---|---| separator
            continue
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _render(pdf_path: Path, page_number: int):
    with pdfplumber.open(pdf_path) as pdf:
        return pdf.pages[page_number - 1].to_image(resolution=RENDER_DPI).original


def _pipeline():
    """One model per process. Loading takes time and memory, and it is not thread safe."""
    global _PIPELINE
    with _LOCK:
        if _PIPELINE is None:
            from paddleocr import PaddleOCRVL

            _PIPELINE = PaddleOCRVL(
                pipeline_version="v1.6",
                device="cpu",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_chart_recognition=False,
                use_seal_recognition=False,
            )
        return _PIPELINE


def available() -> bool:
    try:
        import paddleocr  # noqa: F401
    except ImportError:
        return False
    return True
