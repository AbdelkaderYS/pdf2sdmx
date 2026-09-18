"""Gradio front end for the Hugging Face Space.

Two panels. On the left the document: drop a PDF, browse its pages, start the run. On the
right what came out: a summary, the tables, and the SDMX files. There is one control, the
page slider, and one folded panel, the cells to review. Everything else is a result.

A page that yields nothing says why on the spot, in the progress line, rather than in a
diagnostic table nobody opens.
"""

import tempfile
import time
import zipfile
from pathlib import Path

import gradio as gr
import pandas as pd

from pdf2sdmx.config import settings
from pdf2sdmx.core import pipeline, sdmx_out
from pdf2sdmx.core.pipeline import PageResult

ACCENT = "teal"
TABLE_SLOTS = 3
SAMPLE = settings.data_raw.parent / "samples" / "ins_bulletin_3T25_p20-23.pdf"

CSS = """
.gradio-container { max-width: 1280px !important; }
#header h1 { margin: 0; font-size: 1.4rem; }
#summary { min-height: 4.5rem; padding: 0.4rem 0; }
#summary p { margin: 0.15rem 0; }
#progress { color: var(--body-text-color-subdued); font-size: 0.9rem; min-height: 1.2rem; }
#hint { color: var(--body-text-color-subdued); padding: 0.4rem 0 0.8rem 0; }
#formats { color: var(--body-text-color-subdued); font-size: 0.9rem; }
#pages .grid-wrap { padding: 0.2rem 0; }
footer { display: none !important; }
"""

INTRO = (
    "Drop an INS Niger PDF report. Every page is read, each table is checked for arithmetic "
    "consistency, and the result comes out as SDMX. Each number keeps the name of the stage that read it."
)
PLACEHOLDER = (
    "Drop a PDF on the left, browse it with the page slider, then press Start. Pages that "
    "hold a table appear here as thumbnails; click one to see its tables."
)
FORMATS = (
    "**SDMX-CSV** is one observation per line, readable in Excel, and assumes the receiver "
    "already has the data structure. **SDMX-ML** carries the structure itself, so a registry "
    "can validate the data against it."
)


def process(file):
    """Generator: yields UI updates as pages are processed."""
    if file is None:
        raise gr.Error("Drop a PDF first")
    pdf_path = Path(file)
    n_pages = pipeline.page_count(pdf_path)

    started = time.perf_counter()
    found: list[dict] = []  # one entry per page with a table: result and its rendered preview
    preview = None
    for page in range(1, n_pages + 1):
        preview = pipeline.render_page(pdf_path, page)
        yield _state(preview, f"Reading page {page} of {n_pages}...", found, None)
        result = pipeline.run_page(pdf_path, page)
        if not result.long.empty:
            found.append({"result": result, "preview": preview})
            note = _table_count(len(result.tables))
        else:
            note = f"no table, {no_table_reason(result)}"
        yield _state(preview, f"Page {page} of {n_pages}: {note}", found, result)

    # Gradio may drop intermediate yields, so the last one carries the full final state.
    elapsed = f"{time.perf_counter() - started:.0f} s"
    if not found:
        yield _state(preview, f"Done in {elapsed}: {n_pages} pages read, no table found", found, None)
        return
    results = [f["result"] for f in found]
    files = _output_files(pdf_path, results)
    archive = _write_archive(pdf_path, files)
    n_tables = sum(len(r.tables) for r in results)
    last = found[-1]
    yield _state(
        last["preview"],
        f"Done in {elapsed}: {n_pages} pages read, {n_tables} tables found",
        found,
        last["result"],
        archive,
        files,
    )


def no_table_reason(result: PageResult) -> str:
    """Why this page produced nothing, in the words of the stage that gave up.

    A rejected table says more than a stage that never ran, so a gate reason wins over an
    error. Without any gate the page was skipped before extraction, and the first error
    holds the reason.
    """
    rejected = ""
    for attempt in result.attempts:
        if attempt.gate is not None and not attempt.gate.accepted:
            rejected = attempt.gate.reason
    if rejected:
        return rejected
    for attempt in result.attempts:
        if attempt.error:
            return attempt.error
    return "nothing that looks like a table"


def show_page(found: list[dict], evt: gr.SelectData):
    """Gallery click: show that page on the left and its tables on the right."""
    entry = found[evt.index]
    result = entry["result"]
    return (
        gr.update(value=entry["preview"], visible=True),
        gr.update(value=result.page),
        f"Page {result.page}, {_table_count(len(result.tables))}",
        *table_slots(result),
    )


def _state(preview, progress, found, current, archive=None, files=None):
    """Values for every output component, in the order declared in build()."""
    files = files or {}
    results = [f["result"] for f in found]
    return (
        gr.update(value=preview, visible=True) if preview is not None else gr.update(),
        progress,
        gr.update(visible=not results),
        gr.update(value=summary_markdown(results, files), visible=bool(results)),
        gr.update(value=archive, visible=archive is not None),
        gr.update(visible=bool(found)),
        gr.update(value=[_gallery_item(f) for f in found], visible=bool(found)),
        found,
        *table_slots(current),
        pipeline.combine(results) if results else pd.DataFrame(),
        _preview_text(files, "_sdmx.csv"),
        _preview_text(files, "_data.xml"),
        checks_frame(results),
    )


def _table_count(n: int) -> str:
    return f"{n} table{'s' if n > 1 else ''}"


def _gallery_item(entry: dict) -> tuple:
    result = entry["result"]
    return entry["preview"], f"p. {result.page}, {_table_count(len(result.tables))}"


def _preview_text(files: dict[str, str | bytes], suffix: str, max_lines: int = 60) -> str:
    """First lines of an output file, shown once the run is complete."""
    match = next((v for k, v in files.items() if k.endswith(suffix)), None)
    if match is None:
        return "Available when the run is complete."
    text = match.decode() if isinstance(match, bytes) else match
    lines = text.splitlines()
    tail = f"\n... {len(lines) - max_lines} more lines in the download" if len(lines) > max_lines else ""
    return "\n".join(lines[:max_lines]) + tail


def table_slots(current: PageResult | None) -> list:
    """One dataframe per table on the current page, unused slots hidden.

    Slots are hidden while a page is being read so that Gradio remounts them with the new
    table height; an updated dataframe otherwise keeps the previous table's scroll area.
    """
    if current is None or not current.tables:
        return [gr.update(visible=False) for _ in range(TABLE_SLOTS)]
    slots = []
    for i in range(TABLE_SLOTS):
        if i < len(current.tables):
            label = (
                f"Page {current.page}, table {i + 1} of {len(current.tables)}, read by {current.tables[i].resolved_by}"
            )
            slots.append(gr.update(value=current.tables[i].frame, label=label, visible=True))
        else:
            slots.append(gr.update(visible=False))
    return slots


def summary_markdown(results: list[PageResult], files: dict[str, str | bytes] | None = None) -> str:
    """The four lines that matter, in the order a reader wants them."""
    if not results:
        return ""
    long = pipeline.combine(results)
    flagged = int((long["OBS_STATUS"] != "A").sum())
    checked = _cells_checked(results)
    stages = long["EXTRACTION_METHOD"].value_counts().to_dict()
    stage_text = ", ".join(f"{k} {v}" for k, v in stages.items())
    lines = [
        f"**{sum(len(r.tables) for r in results)} tables** on pages {', '.join(str(r.page) for r in results)}",
        f"**{len(long)} observations**, {checked / len(long):.0%} covered by a check, {flagged} flagged for review",
        f"**Read by:** {stage_text}",
    ]
    conformance = conformance_markdown(files or {})
    if conformance:
        lines.append(conformance)
    return "\n\n".join(lines)


def _cells_checked(results: list[PageResult]) -> int:
    """Cells named by at least one arithmetic check.

    Published next to the observation count because a value nobody checked carries
    OBS_STATUS A for want of a contradiction, not because anything confirmed it.
    """
    cells = set()
    for result in results:
        for check in result.checks:
            if check.row and check.column:
                cells.add((result.page, check.row, check.column))
    return len(cells)


def conformance_markdown(files: dict[str, str | bytes]) -> str:
    """Whether the SDMX-ML output passes the official schemas. Empty until the run ends."""
    messages = [content for name, content in files.items() if name.endswith(".xml")]
    if not messages:
        return ""
    as_bytes = [m if isinstance(m, bytes) else m.encode() for m in messages]
    result = sdmx_out.validate(*as_bytes)
    if result is None:
        return "**SDMX-ML 2.1:** not checked, the official schemas are not installed"
    if result:
        return "**SDMX-ML 2.1:** valid against the official schemas"
    return "**SDMX-ML 2.1:** does not pass the official schemas"


def checks_frame(results: list[PageResult]) -> pd.DataFrame:
    rows = [
        {"page": r.page, "check": c.name, "row": c.row, "column": c.column, "detail": c.detail}
        for r in results
        for c in r.checks
        if c.status in ("fail", "warn")
    ]
    return pd.DataFrame(rows, columns=["page", "check", "row", "column", "detail"])


def _output_files(pdf_path: Path, results: list[PageResult]) -> dict[str, str | bytes]:
    """Long CSV, SDMX-CSV, SDMX-ML structure and data, cells to review, for the whole document."""
    long = pipeline.combine(results)
    structure_xml, data_xml = sdmx_out.to_sdmx_ml(long)
    to_review = pd.concat([r.to_review.assign(page=r.page) for r in results], ignore_index=True)
    stem = pdf_path.stem
    return {
        f"{stem}_long.csv": long.to_csv(index=False),
        f"{stem}_sdmx.csv": sdmx_out.to_sdmx_csv(long),
        f"{stem}_structure.xml": structure_xml,
        f"{stem}_data.xml": data_xml,
        f"{stem}_to_review.csv": to_review.to_csv(index=False),
    }


def _write_archive(pdf_path: Path, files: dict[str, str | bytes]) -> str:
    out = Path(tempfile.mkdtemp(prefix="pdf2sdmx_")) / f"{pdf_path.stem}_sdmx.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content if isinstance(content, bytes) else content.encode())
    return str(out)


def open_document(file) -> tuple:
    """Show page 1 and set the slider to the length of this document."""
    if not file:
        return gr.update(visible=False), gr.update(visible=False)
    path = Path(file)
    n_pages = pipeline.page_count(path)
    return (
        gr.update(value=pipeline.render_page(path, 1), visible=True),
        gr.update(minimum=1, maximum=n_pages, value=1, label=f"Page, 1 to {n_pages}", visible=True),
    )


def show_page_number(file, number):
    """Slider move: render that page. The run and the gallery set the image directly."""
    if not file:
        return gr.update(visible=False)
    return gr.update(value=pipeline.render_page(Path(file), int(number)), visible=True)


def load_sample() -> tuple:
    preview, slider = open_document(str(SAMPLE))
    return str(SAMPLE), preview, slider


def theme() -> gr.themes.Base:
    return gr.themes.Base(
        primary_hue=ACCENT,
        neutral_hue="slate",
        font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
    ).set(button_primary_background_fill="*primary_600", button_primary_background_fill_hover="*primary_700")


def build() -> gr.Blocks:
    """Theme and CSS are applied by the caller: launch(theme=, css=) or mount_gradio_app(theme=, css=)."""
    with gr.Blocks(title="pdf2sdmx") as demo:
        with gr.Row(elem_id="header"):
            gr.Markdown(f"# pdf2sdmx\n\n{INTRO}")
        with gr.Row():
            with gr.Column(scale=1):
                file = gr.File(label="Drop a PDF here", file_types=[".pdf"], type="filepath", height=110)
                with gr.Row():
                    start = gr.Button("Start", variant="primary")
                    stop = gr.Button("Stop")
                sample = gr.Button("Load the sample: INS bulletin 3T 2025, pages 20 to 23", size="sm")
                progress = gr.Markdown(elem_id="progress")
                preview = gr.Image(show_label=False, interactive=False, height=560, container=False, visible=False)
                # The range is a placeholder: open_document sets it to the document length.
                page = gr.Slider(minimum=1, maximum=2, value=1, step=1, label="Page", visible=False)
            with gr.Column(scale=2):
                hint = gr.Markdown(PLACEHOLDER, elem_id="hint")
                summary = gr.Markdown(elem_id="summary", visible=False)
                archive = gr.File(label="Download the zip: long CSV, SDMX-CSV, SDMX-ML, cells to review", visible=False)
                gallery_title = gr.Markdown("**Pages with tables.** Click one to see it.", visible=False)
                gallery = gr.Gallery(
                    show_label=False,
                    columns=8,
                    height=140,
                    object_fit="contain",
                    allow_preview=False,
                    visible=False,
                    elem_id="pages",
                )
                found = gr.State([])
                with gr.Tabs():
                    with gr.Tab("Tables"):
                        tables = [
                            gr.Dataframe(wrap=True, interactive=False, max_height=600, visible=False)
                            for _ in range(TABLE_SLOTS)
                        ]
                    with gr.Tab("Data"):
                        gr.Markdown(
                            "One row per number, with region, indicator, period, unit and the stage that read it."
                        )
                        long = gr.Dataframe(interactive=False, wrap=True, max_height=700)
                    with gr.Tab("SDMX"):
                        gr.Markdown(FORMATS, elem_id="formats")
                        sdmx_csv = gr.Code(label="SDMX-CSV 2.0", language=None, interactive=False, max_lines=20)
                        sdmx_xml = gr.Code(
                            label="SDMX-ML 2.1 data message", language=None, interactive=False, max_lines=20
                        )
                with gr.Accordion("Cells flagged for review", open=False):
                    gr.Markdown("A flagged cell stays in the output with OBS_STATUS = U, low reliability.")
                    checks = gr.Dataframe(interactive=False, wrap=True)

        outputs = [
            preview,
            progress,
            hint,
            summary,
            archive,
            gallery_title,
            gallery,
            found,
            *tables,
            long,
            sdmx_csv,
            sdmx_xml,
            checks,
        ]
        # The page loop reports its own progress; Gradio's elapsed-time overlay would only add noise.
        run_event = start.click(process, file, outputs, show_progress="hidden")
        stop.click(None, cancels=[run_event])
        gallery.select(show_page, found, [preview, page, progress, *tables], show_progress="hidden")
        sample.click(load_sample, outputs=[file, preview, page], show_progress="hidden")
        file.upload(open_document, file, [preview, page], show_progress="hidden")
        page.release(show_page_number, [file, page], preview, show_progress="hidden")
    return demo


if __name__ == "__main__":
    build().launch(theme=theme(), css=CSS)
