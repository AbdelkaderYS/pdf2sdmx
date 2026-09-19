"""Gradio front end for the Hugging Face Space.

Two panels. On the left the document: drop a PDF, browse its pages, start the run. On the
right what came out: the figures, the tables under the titles the report gives them, and
the SDMX files.

The result is read as figures rather than sentences. A list of forty page numbers tells a
reader nothing; five numbers and a conformance badge tell them where they stand.

A page that yields nothing says why on the spot, in the progress line, rather than in a
diagnostic table nobody opens.
"""

import html
import tempfile
import time
import zipfile
from pathlib import Path

import gradio as gr
import pandas as pd

from pdf2sdmx.config import settings
from pdf2sdmx.core import pipeline, sdmx_out
from pdf2sdmx.core.ingest import paddleocr_stage
from pdf2sdmx.core.pipeline import PageResult
from pdf2sdmx.core.reshape import NOT_IDENTIFIED

ACCENT = "teal"
TABLE_SLOTS = 3
SAMPLE = settings.data_raw.parent / "samples" / "ins_bulletin_3T25_p20-23.pdf"
THIN_SPACE = " "

CSS = """
.gradio-container { max-width: 1280px !important; }
#header h1 { margin: 0 0 0.2rem 0; font-size: 1.35rem; letter-spacing: -0.01em; }
#header p { margin: 0; color: var(--body-text-color-subdued); font-size: 0.92rem; }
#progress { color: var(--body-text-color-subdued); font-size: 0.88rem; min-height: 1.3rem; }
#hint { color: var(--body-text-color-subdued); font-size: 0.92rem; padding: 0.6rem 0 1rem 0; }
#formats { color: var(--body-text-color-subdued); font-size: 0.86rem; }
#pages .grid-wrap { padding: 0.2rem 0; }
#nav button { min-width: 0; }

#stats { display: flex; flex-wrap: wrap; align-items: center; gap: 1.6rem;
         padding: 0.2rem 0 0.9rem 0; }
#stats .figure { display: flex; flex-direction: column; line-height: 1.25; }
#stats .figure b { font-size: 1.4rem; font-weight: 600; font-variant-numeric: tabular-nums; }
#stats .figure span { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
                      color: var(--body-text-color-subdued); }
#stats .pill { padding: 0.28rem 0.7rem; border-radius: 999px; font-size: 0.78rem;
               font-weight: 500; white-space: nowrap; }
#stats .pill.ok { background: var(--color-accent-soft); color: var(--body-text-color); }
#stats .pill.bad { background: #fdecec; color: #9b1c1c; }
#stats .pill.unknown { background: var(--background-fill-secondary);
                       color: var(--body-text-color-subdued); }
footer { display: none !important; }
"""

# On screen each code sits next to the label it stands for, so the coding can be checked
# without scrolling sideways. The written files keep the SDMX column order instead.
DISPLAY_COLUMNS = [
    "REF_AREA",
    "REF_AREA_LABEL",
    "INDICATOR",
    "INDICATOR_LABEL",
    "COMPOSITE_BREAKDOWN",
    "COMPOSITE_BREAKDOWN_LABEL",
    "TIME_PERIOD",
    "TIME_PERIOD_LABEL",
    "OBS_VALUE",
    "UNIT_MEASURE",
    "UNIT_MULT",
    "FREQ",
    "OBS_STATUS",
    "PAGE",
    "TABLE",
    "EXTRACTION_METHOD",
    "SOURCE",
]

INTRO = "Tables printed in INS Niger PDF reports, read, checked and written as SDMX."
PLACEHOLDER = "Drop a PDF on the left, browse it with the page slider, then press Start."
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
    last = found[-1]
    yield _state(last["preview"], f"Done in {elapsed}, {n_pages} pages read", found, last["result"], archive, files)


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
    long = pipeline.combine(results) if results else pd.DataFrame()
    return (
        gr.update(value=preview, visible=True) if preview is not None else gr.update(),
        progress,
        gr.update(visible=not results),
        gr.update(value=stats_html(results, files), visible=bool(results)),
        gr.update(value=archive, visible=archive is not None),
        gr.update(visible=bool(found)),
        gr.update(value=[_gallery_item(f) for f in found], visible=bool(found)),
        found,
        *table_slots(current),
        observations_note(long),
        observations_frame(long),
        _preview_text(files, "_sdmx.csv"),
        _preview_text(files, "_data.xml"),
        checks_frame(results),
    )


def stats_html(results: list[PageResult], files: dict[str, str | bytes] | None = None) -> str:
    """The five figures that say where the run stands, and the conformance badge."""
    if not results:
        return ""
    long = pipeline.combine(results)
    checked = _cells_checked(results)
    figures = [
        (_number(sum(len(r.tables) for r in results)), "tables"),
        (_number(len(results)), "pages"),
        (_number(len(long)), "observations"),
        (f"{checked / len(long):.0%}", "covered by a check"),
        (f"{_measure_named(long):.0%}", "measure identified"),
        (_number(int((long["OBS_STATUS"] != "A").sum())), "to review"),
    ]
    blocks = [f"<div class='figure'><b>{value}</b><span>{label}</span></div>" for value, label in figures]
    return f"<div id='stats'>{''.join(blocks)}{conformance_pill(files or {})}</div>"


def observations_frame(long: pd.DataFrame) -> pd.DataFrame:
    """The observations as a reader wants them: every code beside its printed label."""
    if long.empty:
        return long
    ordered = [c for c in DISPLAY_COLUMNS if c in long.columns]
    rest = [c for c in long.columns if c not in ordered]
    return long[ordered + rest]


def observations_note(long: pd.DataFrame) -> str:
    """One line saying what a row is. Without it the table reads as a debug dump."""
    if long.empty:
        return ""
    return (
        f"**{_number(len(long))} observations.** One row per number read: the SDMX code and "
        "the label printed in the report side by side, then the page and the stage it came from."
    )


def _measure_named(long: pd.DataFrame) -> float:
    """Share of observations whose indicator came from the mapping rather than nothing.

    The rest carry _Z. Published because a code list that looks full is worth less than one
    that says where it stops: an accuracy that ignores what was never linked is inflated.
    """
    if long.empty:
        return 0.0
    return float((long["INDICATOR"] != NOT_IDENTIFIED).mean())


def _number(value: int) -> str:
    """Grouped with a thin space, so 6106 reads as 6 106 at a glance."""
    return f"{value:,}".replace(",", THIN_SPACE)


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


def conformance_pill(files: dict[str, str | bytes]) -> str:
    """Whether the SDMX-ML output passes the official schemas. Empty until the run ends."""
    messages = [content for name, content in files.items() if name.endswith(".xml")]
    if not messages:
        return ""
    as_bytes = [m if isinstance(m, bytes) else m.encode() for m in messages]
    result = sdmx_out.validate(*as_bytes)
    if result is None:
        return "<span class='pill unknown'>SDMX-ML 2.1 not checked</span>"
    if result:
        return "<span class='pill ok'>SDMX-ML 2.1 valid</span>"
    return "<span class='pill bad'>SDMX-ML 2.1 invalid</span>"


def stages_markdown() -> str:
    """What this machine can run. The vision stage is optional and the reader should know."""
    vision = "installed" if paddleocr_stage.available() else "not installed here"
    return (
        "pdfplumber reads the text layer and Camelot finds the borders it misses. "
        "The vision model PaddleOCR-VL runs only on a table the earlier stages leave "
        f"unreadable, or whose header or arithmetic fails, and it is {vision}."
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
    """One dataframe per table on the current page, under the title the report gives it.

    Slots are hidden while a page is being read so that Gradio remounts them with the new
    table height; an updated dataframe otherwise keeps the previous table's scroll area.
    """
    if current is None or not current.tables:
        return [gr.update(visible=False) for _ in range(TABLE_SLOTS)]
    slots = []
    for i in range(TABLE_SLOTS):
        if i < len(current.tables):
            table = current.tables[i]
            title = table.title or f"Page {current.page}, table {i + 1} of {len(current.tables)}"
            slots.append(gr.update(value=table.frame, label=f"{title} — read by {table.resolved_by}", visible=True))
        else:
            slots.append(gr.update(visible=False))
    return slots


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
        return gr.update(visible=False), gr.update(visible=False), ""
    path = Path(file)
    n_pages = pipeline.page_count(path)
    return (
        gr.update(value=pipeline.render_page(path, 1), visible=True),
        gr.update(minimum=1, maximum=max(n_pages, 2), value=1, visible=True),
        f"{html.escape(path.name)}, {n_pages} pages",
    )


def show_page_number(file, number):
    """Slider move: render that page. The run and the gallery set the image directly."""
    if not file:
        return gr.update(visible=False)
    return gr.update(value=pipeline.render_page(Path(file), int(number)), visible=True)


def previous_page(file, number):
    return _step(file, number, -1)


def next_page(file, number):
    return _step(file, number, 1)


def _step(file, number, direction: int):
    """One page forward or back, staying inside the document."""
    if not file:
        return gr.update(), gr.update()
    path = Path(file)
    target = min(max(int(number) + direction, 1), pipeline.page_count(path))
    return gr.update(value=pipeline.render_page(path, target), visible=True), gr.update(value=target)


def load_sample() -> tuple:
    preview, slider, caption = open_document(str(SAMPLE))
    return str(SAMPLE), preview, slider, caption


def theme() -> gr.themes.Base:
    return gr.themes.Base(
        primary_hue=ACCENT,
        neutral_hue="slate",
        font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
    ).set(button_primary_background_fill="*primary_600", button_primary_background_fill_hover="*primary_700")


def build() -> gr.Blocks:
    """Theme and CSS are applied by the caller: launch(theme=, css=) or mount_gradio_app(theme=, css=)."""
    with gr.Blocks(title="pdf2sdmx") as demo:
        gr.Markdown(f"# pdf2sdmx\n\n{INTRO}", elem_id="header")
        with gr.Row():
            with gr.Column(scale=1):
                file = gr.File(label="Drop a PDF here", file_types=[".pdf"], type="filepath", height=110)
                with gr.Row():
                    start = gr.Button("Start", variant="primary")
                    stop = gr.Button("Stop")
                sample = gr.Button("Load the sample report, four pages", size="sm")
                progress = gr.Markdown(elem_id="progress")
                preview = gr.Image(show_label=False, interactive=False, height=560, container=False, visible=False)
                with gr.Row(elem_id="nav"):
                    back = gr.Button("‹", size="sm", scale=0, min_width=44)
                    # The range is a placeholder: open_document sets it to the document length.
                    page = gr.Slider(minimum=1, maximum=2, value=1, step=1, label="Page", visible=False, scale=6)
                    forward = gr.Button("›", size="sm", scale=0, min_width=44)
            with gr.Column(scale=2):
                hint = gr.Markdown(PLACEHOLDER, elem_id="hint")
                stats = gr.HTML(visible=False)
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
                    with gr.Tab("Observations"):
                        observations_note_box = gr.Markdown(elem_id="formats")
                        long = gr.Dataframe(interactive=False, wrap=True, max_height=680)
                    with gr.Tab("SDMX"):
                        gr.Markdown(FORMATS, elem_id="formats")
                        sdmx_csv = gr.Code(label="SDMX-CSV 2.0", language=None, interactive=False, max_lines=18)
                        sdmx_xml = gr.Code(
                            label="SDMX-ML 2.1 data message", language=None, interactive=False, max_lines=18
                        )
                with gr.Accordion("Cells to review, and how the pages were read", open=False):
                    checks = gr.Dataframe(interactive=False, wrap=True)
                    gr.Markdown(stages_markdown(), elem_id="formats")

        outputs = [
            preview,
            progress,
            hint,
            stats,
            archive,
            gallery_title,
            gallery,
            found,
            *tables,
            observations_note_box,
            long,
            sdmx_csv,
            sdmx_xml,
            checks,
        ]
        # The page loop reports its own progress; Gradio's elapsed-time overlay would only add noise.
        run_event = start.click(process, file, outputs, show_progress="hidden")
        stop.click(None, cancels=[run_event])
        gallery.select(show_page, found, [preview, page, progress, *tables], show_progress="hidden")
        sample.click(load_sample, outputs=[file, preview, page, progress], show_progress="hidden")
        file.upload(open_document, file, [preview, page, progress], show_progress="hidden")
        page.release(show_page_number, [file, page], preview, show_progress="hidden")
        back.click(previous_page, [file, page], [preview, page], show_progress="hidden")
        forward.click(next_page, [file, page], [preview, page], show_progress="hidden")
    return demo


if __name__ == "__main__":
    build().launch(theme=theme(), css=CSS)
