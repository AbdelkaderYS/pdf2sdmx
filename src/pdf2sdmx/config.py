"""Application settings, read from the environment and validated at startup."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Fails fast at import if a required variable is missing."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Who publishes, where, and in which vocabulary. Nothing about one country belongs in
    # the code: point these at another office and the same engine reads its reports.
    agency: str = "INS_NE"
    agency_name: str = "Institut National de la Statistique du Niger"
    country: str = "NE"
    country_name: str = "Niger"

    data_raw: Path = ROOT / "data" / "raw"
    data_processed: Path = ROOT / "data" / "processed"
    # The vocabulary is an input, like the PDF, not a part of the tool. The bundled file is
    # a starting point; point this at another one to read another domain or another country.
    mapping_file: Path = ROOT / "mapping" / "labels_to_codes.csv"
    sources_file: Path = ROOT / "data" / "sources.csv"

    request_timeout: int = 120
    max_pdf_mb: int = 50

    api_url: str = "http://localhost:8000"
    log_level: str = "INFO"


settings = Settings()
