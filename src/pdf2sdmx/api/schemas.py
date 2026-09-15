"""Request and response shapes. Keep these stable, integrators depend on them."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    data_date: str


class MetadataResponse(BaseModel):
    geography: list[str]
    period_start: int
    period_end: int
    indicators: list[str]
    last_refresh: str
    stages: list[str]


class CheckOut(BaseModel):
    name: str
    status: str
    detail: str
    row: str = ""
    column: str = ""


class AttemptOut(BaseModel):
    method: str
    tables_found: int
    accepted: bool | None
    score: float | None
    failed_checks: int | None
    reason: str
    seconds: float


class ExtractResponse(BaseModel):
    source: str
    page: int
    resolved_by: str
    accepted: bool
    time_period: str
    check_counts: dict[str, int]
    checks: list[CheckOut]
    attempts: list[AttemptOut]
    table: list[dict]
    observations: list[dict]
    sdmx_csv: str


class MetricsResponse(BaseModel):
    """Extraction accuracy against manual ground truth, next to the baseline stage alone."""

    model_score: float
    baseline_score: float
    baseline_name: str
    metric: str
    evaluated_on: str
