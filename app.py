"""Hugging Face Space entry point. Serves the Gradio UI and the FastAPI routes on one port."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import logging  # noqa: E402
import threading  # noqa: E402

import gradio as gr  # noqa: E402
import pandas as pd  # noqa: E402

from pdf2sdmx.api.main import app  # noqa: E402
from pdf2sdmx.config import settings  # noqa: E402
from pdf2sdmx.core.ingest import camelot_stage, refresh  # noqa: E402
from pdf2sdmx.ui.app import CSS, build, theme  # noqa: E402

log = logging.getLogger("pdf2sdmx")


def _warm_up() -> None:
    """Fetch the sample PDFs, install the SDMX schemas and load the Table Transformer
    models, in the background. The Space must still serve stage 1 if any of them fails."""
    sources = pd.read_csv(settings.sources_file, dtype=str).fillna("")
    try:
        refresh.fetch_missing(sources)
    except Exception as exc:
        log.warning("sample download failed: %s", exc)
    try:
        _install_sdmx_schemas()
    except Exception as exc:
        log.warning("sdmx schema install failed, conformance will read 'not checked': %s", exc)
    try:
        first = sources.iloc[0]
        log.info(camelot_stage.warm_up(settings.data_raw / first["file"], int(first["page"])))
    except Exception as exc:
        log.warning("camelot ml warm-up failed: %s", exc)


def _install_sdmx_schemas() -> None:
    """Download the official SDMX 2.1 schemas once, so the run can state its conformance.

    A Space built on the Gradio SDK never runs the Dockerfile, so this is the only place
    the schemas get installed there. Already present, it returns at once.
    """
    import sdmx

    log.info("sdmx 2.1 schemas at %s", sdmx.install_schemas(version="2.1"))


threading.Thread(target=_warm_up, daemon=True).start()
app = gr.mount_gradio_app(app, build(), path="/", theme=theme(), css=CSS)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=7860)
