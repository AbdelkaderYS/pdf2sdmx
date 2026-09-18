"""Arithmetic checks on an extracted table. No number leaves without passing one."""

import re
from dataclasses import dataclass

import pandas as pd

from pdf2sdmx.core.numbers import parse_number

# The words a check looks for in a row or column name. They decide which check applies to
# which table, and nothing here assumes a particular report: a table naming none of them
# simply has those checks skipped, which the run counts and reports.
TOTAL_LABEL = re.compile(r"\b(total|ensemble|niger|national)\b", re.I)
RATE_HEADER = re.compile(r"kg/ha|%|taux|rendement|moyen|ratio|prix|indice|part\b", re.I)
AREA_HEADER = re.compile(r"superficie|surface", re.I)
YIELD_HEADER = re.compile(r"rendement", re.I)
PRODUCTION_HEADER = re.compile(r"production", re.I)
# A year, a range of years, either followed by a footnote marker such as * or (p).
YEAR_HEADER = re.compile(r"^(19|20)\d{2}(\s*[/-]\s*(19|20)?\d{2})?\s*(\*+|\(\s*[a-z]{1,4}\s*\))?$", re.I)
# The name table._default_header gives a column when no header row was found.
PLACEHOLDER_COLUMN = re.compile(r"^col(_\d+)?$")

SUM_TOLERANCE = 0.005  # half a percent, INS totals are rounded independently
PRODUCT_TOLERANCE = 0.02
ABSOLUTE_TOLERANCE = 1.0  # printed values are rounded to the unit, so 1 vs 1.4 is not an error
JUMP_FACTOR = 5.0
UPPER_BOUND = 1e9


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # "pass", "fail", "warn", "skip"
    detail: str
    row: str = ""
    column: str = ""


def row_labels(frame: pd.DataFrame) -> list[str]:
    """First column as unique labels. INS tables repeat labels, so duplicates get a suffix."""
    seen: dict[str, int] = {}
    out = []
    for raw in frame.iloc[:, 0].astype(str).str.strip():
        seen[raw] = seen.get(raw, 0) + 1
        out.append(raw if seen[raw] == 1 else f"{raw} ({seen[raw]})")
    return out


def numeric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Same shape as the input, body cells parsed to floats, NaN where missing or unreadable."""
    body = frame.iloc[:, 1:].map(lambda c: parse_number(c).value)
    body.index = row_labels(frame)
    return body.astype(float)


def run_checks(frame: pd.DataFrame) -> list[Check]:
    values = numeric_frame(frame)
    checks: list[Check] = []
    checks += check_header(frame)
    checks += check_unreadable(frame)
    checks += check_bounds(values)
    checks += check_column_totals(values)
    checks += check_row_totals(values)
    checks += check_area_yield_production(values)
    checks += check_year_jumps(values)
    return checks


def failed_cells(checks: list[Check]) -> pd.DataFrame:
    rows = [c for c in checks if c.status == "fail"]
    return pd.DataFrame([c.__dict__ for c in rows], columns=list(Check.__dataclass_fields__))


def check_header(frame: pd.DataFrame) -> list[Check]:
    """Column names still set to placeholders mean no header row was found.

    The numbers may all be right and every total may add up while the columns say nothing,
    so nothing else in this module would notice. Counting it as a failure is what sends the
    cascade to the next stage, and eventually to the vision model.
    """
    unnamed = sum(bool(PLACEHOLDER_COLUMN.match(str(c))) for c in frame.columns[1:])
    if unnamed and unnamed == frame.shape[1] - 1:
        return [Check("header", "fail", "no header row found, the columns are unnamed")]
    return [Check("header", "pass", "the table has a header row")]


def check_unreadable(frame: pd.DataFrame) -> list[Check]:
    out = []
    for row_label, row in zip(row_labels(frame), frame.iloc[:, 1:].itertuples(index=False), strict=True):
        for column, cell in zip(frame.columns[1:], row, strict=True):
            if parse_number(cell).status == "error":
                out.append(Check("unreadable", "fail", f"cannot parse '{cell}'", row_label, column))
    return out or [Check("unreadable", "pass", "every cell parsed as a number or a missing marker")]


def check_bounds(values: pd.DataFrame) -> list[Check]:
    out = []
    for column in values.columns:
        col = values[column].dropna()
        if col.empty:
            continue
        if (col < 0).any() and not RATE_HEADER.search(column):
            out.append(Check("bounds", "warn", "negative value in a count column", column=column))
        if (col.abs() > UPPER_BOUND).any():
            out.append(Check("bounds", "fail", f"value above {UPPER_BOUND:.0e}", column=column))
    return out or [Check("bounds", "pass", "all values within bounds")]


def check_column_totals(values: pd.DataFrame) -> list[Check]:
    """A row named Total or Niger must equal the sum of the other rows, column by column."""
    total_rows = [i for i in values.index if TOTAL_LABEL.search(i)]
    if not total_rows or len(values) < 3:
        return [Check("column_total", "skip", "no total row found")]
    parts = values.drop(index=total_rows)
    out = []
    for column in values.columns:
        expected = parts[column].sum(skipna=True)
        if parts[column].notna().sum() < 2 or expected == 0:
            continue
        for total_row in total_rows:
            actual = values.at[total_row, column]
            if pd.isna(actual):
                continue
            out.append(_compare_sum("column_total", actual, expected, total_row, column))
    return out or [Check("column_total", "skip", "total row has no numeric cells")]


def check_row_totals(values: pd.DataFrame) -> list[Check]:
    """A column named Total must equal the sum of the other columns, row by row."""
    total_cols = [c for c in values.columns if TOTAL_LABEL.search(c)]
    if not total_cols or values.shape[1] < 3:
        return [Check("row_total", "skip", "no total column found")]
    parts = values.drop(columns=total_cols)
    out = []
    for total_col in total_cols:
        for row_label, row in parts.iterrows():
            actual = values.at[row_label, total_col]
            if pd.isna(actual) or row.notna().sum() < 2:
                continue
            out.append(_compare_sum("row_total", actual, row.sum(), row_label, total_col))
    return out or [Check("row_total", "skip", "total column has no numeric cells")]


def check_area_yield_production(values: pd.DataFrame) -> list[Check]:
    """Production (t) must equal area (ha) times yield (kg/ha) / 1000. Catches unit slips.

    Works whether the three sit in columns, one item per row, or in rows nested under each
    item. The check is skipped unless all three are named in the same table.
    """
    triple = _find_triple(values.columns)
    if triple:
        area, yield_, prod = triple
        checks = [
            _product_check(values.at[r, area], values.at[r, yield_], values.at[r, prod], r, prod) for r in values.index
        ]
        return [c for c in checks if c is not None]
    out = []
    for rows in _group_by_outer_label(values.index).values():
        triple = _find_triple(rows)
        if not triple:
            continue
        area, yield_, prod = triple
        out += [
            _product_check(values.at[area, c], values.at[yield_, c], values.at[prod, c], prod, c)
            for c in values.columns
        ]
    return [c for c in out if c is not None]


def _product_check(a: float, y: float, p: float, row: str, column: str) -> Check | None:
    if pd.isna(a) or pd.isna(y) or pd.isna(p) or p == 0:
        return None
    expected = a * y / 1000
    gap = abs(p - expected) / abs(p)
    status = "pass" if gap <= PRODUCT_TOLERANCE or abs(p - expected) <= ABSOLUTE_TOLERANCE else "fail"
    detail = f"production {p:,.0f} vs area x yield {expected:,.0f} ({gap:.1%})"
    return Check("area_x_yield", status, detail, row, column)


def _find_triple(labels) -> tuple[str, str, str] | None:
    area = _first_matching(labels, AREA_HEADER)
    yield_ = _first_matching(labels, YIELD_HEADER)
    prod = _first_matching(labels, PRODUCTION_HEADER)
    return (area, yield_, prod) if area and yield_ and prod else None


def _group_by_outer_label(labels) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for label in labels:
        outer = label.split(" / ")[0] if " / " in label else ""
        groups.setdefault(outer, []).append(label)
    return groups


def check_year_jumps(values: pd.DataFrame) -> list[Check]:
    year_cols = [c for c in values.columns if YEAR_HEADER.match(c.strip())]
    if len(year_cols) < 2:
        return []
    out = []
    for row_label, row in values[year_cols].iterrows():
        series = row.dropna()
        for prev, cur in zip(series.index[:-1], series.index[1:], strict=True):
            a, b = series[prev], series[cur]
            if a > 0 and b > 0 and (b / a > JUMP_FACTOR or a / b > JUMP_FACTOR):
                out.append(Check("year_jump", "warn", f"{a:,.0f} -> {b:,.0f} between {prev} and {cur}", row_label, cur))
    return out or [Check("year_jump", "pass", "no jump above a factor of 5 between years")]


def _compare_sum(name: str, actual: float, expected: float, row: str, column: str) -> Check:
    gap = abs(actual - expected) / max(abs(expected), 1)
    detail = f"total {actual:,.1f} vs sum of parts {expected:,.1f} ({gap:.1%})"
    if gap <= SUM_TOLERANCE or abs(actual - expected) <= ABSOLUTE_TOLERANCE:
        return Check(name, "pass", detail, row, column)
    if RATE_HEADER.search(column) or RATE_HEADER.search(row):
        return Check(name, "warn", detail + ", looks like a rate so the total may be an average", row, column)
    return Check(name, "fail", detail, row, column)


def _first_matching(columns, pattern: re.Pattern) -> str | None:
    return next((c for c in columns if pattern.search(c)), None)


def check_period_jumps(long: pd.DataFrame) -> list[Check]:
    """Same area and indicator across documents: flag a jump above a factor of 5 between periods.

    A single table cannot catch a value that is wrong but consistent with its own totals.
    Comparing editions can. The check cannot tell which edition is wrong, so it warns.
    """
    out = []
    keys = ["REF_AREA", "INDICATOR"]
    ordered = long[long["OBS_STATUS"] == "A"].sort_values(keys + ["TIME_PERIOD"])
    for (area, indicator), group in ordered.groupby(keys):
        rows = group[["TIME_PERIOD", "OBS_VALUE"]].to_numpy()
        for (t0, v0), (t1, v1) in zip(rows[:-1], rows[1:], strict=True):
            if v0 > 0 and v1 > 0 and (v1 / v0 > JUMP_FACTOR or v0 / v1 > JUMP_FACTOR):
                detail = f"{v0:,.0f} in {t0} vs {v1:,.0f} in {t1}"
                out.append(Check("period_jump", "warn", detail, f"{area} {indicator}", t1))
    return out
