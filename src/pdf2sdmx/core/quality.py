"""Acceptance gate for an extracted table. Decides whether the cascade moves to the next stage."""

from dataclasses import dataclass

import pandas as pd

from pdf2sdmx.config import settings
from pdf2sdmx.core.table import body_stats


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    score: float
    reason: str
    stats: dict


def gate_table(
    frame: pd.DataFrame,
    *,
    min_rows: int | None = None,
    min_cols: int | None = None,
    min_numeric_share: float | None = None,
    max_unreadable_share: float | None = None,
) -> GateResult:
    """A table passes when it is big enough and its body is mostly readable numbers.

    The thresholds come from the settings unless a caller overrides them.
    """
    min_rows = settings.gate_min_rows if min_rows is None else min_rows
    min_cols = settings.gate_min_columns if min_cols is None else min_cols
    if min_numeric_share is None:
        min_numeric_share = settings.gate_min_numeric_share
    if max_unreadable_share is None:
        max_unreadable_share = settings.gate_max_unreadable_share
    stats = body_stats(frame)
    score = round(stats["numeric_share"] * (1 - stats["unreadable_share"]), 3)

    if stats["rows"] < min_rows or stats["cols"] < min_cols:
        return GateResult(False, score, f"too small ({stats['rows']}x{stats['cols']})", stats)
    if stats["numeric_share"] < min_numeric_share:
        return GateResult(False, score, f"numeric share {stats['numeric_share']:.0%}", stats)
    if stats["unreadable_share"] > max_unreadable_share:
        return GateResult(False, score, f"unreadable share {stats['unreadable_share']:.0%}", stats)
    return GateResult(True, score, "ok", stats)


class QualityCheckFailed(Exception):
    """Raised by the scheduled refresh when a processed dataset must not be published."""


def check_long_dataset(df: pd.DataFrame, *, min_rows: int, max_error_share: float) -> None:
    """Gate for the long-format output: enough rows and few validation failures."""
    if len(df) < min_rows:
        raise QualityCheckFailed(f"Expected at least {min_rows} rows, got {len(df)}")
    error_share = (df["OBS_STATUS"] != "A").mean() if len(df) else 0.0
    if error_share > max_error_share:
        raise QualityCheckFailed(f"{error_share:.0%} of observations failed validation")
