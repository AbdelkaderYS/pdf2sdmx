"""Gradio front end for the Hugging Face Space.

Two panels, like an OCR demo: drop a PDF on the left and watch the current page, read the
result on the right as pages are processed. Details stay folded until asked for.
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
STATUS_MARK = {"pass": "ok", "fail": "FAIL", "warn": "warn", "skip": "skip"}

CSS = """
.gradio-container { max-width: 1280px !important; }
#header { display: flex; align-items: baseline; gap: 1rem; }
#header h1 { margin: 0; font-size: 1.4rem; }
#header p { margin: 0; color: var(--body-text-color-subdued); }
#status { min-height: 4.5rem; padding: 0.4rem 0; }
#status p { margin: 0.15rem 0; }
#progress { color: var(--body-text-color-subdued); font-size: 0.9rem; }
#hint { color: var(--body-text-color-subdued); padding: 0.4rem 0 0.8rem 0; }
#pages .grid-wrap { padding: 0.2rem 0; }
footer { display: none !important; }
"""

INTRO = (
    "Drop an INS Niger PDF report. Every page is read, each table is checked for arithmetic "
    "consistency, and the result comes out as SDMX. Each number keeps the name of the stage that read it."
)
PLACEHOLDER = (
    "Drop a PDF on the left and press Start. Each page with a table shows up here as a "
    "thumbnail; click one to see its tables. The CSV and SDMX files build up as pages are read."
)


def process(file, pages_text, time_period, unit, subject):
    """Generator: yields UI updates as pages are processed."""
    if file is None:
        raise gr.Error("Drop a PDF first")
    pdf_path = Path(file)
    n_pages = pipeline.page_count(pdf_path)
    pages = pipeline.parse_pages(pages_text or "", n_pages) or list(range(1, n_pages + 1))
    options = {"time_period": time_period, "unit": unit, "subject": subject}

    started = time.perf_counter()
    found: list[dict] = []  # one entry per page with a table: result and its rendered preview
    preview = None
    for index, page in enumerate(pages, 1):
        preview = pipeline.render_page(pdf_path, page)
        yield _state(preview, f"Reading page {page} of {n_pages} ({index}/{len(pages)})...", found, None)
        result = pipeline.run_page(pdf_path, page, **options)
        if not result.long.empty:
            found.append({"result": result, "preview": preview})
            note = f"{len(result.tables)} table{'s' if len(result.tables) > 1 else ''} found"
        else:
            note = "no table"
        yield _state(preview, f"Page {page} of {n_pages}: {note}", found, result)

    # Gradio may drop intermediate yields, so the last one carries the full final state.
    elapsed = f"{time.perf_counter() - started:.0f} s"
    if not found:
        yield _state(preview, f"Done in {elapsed}: {len(pages)} pages read, no table found", found, None)
        return
    results = [f["result"] for f in found]
    files = _output_files(pdf_path, results)
    archive = _write_archive(pdf_path, files)
    n_tables = sum(len(r.tables) for r in results)
    last = found[-1]
    yield _state(
        last["preview"],
        f"Done in {elapsed}: {len(pages)} pages read, {n_tables} tables found",
        found,
        last["result"],
        archive,
        files,
    )


def show_page(found: list[dict], evt: gr.SelectData):
    """Gallery click: show that page on the left and its tables on the right."""
    entry = found[evt.index]
    result = entry["result"]
    return (
        gr.update(value=entry["preview"], visible=True),
        f"Page {result.page}, {len(result.tables)} table{'s' if len(result.tables) > 1 else ''}",
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
        attempts_frame(results),
    )


def _gallery_item(entry: dict) -> tuple:
    result = entry["result"]
    n = len(result.tables)
    return entry["preview"], f"p. {result.page}, {n} table{'s' if n > 1 else ''}"


def _preview_text(files: dict[str, str | bytes], suffix: str, max_lines: int = 80) -> str:
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
    if not results:
        return ""
    long = pipeline.combine(results)
    flagged = int((long["OBS_STATUS"] != "A").sum())
    fails = sum(r.check_counts["fail"] for r in results)
    stages = long["EXTRACTION_METHOD"].value_counts().to_dict()
    stage_text = ", ".join(f"{k} {v}" for k, v in stages.items())
    lines = [
        f"**{sum(len(r.tables) for r in results)} tables** on pages {', '.join(str(r.page) for r in results)}",
        f"**{len(long)} observations**, {flagged} flagged for review, {fails} failed checks",
        f"**Read by:** {stage_text}",
    ]
    conformance = conformance_markdown(files or {})
    if conformance:
        lines.append(conformance)
    return "\n\n".join(lines)


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
        {
            "page": r.page,
            "status": STATUS_MARK[c.status],
            "check": c.name,
            "row": c.row,
            "column": c.column,
            "detail": c.detail,
        }
        for r in results
        for c in r.checks
        if c.status in ("fail", "warn")
    ]
    return pd.DataFrame(rows, columns=["page", "status", "check", "row", "column", "detail"])


def attempts_frame(results: list[PageResult]) -> pd.DataFrame:
    rows = [
        {
            "page": r.page,
            "stage": a.method,
            "gate": "accepted"
            if (a.gate and a.gate.accepted)
            else (a.gate.reason if a.gate else (a.error or "no table")[:80]),
            "failed checks": a.failed_checks,
            "seconds": round(a.seconds, 1),
        }
        for r in results
        for a in r.attempts
    ]
    return pd.DataFrame(rows, columns=["page", "stage", "gate", "failed checks", "seconds"])


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


def load_sample():
    return str(SAMPLE), gr.update(value=pipeline.render_page(SAMPLE, 1), visible=True)


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
                with gr.Accordion("Options", open=False):
                    pages_text = gr.Textbox(label="Pages", placeholder="all pages. Or 21, or 20-25")
                    time_period = gr.Textbox(label="Reference period", placeholder="auto-detected, e.g. 2024/2025")
                    unit = gr.Textbox(label="Default unit", placeholder="used when the header has none")
                    subject = gr.Textbox(label="Table subject", placeholder="e.g. Campagne agricole")
            with gr.Column(scale=2):
                hint = gr.Markdown(PLACEHOLDER, elem_id="hint")
                status = gr.Markdown(elem_id="status", visible=False)
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
                    with gr.Tab("CSV"):
                        gr.Markdown(
                            "One row per number, with region, indicator, period, unit and the stage that read it."
                        )
                        long = gr.Dataframe(interactive=False, wrap=True, max_height=700)
                    with gr.Tab("SDMX-CSV"):
                        sdmx_csv = gr.Code(language=None, interactive=False, max_lines=40)
                    with gr.Tab("SDMX-ML"):
                        sdmx_xml = gr.Code(language=None, interactive=False, max_lines=40)
                with gr.Accordion("Technical details: checks and extraction stages", open=False):
                    gr.Markdown(
                        "Failed and warned checks. A failed cell stays in the CSV "
                        "with OBS_STATUS = U (low reliability)."
                    )
                    checks = gr.Dataframe(interactive=False, wrap=True)
                    gr.Markdown("Which extraction stage ran on each page, and how many checks its tables failed.")
                    attempts = gr.Dataframe(interactive=False)

        outputs = [
            preview,
            progress,
            hint,
            status,
            archive,
            gallery_title,
            gallery,
            found,
            *tables,
            long,
            sdmx_csv,
            sdmx_xml,
            checks,
            attempts,
        ]
        # The page loop reports its own progress; Gradio's elapsed-time overlay would only add noise.
        run_event = start.click(
            process, [file, pages_text, time_period, unit, subject], outputs, show_progress="hidden"
        )
        stop.click(None, cancels=[run_event])
        gallery.select(show_page, found, [preview, progress, *tables], show_progress="hidden")
        sample.click(load_sample, outputs=[file, preview], show_progress="hidden")
        file.upload(show_first_page, file, preview, show_progress="hidden")
    return demo


def show_first_page(file):
    if not file:
        return gr.update(visible=False)
    return gr.update(value=pipeline.render_page(Path(file), 1), visible=True)


if __name__ == "__main__":
    build().launch(theme=theme(), css=CSS)
