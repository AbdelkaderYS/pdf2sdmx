"""Every setting the tool has, in one place, read from the environment at startup.

The thresholds below decide what counts as a table, what counts as a unit and what counts
as an arithmetic failure. They were measured on one publisher's reports, so reading
another's is a matter of moving them rather than editing code. `.env.example` lists them
with the reasoning; copy it to `.env` and change what you need.
"""

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

    # A page with fewer digits than this holds no table worth the model stages. Raise it and
    # small tables are dropped; the run says so page by page.
    min_digits_for_a_table: int = 100
    # What an extracted table must look like to be accepted. A register with many identifier
    # columns needs a lower numeric share than a table of regions by indicator.
    gate_min_rows: int = 2
    gate_min_columns: int = 2
    gate_min_numeric_share: float = 0.5
    gate_max_unreadable_share: float = 0.15
    # Share of a row's cells that must read as periods for the row to be a header.
    header_period_share: float = 0.5

    # How close a printed label must be to a vocabulary entry to take its code. Lower it and
    # unrelated words start sharing a code, which is silent and hard to find.
    vocabulary_match_threshold: int = 88

    # A printed total is rounded independently of its parts, so an exact match is not the
    # test. Absolute tolerance covers values printed to the unit: 1 against 1.4 is not an
    # error. A jump beyond the factor is reported, never corrected.
    sum_tolerance: float = 0.005
    product_tolerance: float = 0.02
    absolute_tolerance: float = 1.0
    jump_factor: float = 5.0
    upper_bound: float = 1e9

    request_timeout: int = 120
    max_pdf_mb: int = 50

    api_url: str = "http://localhost:8000"
    log_level: str = "INFO"


settings = Settings()
