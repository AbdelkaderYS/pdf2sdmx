"""Turn a wide printed table into one observation per row, with SDMX style columns."""

import re
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process
from rapidfuzz.utils import default_process

from pdf2sdmx.core.numbers import parse_number
from pdf2sdmx.core.table import LABEL_JOIN
from pdf2sdmx.core.validate import YEAR_HEADER, Check, row_labels

# Column ids follow the SDMX cross-domain concepts (FREQ, REF_AREA, TIME_PERIOD, OBS_VALUE,
# UNIT_MEASURE, UNIT_MULT, OBS_STATUS), the same ids the World Bank WDI DSD uses.
LONG_COLUMNS = [
    "FREQ",
    "REF_AREA",
    "INDICATOR",
    "TIME_PERIOD",
    "OBS_VALUE",
    "UNIT_MEASURE",
    "UNIT_MULT",
    "OBS_STATUS",
    "TIME_PERIOD_LABEL",
    "EXTRACTION_METHOD",
    "SOURCE",
    "REF_AREA_LABEL",
    "INDICATOR_LABEL",
]
MATCH_THRESHOLD = 88
UNIT_IN_HEADER = re.compile(r"\(([^)]+)\)\s*$")
DUPLICATE_SUFFIX = re.compile(r"\s\(\d+\)$")
SPLIT_YEAR = re.compile(r"^((?:19|20)\d{2})\s*[/-]\s*(?:19|20)?\d{2}$")
# Characters an SDMX code id may not contain. The standard allows A-Z a-z 0-9 and _ @ $ -
NOT_IN_A_CODE = re.compile(r"[^A-Za-z0-9_@$-]+")
PLAIN_YEAR = re.compile(r"^(?:19|20)\d{2}$")

# CL_OBS_STATUS 2.3: A normal value, U low reliability. E means estimated and is not used here.
STATUS_OK = "A"
STATUS_FAILED_CHECK = "U"


def load_mapping(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str).fillna("")


def to_long(
    frame: pd.DataFrame,
    *,
    mapping: pd.DataFrame,
    time_period: str,
    unit: str,
    method: str | dict[str, str],
    source: str,
    checks: list[Check],
    subject: str = "UNKNOWN",
) -> pd.DataFrame:
    """Wide table -> long observations.

    `method` is one stage name, or a row label to stage name mapping when rows were
    repaired from different stages. Each axis is classified as regions, years or other. Regions become REF_AREA, years
    become TIME_PERIOD, and whatever is left becomes INDICATOR. When neither axis holds
    an indicator, the table subject is used.
    """
    labels = row_labels(frame)
    columns = list(frame.columns[1:])
    row_kind = _axis_kind(labels, mapping)
    col_kind = _axis_kind(columns, mapping)
    failed = _failed_cells(checks)

    records = []
    for label, row in zip(labels, frame.iloc[:, 1:].itertuples(index=False), strict=True):
        for column, cell in zip(columns, row, strict=True):
            parsed = parse_number(cell)
            if parsed.status == "missing":
                continue
            area_label, indicator_label, period = _assign_axes(label, column, row_kind, col_kind, subject, time_period)
            indicator_code, mapped_unit = _indicator(indicator_label, mapping)
            records.append(
                {
                    "FREQ": "A",
                    "REF_AREA": _code_for(area_label, mapping, "REF_AREA")[0] if area_label else "NE",
                    "INDICATOR": indicator_code,
                    "TIME_PERIOD": sdmx_time_period(period),
                    "OBS_VALUE": parsed.value,
                    "UNIT_MEASURE": _unit_for(indicator_label, mapped_unit, unit),
                    "UNIT_MULT": "0",
                    "OBS_STATUS": _status(parsed.status, (label, column) in failed),
                    "TIME_PERIOD_LABEL": period,
                    "EXTRACTION_METHOD": method if isinstance(method, str) else method.get(label, "unknown"),
                    "SOURCE": source,
                    "REF_AREA_LABEL": area_label or "Niger",
                    "INDICATOR_LABEL": indicator_label,
                }
            )
    return pd.DataFrame(records, columns=LONG_COLUMNS)


def sdmx_time_period(printed: str) -> str:
    """SDMX time format for an annual value.

    "2024" stays "2024". A campaign printed "2024/2025" becomes the SDMX reporting year
    "2024-A1": the year the period starts, as the SDMX guidelines require. Anything else is
    returned unchanged and will not validate as a time period.
    """
    text = printed.strip()
    if PLAIN_YEAR.match(text):
        return text
    split = SPLIT_YEAR.match(text)
    if split:
        return f"{split.group(1)}-A1"
    return text


def _axis_kind(labels: list[str], mapping: pd.DataFrame) -> str:
    """ "area", "year" or "other", by majority of the labels on that axis."""
    clean = [_strip_suffix(lb) for lb in labels]
    if clean and sum(bool(YEAR_HEADER.match(lb.strip())) for lb in clean) / len(clean) >= 0.5:
        return "year"
    if _share_matching(clean, mapping, "REF_AREA") >= 0.5:
        return "area"
    return "other"


def _assign_axes(row: str, column: str, row_kind: str, col_kind: str, subject: str, period: str):
    """Returns (area label, indicator label, time period) for one cell."""
    row_clean, col_clean = _strip_suffix(row), column.strip()
    area = row_clean if row_kind == "area" else col_clean if col_kind == "area" else ""
    time = row_clean if row_kind == "year" else col_clean if col_kind == "year" else period
    leftovers = [lb for lb, kind in ((row_clean, row_kind), (col_clean, col_kind)) if kind == "other"]
    indicator = LABEL_JOIN.join(leftovers) if leftovers else subject
    return area, indicator, time


def _indicator(label: str, mapping: pd.DataFrame) -> tuple[str, str]:
    """Composite labels such as "Mil / Superficie" map part by part: MILLET_AREA_HA, unit ha."""
    codes, units = [], []
    for part in label.split(LABEL_JOIN):
        code, unit = _code_for(part, mapping, "INDICATOR")
        codes.append(code)
        if unit:
            units.append(unit)
    return "_".join(codes), units[0] if units else ""


def _code_for(label: str, mapping: pd.DataFrame, dimension: str) -> tuple[str, str]:
    """Exact then fuzzy match against the mapping file. Unknown labels get a slug, not a guess."""
    candidates = mapping[mapping["dimension"] == dimension]
    if candidates.empty:
        return sdmx_code(label), ""
    exact = candidates[candidates["label"].str.casefold() == label.casefold()]
    if not exact.empty:
        return sdmx_code(exact["code"].iloc[0]), exact["unit"].iloc[0]
    match = process.extractOne(
        label, candidates["label"].tolist(), scorer=fuzz.WRatio, processor=default_process, score_cutoff=MATCH_THRESHOLD
    )
    if match:
        hit = candidates[candidates["label"] == match[0]].iloc[0]
        return sdmx_code(hit["code"]), hit["unit"]
    return sdmx_code(label), ""


def _share_matching(labels: list[str], mapping: pd.DataFrame, dimension: str) -> float:
    known = mapping.loc[mapping["dimension"] == dimension, "label"].tolist()
    if not labels or not known:
        return 0.0
    hits = sum(
        bool(process.extractOne(lb, known, scorer=fuzz.WRatio, processor=default_process, score_cutoff=MATCH_THRESHOLD))
        for lb in labels
    )
    return hits / len(labels)


def _unit_for(indicator_label: str, mapped_unit: str, default: str) -> str:
    """The unit as a code. A header printing "(kg/ha)" gives KG_HA, not kg/ha."""
    found = UNIT_IN_HEADER.search(indicator_label)
    if found:
        return sdmx_code(found.group(1))
    return sdmx_code(mapped_unit or default)


def _status(parse_status: str, failed_check: bool) -> str:
    if parse_status == "error" or failed_check:
        return STATUS_FAILED_CHECK
    return STATUS_OK


def _failed_cells(checks: list[Check]) -> set[tuple[str, str]]:
    return {(c.row, c.column) for c in checks if c.status == "fail" and c.row}


def _strip_suffix(label: str) -> str:
    return DUPLICATE_SUFFIX.sub("", label.strip())


def sdmx_code(text: str) -> str:
    """A value usable as an SDMX code id.

    The standard allows letters, digits and `_ @ $ -`, so "NE-1" and "_T" pass through
    unchanged while a unit printed "kg/ha" becomes KG_HA. Applied to every coded
    component, including codes read from the mapping file, because that file is hand
    edited. A leading underscore is kept only when the text already had one, so the SDMX
    total code `_T` survives.
    """
    code = NOT_IN_A_CODE.sub("_", text.strip()).upper().rstrip("_")
    if not text.startswith("_"):
        code = code.lstrip("_")
    return code or "UNKNOWN"
