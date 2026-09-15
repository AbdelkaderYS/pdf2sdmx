"""Stage 3: Docling (IBM) with TableFormer. Vision based, so it is the only stage that can
misread a digit on a scanned page. Every table it returns is flagged for manual review.
"""

from pathlib import Path

from pdf2sdmx.core.table import ExtractedTable

METHOD = "docling"


def extract(pdf_path: Path, page_number: int) -> list[ExtractedTable]:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(do_ocr=True, do_table_structure=True)
    options.table_structure_options.do_cell_matching = True
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    result = converter.convert(str(pdf_path), page_range=(page_number, page_number))

    out = []
    for table in result.document.tables:
        frame = table.export_to_dataframe(doc=result.document)
        cells = [list(map(str, frame.columns))] + frame.astype(str).values.tolist()
        out.append(ExtractedTable(cells, page_number, METHOD, {"needs_manual_review": True}))
    return out


def available() -> bool:
    try:
        import docling  # noqa: F401
    except ImportError:
        return False
    return True
