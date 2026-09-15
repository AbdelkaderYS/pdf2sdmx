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
        yield _state(preview, f"Page {page} / {n_pages}, reading", results, None, None)
        result = pipeline.run_page(pdf_path, page, **options)
        if not result.long.empty:
            results.append(result)
        yield _state(preview, f"Page {page} / {n_pages}, {index} of {len(pages)} done", results, result, None)

    archive = _write_archive(pdf_path, results) if results else None
    yield _state(None, f"Done, {len(pages)} pages read", results, results[-1] if results else None, archive)


def _state(preview, progress, results, current, archive):
    return (
        gr.update(value=preview, visible=True) if preview is not None else gr.update(),
        progress,
        summary_markdown(results),
        *table_slots(current),
        checks_frame(results),
        attempts_frame(results),
        pipeline.combine(results) if results else pd.DataFrame(),
        gr.update(value=archive, visible=archive is not None),
    )


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


def _write_archive(pdf_path: Path, results: list[PageResult]) -> str:
    """One zip: long CSV, SDMX-CSV, SDMX-ML structure and data, cells to review."""
    long = pipeline.combine(results)
    structure_xml, data_xml = sdmx_out.to_sdmx_ml(long)
    to_review = pd.concat([r.to_review.assign(page=r.page) for r in results], ignore_index=True)
    stem = pdf_path.stem
    files = {
        f"{stem}_long.csv": long.to_csv(index=False),
        f"{stem}_sdmx.csv": sdmx_out.to_sdmx_csv(long),
        f"{stem}_structure.xml": structure_xml,
        f"{stem}_data.xml": data_xml,
        f"{stem}_to_review.csv": to_review.to_csv(index=False),
    }
    out = Path(tempfile.mkdtemp(prefix="pdf2sdmx_")) / f"{stem}_sdmx.zip"
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
                tables = [
                    gr.Dataframe(wrap=True, interactive=False, max_height=600, visible=False)
                    for _ in range(TABLE_SLOTS)
                ]
                archive = gr.File(label="Download: long CSV, SDMX-CSV, SDMX-ML, cells to review", visible=False)
                with gr.Accordion("Details", open=False), gr.Tabs():
                    with gr.Tab("Failed and warned checks"):
                        checks = gr.Dataframe(interactive=False, wrap=True)
                    with gr.Tab("Stages per page"):
                        attempts = gr.Dataframe(interactive=False)
                    with gr.Tab("All observations"):
                        long = gr.Dataframe(interactive=False, wrap=True)

        outputs = [preview, progress, status, *tables, checks, attempts, long, archive]
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
