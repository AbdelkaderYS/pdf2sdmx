"""Gradio front end for the Hugging Face Space.

Shows three things the usual PDF-to-table demos do not: which stage resolved the table,
what the arithmetic checks found, and the SDMX files that come out.
"""

import tempfile
from pathlib import Path

import gradio as gr
import pandas as pd

from pdf2sdmx.config import settings
from pdf2sdmx.core import pipeline
from pdf2sdmx.core.pipeline import PageResult

ACCENT = "teal"
STATUS_MARK = {"pass": "ok", "fail": "FAIL", "warn": "warn", "skip": "skip"}

CSS = """
.gradio-container { max-width: 1180px !important; }
#summary { padding: 0.6rem 0 0.2rem 0; }
#summary p { margin: 0.15rem 0; }
footer { display: none !important; }
"""

INTRO = """
# pdf2sdmx

Upload a statistical report from INS Niger, pick a page, and get the table as validated
observations in SDMX-CSV and SDMX-ML. Three extraction stages run in order: **pdfplumber**
(text layer), **Camelot ml** (Table Transformer for structure, text from the PDF), then
**Docling** (vision, flagged for review). The stage with the fewest failed checks wins and
its broken rows are repaired from the others when that lowers the failure count.

Numbers only reach the output after arithmetic checks: totals against parts,
area x yield against production, bounds, and year-to-year jumps. Each observation
records the stage it came from.
"""

SAMPLES_DIR = settings.data_raw


def process(file, page, time_period, unit, subject):
    if file is None:
        raise gr.Error("Upload a PDF first")
    pdf_path = Path(file)
    n_pages = pipeline.page_count(pdf_path)
    page = int(page)
    if not 1 <= page <= n_pages:
        raise gr.Error(f"Page must be between 1 and {n_pages}")

    result = pipeline.run_page(pdf_path, page, time_period=time_period, unit=unit, subject=subject or "UNKNOWN")
    return (
        summary_markdown(result),
        result.table,
        checks_frame(result),
        result.long,
        attempts_frame(result),
        write_downloads(result),
    )


def summary_markdown(result: PageResult) -> str:
    counts = result.check_counts
    if result.table.empty:
        return "**No table found on this page.** The cascade tried every stage. Manual entry is the remaining route."
    trust = (
        "accepted by the quality gate" if result.accepted else "**did not pass the gate, shown for manual review only**"
    )
    lines = [
        f"**Resolved by:** {result.resolved_by} ({trust})",
        f"**Checks:** {counts['pass']} passed, {counts['fail']} failed, "
        f"{counts['warn']} warnings, {counts['skip']} skipped",
        f"**Observations:** {len(result.long)}, period {result.time_period}, "
        f"{(result.long['OBS_STATUS'] == 'E').sum() if len(result.long) else 0} flagged for review",
    ]
    return "\n\n".join(lines)


def checks_frame(result: PageResult) -> pd.DataFrame:
    rows = [
        {"status": STATUS_MARK[c.status], "check": c.name, "row": c.row, "column": c.column, "detail": c.detail}
        for c in sorted(result.checks, key=lambda c: ("fail", "warn", "pass", "skip").index(c.status))
    ]
    return pd.DataFrame(rows, columns=["status", "check", "row", "column", "detail"])


def attempts_frame(result: PageResult) -> pd.DataFrame:
    rows = [
        {
            "stage": a.method,
            "tables found": a.tables_found,
            "gate": "accepted"
            if (a.gate and a.gate.accepted)
            else (a.gate.reason if a.gate else (a.error or "no table")[:80]),
            "score": a.gate.score if a.gate else None,
            "failed checks": a.failed_checks,
            "seconds": round(a.seconds, 2),
        }
        for a in result.attempts
    ]
    return pd.DataFrame(rows, columns=["stage", "tables found", "gate", "score", "failed checks", "seconds"])


def write_downloads(result: PageResult) -> list[str]:
    if result.long.empty:
        return []
    out_dir = Path(tempfile.mkdtemp(prefix="pdf2sdmx_"))
    stem = f"{Path(result.source).stem}_p{result.page}"
    files = {
        f"{stem}_long.csv": result.long.to_csv(index=False),
        f"{stem}_sdmx.csv": result.sdmx_csv,
        f"{stem}_structure.xml": result.sdmx_structure_xml,
        f"{stem}_data.xml": result.sdmx_data_xml,
        f"{stem}_to_review.csv": result.to_review.to_csv(index=False),
    }
    paths = []
    for name, content in files.items():
        path = out_dir / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        paths.append(str(path))
    return paths


def sample_examples() -> list[list]:
    samples = {
        "bulletin_3T25.pdf": [21, "2024/2025", "", "Campagne agricole"],
        "bulletin_1T24.pdf": [24, "2023/2024", "", "Campagne agricole"],
    }
    return [[str(SAMPLES_DIR / name), *args] for name, args in samples.items() if (SAMPLES_DIR / name).exists()]


def build() -> gr.Blocks:
    theme = gr.themes.Base(
        primary_hue=ACCENT,
        neutral_hue="slate",
        font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
    ).set(button_primary_background_fill="*primary_600", button_primary_background_fill_hover="*primary_700")

    with gr.Blocks(theme=theme, css=CSS, title="pdf2sdmx") as demo:
        gr.Markdown(INTRO)
        with gr.Row():
            with gr.Column(scale=1):
                file = gr.File(label="PDF report", file_types=[".pdf"], type="filepath")
                page = gr.Number(label="Page", value=1, precision=0, minimum=1)
                time_period = gr.Textbox(label="Reference period", placeholder="auto-detected, e.g. 2024/2025")
                unit = gr.Textbox(label="Default unit", placeholder="used when the header has none")
                subject = gr.Textbox(label="Table subject", placeholder="e.g. Campagne agricole")
                run = gr.Button("Extract and validate", variant="primary")
                gr.Markdown("Samples: INS Niger quarterly bulletins, 1T 2024 and 3T 2025, table 03.01.")
            with gr.Column(scale=2):
                summary = gr.Markdown(elem_id="summary")
                table = gr.Dataframe(label="Extracted table, as printed", wrap=True, interactive=False)

        with gr.Tabs():
            with gr.Tab("Checks"):
                checks = gr.Dataframe(interactive=False, wrap=True)
            with gr.Tab("Observations (long format)"):
                long = gr.Dataframe(interactive=False, wrap=True)
            with gr.Tab("Cascade trace"):
                attempts = gr.Dataframe(interactive=False)
            with gr.Tab("Downloads"):
                downloads = gr.Files(label="long CSV, SDMX-CSV, SDMX-ML structure and data, cells to review")

        examples = sample_examples()
        if examples:
            gr.Examples(examples=examples, inputs=[file, page, time_period, unit, subject], label="Try a sample")

        run.click(
            process, [file, page, time_period, unit, subject], [summary, table, checks, long, attempts, downloads]
        )
    return demo


if __name__ == "__main__":
    build().launch()
