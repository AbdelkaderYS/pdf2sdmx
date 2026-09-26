"""Gradio front end.

Two panels: the document on the left, what came out of it on the right. Results are read
as figures rather than sentences, and a page that yields nothing says why in the progress
line rather than in a panel nobody opens.
"""

import hashlib
import html
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path

import gradio as gr
import pandas as pd

from pdf2sdmx.config import settings
from pdf2sdmx.core import pipeline, sdmx_out, validate
from pdf2sdmx.core.ingest import cascade, paddleocr_stage
from pdf2sdmx.core.pipeline import PageResult
from pdf2sdmx.core.reshape import NOT_IDENTIFIED, TOTAL

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

# On screen a code sits next to its label; the written files keep the SDMX order.
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

INTRO = (
    "Tables printed in a statistical PDF report, read, checked and written as SDMX. "
    f"Set up for {settings.country_name} ({settings.agency}): a label outside its vocabulary is kept and coded _Z."
)
PLACEHOLDER = "Drop a PDF on the left, browse it with the page slider, then press Start."
if settings.max_pages:
    PLACEHOLDER += f" This demo reads {settings.max_pages} pages from the page shown."
FORMATS = (
    "**SDMX-CSV** is one observation per line, readable in Excel, and assumes the receiver "
    "already has the data structure. **SDMX-ML** carries the structure itself, so a registry "
    "can validate the data against it."
)
# The complete files behind the SDMX tab. Its two previews are cut, so they are no source to copy.
SDMX_FILES = (("_sdmx.csv", "SDMX-CSV"), ("_structure.xml", "SDMX-ML structure"), ("_data.xml", "SDMX-ML data"))
NO_FIGURES = "with no figures to read"
TEXT_TABLE = "with a table of text"
SCANNED = "scanned, with no text layer"
UNREADABLE = "where no table was found"
FIGURES_ONLY = "SDMX publishes figures, so a table of names or text is not converted."


def process(file, shown=1):
    """Generator: yields UI updates as pages are processed."""
    if file is None:
        raise gr.Error("Drop a PDF first")
    pdf_path = Path(file)
    n_pages = pipeline.page_count(pdf_path)
    first = int(shown or 1) if settings.max_pages else 1
    end = min(first + settings.max_pages - 1, n_pages) if settings.max_pages else n_pages
    pages = range(first, end + 1)

    started = time.perf_counter()
    found: list[dict] = []  # one entry per page with a table: result and its rendered preview
    skipped: Counter[str] = Counter()  # why the other pages gave nothing
    preview = None
    for page in pages:
        preview = pipeline.render_page(pdf_path, page)
        yield _state(preview, f"Reading page {page} of {n_pages}...", found, None)
        result = pipeline.run_page(pdf_path, page)
        if not result.long.empty:
            found.append({"result": result, "preview": preview})
            note = _table_count(len(result.tables))
        else:
            skipped[skip_kind(result)] += 1
            note = f"nothing to convert, {no_table_reason(result)}"
        yield _state(preview, f"Page {page} of {n_pages}: {note}", found, result)

    # Gradio may drop intermediate yields, so the last one carries the full final state.
    elapsed = f"{time.perf_counter() - started:.0f} s"
    if not found:
        summary = f"Done in {elapsed}: {_pages(len(pages))} read, no table of figures.{skipped_summary(skipped)}"
        yield _state(preview, summary, found, None)
        return
    results = [f["result"] for f in found]
    jumps = validate.check_period_jumps(pipeline.combine(results))
    files = _output_files(pdf_path, results, jumps)
    archive = _write_archive(pdf_path, files)
    last = found[-1]
    summary = f"Done in {elapsed}, {_pages(len(pages))} read, {len(found)} with tables.{skipped_summary(skipped)}"
    if jumps:
        factor = f"{validate.JUMP_FACTOR:g}"
        summary += f" {len(jumps)} values change by more than {factor} times between periods, see Cells to review."
    yield _state(last["preview"], summary, found, last["result"], archive, files, jumps)


def no_table_reason(result: PageResult) -> str:
    """Why this page produced nothing, in the words of the stage that gave up.

    A rejected table says more than a stage that never ran, so a gate reason wins.
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


def skip_kind(result: PageResult) -> str:
    """Which of three reasons a page gave nothing for, so a run can count them."""
    for attempt in result.attempts:
        if attempt.method == cascade.NO_TEXT_LAYER:
            return SCANNED
        if attempt.method == "text_scan":
            return NO_FIGURES
        gate = attempt.gate
        if gate is not None and gate.stats.get("numeric_share", 1) < settings.gate_min_numeric_share:
            return TEXT_TABLE
    return UNREADABLE


def skipped_summary(skipped: Counter) -> str:
    """The pages that gave nothing, by reason, and what a page of names or text means for SDMX."""
    if not skipped:
        return ""
    parts = ", ".join(f"{_pages(n)} {kind}" for kind, n in skipped.most_common())
    note = f" {FIGURES_ONLY}" if skipped[TEXT_TABLE] or skipped[NO_FIGURES] else ""
    if skipped[SCANNED] and not paddleocr_stage.available():
        note += " A scanned page needs the vision stage, which is not installed here."
    return f" {parts}.{note}"


def _pages(n: int) -> str:
    return f"{n} page{'s' if n > 1 else ''}"


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


def _state(preview, progress, found, current, archive=None, files=None, jumps=()):
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
        *_sdmx_downloads(archive, files),
        checks_frame(results, jumps),
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
    pills = conformance_pill(files or {}) + official_codes_pill(long)
    return f"<div id='stats'>{''.join(blocks)}{pills}</div>"


def observations_frame(long: pd.DataFrame) -> pd.DataFrame:
    """Every code beside the printed label it stands for."""
    if long.empty:
        return long
    ordered = [c for c in DISPLAY_COLUMNS if c in long.columns]
    rest = [c for c in long.columns if c not in ordered]
    return long[ordered + rest]


def observations_note(long: pd.DataFrame) -> str:
    """One line saying what a row is."""
    if long.empty:
        return ""
    unnamed = int((long["INDICATOR"] == NOT_IDENTIFIED).sum())
    note = (
        f"**{_number(len(long))} observations.** One row per number read: the SDMX code and "
        "the label printed in the report side by side, then the page and the stage it came from."
    )
    totals = int((long["COMPOSITE_BREAKDOWN"] == TOTAL).sum())
    if totals:
        note += (
            f" **`_T` on {_number(totals)}** is the SDMX code for a total over that column: "
            "the row is not broken down there."
        )
    if unnamed:
        note += (
            f" **`_Z` on {_number(unnamed)} of them** means the measure was not named by the "
            "vocabulary, not that the number is wrong. Add the label to "
            "`mapping/labels_to_codes.csv` and it gets a code."
        )
    return note


def _measure_named(long: pd.DataFrame) -> float:
    """Share of observations whose indicator came from the vocabulary rather than nothing.

    The rest carry _Z. A code list that looks full is worth less than one saying where it
    stops.
    """
    if long.empty:
        return 0.0
    return float((long["INDICATOR"] != NOT_IDENTIFIED).mean())


def _number(value: int) -> str:
    """Grouped with a thin space, so 6106 reads as 6 106 at a glance."""
    return f"{value:,}".replace(",", THIN_SPACE)


def _cells_checked(results: list[PageResult]) -> int:
    """Cells named by at least one arithmetic check.

    Published because an unchecked value carries OBS_STATUS A for want of a contradiction.
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


def official_codes_pill(long: pd.DataFrame) -> str:
    """Whether the codes we wrote exist in a list this repository does not maintain.

    Separate from the schema badge: that one says the file is well formed, this one says
    the values inside it were checked against someone else's list.
    """
    if long.empty:
        return ""
    outside = sdmx_out.codes_outside_official_lists(long)
    if outside is None:
        return "<span class='pill unknown'>codes not checked</span>"
    if outside:
        first = sorted(next(iter(outside.values())))[:3]
        return f"<span class='pill bad'>codes outside the official lists: {', '.join(first)}</span>"
    return "<span class='pill ok'>codes in the official lists</span>"


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
    """One dataframe per table on the current page, under the title the report gives it, and its CSV.

    Hidden while a page is read so Gradio remounts them at the new height.
    """
    hidden = [gr.update(visible=False), gr.update(visible=False)]
    if current is None or not current.tables:
        return hidden * TABLE_SLOTS
    slots = []
    for i in range(TABLE_SLOTS):
        if i < len(current.tables):
            table = current.tables[i]
            title = table.title or f"Page {current.page}, table {i + 1} of {len(current.tables)}"
            slots.append(gr.update(value=table.frame, label=f"{title} · read by {table.resolved_by}", visible=True))
            slots.append(gr.update(value=_table_csv(current, i), label="CSV of this table", visible=True))
        else:
            slots += hidden
    return slots


def _table_csv(result: PageResult, index: int) -> str:
    """The table as printed, in a file named after its page.

    The folder is named after the content, so two documents never share a file.
    """
    data = _for_excel(result.tables[index].frame)
    folder = Path(tempfile.gettempdir()) / "pdf2sdmx" / hashlib.sha1(data).hexdigest()[:12]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{Path(result.source).stem}_p{result.page}_table{index + 1}.csv"
    path.write_bytes(data)
    return str(path)


def _sdmx_downloads(archive: str | None, files: dict[str, str | bytes]) -> list:
    """One button per complete SDMX file, taken from the folder the zip was written to."""
    folder = Path(archive).parent if archive else None
    updates = []
    for suffix, _ in SDMX_FILES:
        name = next((k for k in files if k.endswith(suffix)), None)
        visible = folder is not None and name is not None
        updates.append(gr.update(value=str(folder / name), visible=True) if visible else gr.update(visible=False))
    return updates


def checks_frame(results: list[PageResult], jumps=()) -> pd.DataFrame:
    """Failed checks page by page, then the jumps between periods, which the run computes
    once at the end: across the whole document, they would cost more at every page."""
    rows = [
        {"page": r.page, "check": c.name, "row": c.row, "column": c.column, "detail": c.detail}
        for r in results
        for c in r.checks
        if c.status in ("fail", "warn")
    ]
    rows += [{"page": None, "check": c.name, "row": c.row, "column": c.column, "detail": c.detail} for c in jumps]
    return pd.DataFrame(rows, columns=["page", "check", "row", "column", "detail"])


def _output_files(pdf_path: Path, results: list[PageResult], jumps: list | None = None) -> dict[str, str | bytes]:
    """Long CSV, SDMX-CSV, SDMX-ML structure and data, cells to review, for the whole document.

    The CSVs meant for a person carry a byte order mark, without which Excel reads UTF-8 as
    Windows-1252 and prints "é" as "Ã©". SDMX-CSV is for machines and holds only codes.
    """
    long = pipeline.combine(results)
    structure_xml, data_xml = sdmx_out.to_sdmx_ml(long)
    per_page = [r.to_review.assign(page=r.page) for r in results]
    across = pd.DataFrame([c.__dict__ for c in jumps or []])
    to_review = pd.concat([*per_page, across], ignore_index=True)
    stem = pdf_path.stem
    return {
        f"{stem}_long.csv": _for_excel(long),
        f"{stem}_sdmx.csv": sdmx_out.to_sdmx_csv(long),
        f"{stem}_structure.xml": structure_xml,
        f"{stem}_data.xml": data_xml,
        f"{stem}_to_review.csv": _for_excel(to_review),
    }


def _for_excel(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8-sig")


def _write_archive(pdf_path: Path, files: dict[str, str | bytes]) -> str:
    """Every file on its own, for the download buttons, and all of them in one zip."""
    folder = Path(tempfile.mkdtemp(prefix="pdf2sdmx_"))
    out = folder / f"{pdf_path.stem}_sdmx.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            data = content if isinstance(content, bytes) else content.encode()
            (folder / name).write_bytes(data)
            zf.writestr(name, data)
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
                        tables = []  # a dataframe then its CSV button, for each slot
                        for _ in range(TABLE_SLOTS):
                            tables.append(
                                gr.Dataframe(
                                    wrap=True,
                                    interactive=False,
                                    max_height=600,
                                    visible=False,
                                    buttons=["copy", "fullscreen"],
                                )
                            )
                            tables.append(gr.DownloadButton("CSV of this table", size="sm", visible=False))
                    with gr.Tab("Observations"):
                        observations_note_box = gr.Markdown(elem_id="formats")
                        long = gr.Dataframe(interactive=False, wrap=True, max_height=680)
                    with gr.Tab("SDMX"):
                        gr.Markdown(FORMATS, elem_id="formats")
                        with gr.Row():
                            sdmx_downloads = [
                                gr.DownloadButton(label, size="sm", visible=False) for _, label in SDMX_FILES
                            ]
                        sdmx_csv = gr.Code(
                            label="SDMX-CSV 2.0, first lines",
                            language=None,
                            interactive=False,
                            max_lines=18,
                            buttons=[],
                        )
                        sdmx_xml = gr.Code(
                            label="SDMX-ML 2.1 data message, first lines",
                            language=None,
                            interactive=False,
                            max_lines=18,
                            buttons=[],
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
            *sdmx_downloads,
            checks,
        ]
        # The page loop reports its own progress; Gradio's elapsed-time overlay would only add noise.
        run_event = start.click(process, [file, page], outputs, show_progress="hidden")
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
