"""Scheduled refresh: fetch PDFs listed in data/sources.csv, extract the listed pages,
run the quality gate, write data/processed. A cached PDF is never re-downloaded or overwritten.
"""

import logging
import sys
from datetime import UTC, datetime

import pandas as pd
import requests

from pdf2sdmx.config import settings
from pdf2sdmx.core import pipeline, validate
from pdf2sdmx.core.quality import QualityCheckFailed, check_long_dataset

log = logging.getLogger(__name__)

MIN_ROWS = 10
MAX_ERROR_SHARE = 0.2


def fetch_missing(sources: pd.DataFrame) -> None:
    settings.data_raw.mkdir(parents=True, exist_ok=True)
    for row in sources.itertuples():
        target = settings.data_raw / row.file
        if target.exists():
            continue
        log.info("downloading %s", row.url)
        response = requests.get(row.url, timeout=settings.request_timeout)
        response.raise_for_status()
        # Write to a temp name first so a broken download never leaves a half file behind.
        partial = target.with_suffix(".part")
        partial.write_bytes(response.content)
        partial.rename(target)


def extract_all(sources: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for row in sources.itertuples():
        result = pipeline.run_page(
            settings.data_raw / row.file,
            int(row.page),
            time_period=str(row.time_period),
            unit=str(row.unit),
            subject=str(row.subject),
        )
        log.info("%s p%s resolved by %s, %s observations", row.file, row.page, result.resolved_by, len(result.long))
        frames.append(result.long)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> int:
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    sources = pd.read_csv(settings.sources_file, dtype=str).fillna("")
    fetch_missing(sources)
    long = extract_all(sources)
    try:
        check_long_dataset(long, min_rows=MIN_ROWS, max_error_share=MAX_ERROR_SHARE)
    except QualityCheckFailed as exc:
        log.error("quality gate failed, keeping the last good snapshot: %s", exc)
        return 1

    settings.data_processed.mkdir(parents=True, exist_ok=True)
    long["DATA_DATE"] = datetime.now(tz=UTC).date().isoformat()
    long.to_csv(settings.data_processed / "observations.csv", index=False)
    jumps = validate.check_period_jumps(long)
    pd.DataFrame([c.__dict__ for c in jumps]).to_csv(settings.data_processed / "to_review.csv", index=False)
    log.info("wrote %s observations, %s cross-period jumps to review", len(long), len(jumps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
