"""Acceptance gate for an extracted table. Decides whether the cascade moves to the next stage."""

from dataclasses import dataclass

import pandas as pd

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
    min_rows: int = 2,
    min_cols: int = 2,
    min_numeric_share: float = 0.5,
    max_unreadable_share: float = 0.15,
) -> GateResult:
    """A table passes when it is big enough and its body is mostly readable numbers."""
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
    error_share = (df["OBS_STATUS"] == "E").mean() if len(df) else 0.0
    if error_share > max_error_share:
        raise QualityCheckFailed(f"{error_share:.0%} of observations failed validation")
