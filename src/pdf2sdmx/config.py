"""Application settings, read from the environment and validated at startup."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Fails fast at import if a required variable is missing."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Who publishes and where. Nothing about one country belongs in the code: point these
    # at another office and the same engine reads its reports.
    agency: str = "INS_NE"
    country: str = "NE"
    country_name: str = "Niger"

    data_raw: Path = ROOT / "data" / "raw"
    data_processed: Path = ROOT / "data" / "processed"
    # The vocabulary is an input, like the PDF, not a part of the tool. The bundled file is
    # a starting point; point this at another one to read another domain or another country.
    mapping_file: Path = ROOT / "mapping" / "labels_to_codes.csv"
    sources_file: Path = ROOT / "data" / "sources.csv"
    # Code lists fetched from the SDMX Global Registry and kept so a run never needs the
    # network. Deleting the folder only means the next run has to fetch them again.
    reference_dir: Path = ROOT / "data" / "reference"

    request_timeout: int = 120
    max_pdf_mb: int = 50

    api_url: str = "http://localhost:8000"
    log_level: str = "INFO"


settings = Settings()
