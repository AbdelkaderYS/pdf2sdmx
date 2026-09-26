"""Every setting, read from the environment at startup. `.env.example` explains each one."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Fails fast at import if a required variable is missing."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Point these at another office and the same engine reads its reports.
    agency: str = "INS_NE"
    country: str = "NE"
    country_name: str = "Niger"

    data_raw: Path = ROOT / "data" / "raw"
    data_processed: Path = ROOT / "data" / "processed"
    # The vocabulary is an input, like the PDF, not a part of the tool.
    mapping_file: Path = ROOT / "mapping" / "labels_to_codes.csv"
    sources_file: Path = ROOT / "data" / "sources.csv"
    # Code lists from the SDMX Global Registry, kept so a run never needs the network.
    reference_dir: Path = ROOT / "data" / "reference"

    # Below this a page holds no table worth the model stages.
    min_digits_for_a_table: int = 100
    # What an extracted table must look like to be accepted.
    gate_min_rows: int = 2
    gate_min_columns: int = 2
    gate_min_numeric_share: float = 0.5
    gate_max_unreadable_share: float = 0.15
    header_period_share: float = 0.5

    # How close a label must be to a vocabulary entry to take its code, out of 100.
    vocabulary_match_threshold: int = 88

    # A printed total is rounded independently of its parts, so exact is not the test.
    sum_tolerance: float = 0.005
    product_tolerance: float = 0.02
    absolute_tolerance: float = 1.0
    jump_factor: float = 5.0
    upper_bound: float = 1e9

    fetch_sources: bool = True
    request_timeout: int = 120
    max_pdf_mb: int = 50
    max_pages: int = 0  # 0 reads every page; a small host reads this many from the page shown
    number_format: str = "fr"  # when a page gives no sign of "1 234,5" or "1,234.5"

    api_url: str = "http://localhost:8000"
    log_level: str = "INFO"


settings = Settings()
