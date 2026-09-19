"""HTTP routes. Health, metadata, the extraction itself, and the accuracy metrics."""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from pdf2sdmx.api.schemas import (
    AttemptOut,
    CheckOut,
    ExtractResponse,
    HealthResponse,
    MetadataResponse,
    MetricsResponse,
)
from pdf2sdmx.config import settings
from pdf2sdmx.core import pipeline
from pdf2sdmx.core.ingest.cascade import STAGES

router = APIRouter()

VERSION = "0.1.0"
METRICS_FILE = settings.data_processed / "metrics.json"


def _data_date() -> str:
    """Date of the newest processed file, or the metrics build date, or "none"."""
    candidates = list(settings.data_processed.glob("*.csv"))
    if not candidates:
        return "none"
    newest = max(c.stat().st_mtime for c in candidates)
    return datetime.fromtimestamp(newest, tz=UTC).date().isoformat()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=VERSION, data_date=_data_date())


@router.get("/metadata", response_model=MetadataResponse)
def metadata() -> MetadataResponse:
    return MetadataResponse(
        geography=[settings.country],
        period_start=2011,
        period_end=2025,
        indicators=["AREA_HA", "YIELD_KG_HA", "PROD_T"],
        last_refresh=_data_date(),
        stages=[name for name, _ in STAGES],
    )


@router.post("/extract", response_model=ExtractResponse)
async def extract(
    file: Annotated[UploadFile, File()],
    page: Annotated[int, Form()] = 1,
    time_period: Annotated[str, Form()] = "",
    unit: Annotated[str, Form()] = "",
    subject: Annotated[str, Form()] = "UNKNOWN",
) -> ExtractResponse:
    """Run the cascade on one page of the uploaded PDF and return table, checks and SDMX-CSV."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Upload a PDF file")
    content = await file.read()
    if len(content) > settings.max_pdf_mb * 1024 * 1024:
        raise HTTPException(413, f"PDF larger than {settings.max_pdf_mb} MB")

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / Path(file.filename).name
        pdf_path.write_bytes(content)
        if page < 1 or page > pipeline.page_count(pdf_path):
            raise HTTPException(400, "Page out of range")
        result = pipeline.run_page(pdf_path, page, time_period=time_period, unit=unit, subject=subject)

    return ExtractResponse(
        source=result.source,
        page=result.page,
        resolved_by=result.resolved_by,
        accepted=result.accepted,
        time_period=result.time_period,
        check_counts=result.check_counts,
        checks=[CheckOut(**c.__dict__) for c in result.checks],
        attempts=[
            AttemptOut(
                method=a.method,
                tables_found=a.tables_found,
                accepted=a.gate.accepted if a.gate else None,
                score=a.gate.score if a.gate else None,
                failed_checks=a.failed_checks,
                reason=a.error or (a.gate.reason if a.gate else "no table"),
                seconds=round(a.seconds, 2),
            )
            for a in result.attempts
        ],
        table=result.table.to_dict(orient="records"),
        observations=result.long.to_dict(orient="records"),
        sdmx_csv=result.sdmx_csv,
    )


@router.get("/metrics", response_model=MetricsResponse)
def metrics() -> MetricsResponse:
    """Cascade accuracy against manual ground truth, next to pdfplumber alone as the baseline."""
    if not METRICS_FILE.exists():
        raise HTTPException(404, "No metrics yet. Run `make evaluate` against the truth files.")
    return MetricsResponse(**json.loads(METRICS_FILE.read_text()))
