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
    """Fetch what the app needs, in the background.

    Every step is optional: the service must answer with stage one even if all of them
    fail. An image that baked them in at build time finds the work already done.
    """
    if settings.fetch_sources:
        try:
            refresh.fetch_missing(pd.read_csv(settings.sources_file, dtype=str).fillna(""))
        except Exception as exc:
            log.warning("source list unreadable or download failed: %s", exc)
    try:
        _install_sdmx_schemas()
    except Exception as exc:
        log.warning("sdmx schema install failed, conformance will read 'not checked': %s", exc)
    try:
        from pdf2sdmx.core import registry

        log.info("official code lists: %s", registry.download_all())
    except Exception as exc:
        log.warning("code list download failed, codes will read 'not checked': %s", exc)
    try:
        sample = settings.data_raw.parent / "samples" / "ins_bulletin_3T25_p20-23.pdf"
        log.info(camelot_stage.warm_up(sample, 2))
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
    import os

    import uvicorn

    # A Space serves on 7860; a host that assigns a port passes it in the environment.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 7860)))
