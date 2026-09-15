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
    options = {"time_period": time_period, "unit": unit, "subject": subject or "UNKNOWN"}

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
        gr.update(value=preview) if preview is not None else gr.update(),
        progress,
        summary_markdown(results),
        current.table if current is not None and not current.table.empty else gr.update(),
        checks_frame(results),
        attempts_frame(results),
        pipeline.combine(results) if results else pd.DataFrame(),
        gr.update(value=archive, visible=archive is not None),
    )


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
            f"**{len(results)} tables** on pages {', '.join(str(r.page) for r in results)}",
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
    return str(SAMPLE), pipeline.render_page(SAMPLE, 1)


def build() -> gr.Blocks:
    theme = gr.themes.Base(
        primary_hue=ACCENT,
        neutral_hue="slate",
        font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
    ).set(button_primary_background_fill="*primary_600", button_primary_background_fill_hover="*primary_700")

    with gr.Blocks(theme=theme, css=CSS, title="pdf2sdmx") as demo:
        with gr.Row(elem_id="header"):
            gr.Markdown(f"# pdf2sdmx\n\n{INTRO}")
        with gr.Row():
            with gr.Column(scale=1):
                file = gr.File(label="Drop a PDF here", file_types=[".pdf"], type="filepath", height=110)
                preview = gr.Image(show_label=False, interactive=False, height=520, container=False)
                progress = gr.Markdown(elem_id="progress")
                with gr.Row():
                    sample = gr.Button("Load sample (INS bulletin 3T 2025, p. 20-23)", size="sm")
                    start = gr.Button("Start", variant="primary", size="sm")
                    stop = gr.Button("Stop", size="sm")
                with gr.Accordion("Options", open=False):
                    pages_text = gr.Textbox(label="Pages", placeholder="all pages. Or 21, or 20-25")
                    time_period = gr.Textbox(label="Reference period", placeholder="auto-detected, e.g. 2024/2025")
                    unit = gr.Textbox(label="Default unit", placeholder="used when the header has none")
                    subject = gr.Textbox(label="Table subject", placeholder="e.g. Campagne agricole")
            with gr.Column(scale=2):
                status = gr.Markdown(PLACEHOLDER, elem_id="status")
                table = gr.Dataframe(label="Current table, as printed", wrap=True, interactive=False, max_height=420)
                archive = gr.File(label="Download: long CSV, SDMX-CSV, SDMX-ML, cells to review", visible=False)
                with gr.Accordion("Details", open=False), gr.Tabs():
                    with gr.Tab("Failed and warned checks"):
                        checks = gr.Dataframe(interactive=False, wrap=True)
                    with gr.Tab("Stages per page"):
                        attempts = gr.Dataframe(interactive=False)
                    with gr.Tab("All observations"):
                        long = gr.Dataframe(interactive=False, wrap=True)

        outputs = [preview, progress, status, table, checks, attempts, long, archive]
        run_event = start.click(process, [file, pages_text, time_period, unit, subject], outputs)
        stop.click(None, cancels=[run_event])
        sample.click(load_sample, outputs=[file, preview])
        file.upload(lambda f: pipeline.render_page(Path(f), 1) if f else None, file, preview)
    return demo


if __name__ == "__main__":
    build().launch()
