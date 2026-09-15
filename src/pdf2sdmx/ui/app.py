"""Gradio front end for the Hugging Face Space.

Two panels, like an OCR demo: drop a PDF on the left and watch the current page, read the
result on the right as pages are processed. Details stay folded until asked for.
"""

import tempfile
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
footer { display: none !important; }
"""

INTRO = (
    "Drop an INS Niger PDF report. Every page is read, each table is checked for arithmetic "
    "consistency, and the result comes out as SDMX. Each number keeps the name of the stage that read it."
)
PLACEHOLDER = "The tables will appear here, page by page."


def process(file, pages_text, time_period, unit, subject):
    """Generator: yields UI updates as pages are processed."""
    if file is None:
        raise gr.Error("Drop a PDF first")
    pdf_path = Path(file)
    n_pages = pipeline.page_count(pdf_path)
    pages = pipeline.parse_pages(pages_text or "", n_pages) or list(range(1, n_pages + 1))
    options = {"time_period": time_period, "unit": unit, "subject": subject}

    results: list[PageResult] = []
    for index, page in enumerate(pages, 1):
        preview = pipeline.render_page(pdf_path, page)
        yield _state(preview, f"Page {page} / {n_pages}, reading", results, None)
        result = pipeline.run_page(pdf_path, page, **options)
        if not result.long.empty:
            results.append(result)
        yield _state(preview, f"Page {page} / {n_pages}, {index} of {len(pages)} done", results, result)

    if not results:
        yield _state(None, f"Done, {len(pages)} pages read, no table found", results, None)
        return
    files = _output_files(pdf_path, results)
    archive = _write_archive(pdf_path, files)
    yield _state(None, f"Done, {len(pages)} pages read", results, results[-1], archive, files)


def _state(preview, progress, results, current, archive=None, files=None):
    """Values for every output component, in the order declared in build()."""
    files = files or {}
    return (
        gr.update(value=preview, visible=True) if preview is not None else gr.update(),
        progress,
        summary_markdown(results),
        gr.update(value=archive, visible=archive is not None),
        *table_slots(current),
        pipeline.combine(results) if results else pd.DataFrame(),
        _preview_text(files, "_sdmx.csv"),
        _preview_text(files, "_data.xml"),
        checks_frame(results),
        attempts_frame(results),
    )


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


def summary_markdown(results: list[PageResult]) -> str:
    if not results:
        return PLACEHOLDER
    long = pipeline.combine(results)
    flagged = int((long["OBS_STATUS"] == "E").sum())
    fails = sum(r.check_counts["fail"] for r in results)
    stages = long["EXTRACTION_METHOD"].value_counts().to_dict()
    stage_text = ", ".join(f"{k} {v}" for k, v in stages.items())
    return "\n\n".join(
        [
            f"**{sum(len(r.tables) for r in results)} tables** on pages {', '.join(str(r.page) for r in results)}",
            f"**{len(long)} observations**, {flagged} flagged for review, {fails} failed checks",
            f"**Read by:** {stage_text}",
        ]
    )


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
                status = gr.Markdown(PLACEHOLDER, elem_id="status")
                archive = gr.File(label="Download the zip: long CSV, SDMX-CSV, SDMX-ML, cells to review", visible=False)
                with gr.Tabs():
                    with gr.Tab("Tables as printed"):
                        tables = [
                            gr.Dataframe(wrap=True, interactive=False, max_height=600, visible=False)
                            for _ in range(TABLE_SLOTS)
                        ]
                    with gr.Tab("CSV, one row per number"):
                        long = gr.Dataframe(interactive=False, wrap=True, max_height=700)
                    with gr.Tab("SDMX-CSV"):
                        sdmx_csv = gr.Code(language=None, interactive=False, max_lines=40)
                    with gr.Tab("SDMX-ML"):
                        sdmx_xml = gr.Code(language=None, interactive=False, max_lines=40)
                    with gr.Tab("Checks"):
                        gr.Markdown("Failed and warned checks. A failed cell is kept in the CSV with OBS_STATUS = E.")
                        checks = gr.Dataframe(interactive=False, wrap=True)
                    with gr.Tab("Stages"):
                        gr.Markdown("Which extraction stage ran on each page, and how many checks its tables failed.")
                        attempts = gr.Dataframe(interactive=False)

        outputs = [preview, progress, status, archive, *tables, long, sdmx_csv, sdmx_xml, checks, attempts]
        run_event = start.click(process, [file, pages_text, time_period, unit, subject], outputs)
        stop.click(None, cancels=[run_event])
        sample.click(load_sample, outputs=[file, preview])
        file.upload(show_first_page, file, preview)
    return demo


def show_first_page(file):
    if not file:
        return gr.update(visible=False)
    return gr.update(value=pipeline.render_page(Path(file), 1), visible=True)


if __name__ == "__main__":
    build().launch(theme=theme(), css=CSS)
