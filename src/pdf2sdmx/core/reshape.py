"""Turn a wide printed table into one observation per row, with SDMX style columns."""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process
from rapidfuzz.utils import default_process

from pdf2sdmx.config import settings
from pdf2sdmx.core.numbers import parse_number
from pdf2sdmx.core.table import LABEL_JOIN, PERIOD_HEADER
from pdf2sdmx.core.validate import Check, row_labels

# Column ids follow the SDMX cross-domain concepts (FREQ, REF_AREA, TIME_PERIOD, OBS_VALUE,
# UNIT_MEASURE, UNIT_MULT, OBS_STATUS), the same ids the World Bank WDI DSD uses.
LONG_COLUMNS = [
    "FREQ",
    "REF_AREA",
    "INDICATOR",
    "COMPOSITE_BREAKDOWN",
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
    "COMPOSITE_BREAKDOWN_LABEL",
]
MATCH_THRESHOLD = 88
# Where a table gives no breakdown, the observation is about the publishing country as a
# whole. Which country that is comes from the settings, not from here.
COUNTRY = settings.country
COUNTRY_LABEL = settings.country_name
# SDMX conventions for a dimension that carries no value here: _T when the observation is
# the total over that dimension, _Z when the label could not be identified at all.
TOTAL = "_T"
NOT_IDENTIFIED = "_Z"
UNIT_IN_HEADER = re.compile(r"\(([^)]+)\)\s*$")
# Trailing parentheses hold a unit only when they hold nothing else. "(1 à 10 m3/mois)" is a
# tariff band and "(17 à 22 places)" a vehicle class; reading either as a unit is worse than
# reading none, because an attribute that means something else still looks filled in.
UNIT_TEXT = re.compile(
    r"^(?:en\s+)?(?:milliers?|millions?|milliards?)?\s*(?:de\s+|d[\u2019\']\s*)?"
    r"(?:ha|hectares?|t|tonnes?|kg(?:\s*/\s*ha)?|g|l|litres?|m3|m\u00b3|km2?|%|"
    r"fcfa|f\s?cfa|unit[e\u00e9]s?|nombre|indice|habitants?|kwh|gwh|mw|points?)$",
    re.I,
)
DUPLICATE_SUFFIX = re.compile(r"\s\(\d+\)$")
SPLIT_YEAR = re.compile(r"^((?:19|20)\d{2})\s*[/-]\s*(?:19|20)?\d{2}$")
# A quarter written "1 T24" or, less often, "T1 2024". Bounded by word breaks rather than
# anchored, so a quarter is still found inside a name that carries its year as well.
QUARTER_THEN_YEAR = re.compile(r"\b([1-4])\s*T\s*[-.]?\s*((?:19|20)?\d{2})\b", re.I)
QUARTER_BEFORE_YEAR = re.compile(r"\bT\s*([1-4])\s*[-.]?\s*((?:19|20)?\d{2})\b", re.I)
# A stock date printed "31 déc-22" or "30 sept-24", which SDMX writes as a calendar day.
DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})\s*(janv|f[e\u00e9]vr|mars|avr|mai|juin|juil|ao[u\u00fb]t|sept|oct|nov|d[e\u00e9]c)"
    r"\w*\.?\s*-?\s*((?:19|20)?\d{2})\b",
    re.I,
)
MONTH_NUMBER = {
    "janv": "01",
    "fevr": "02",
    "févr": "02",
    "mars": "03",
    "avr": "04",
    "mai": "05",
    "juin": "06",
    "juil": "07",
    "aout": "08",
    "août": "08",
    "sept": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
    "déc": "12",
}
# A marker printed next to a period, meaning provisional, estimated or revised:
# "2010*", "2023 (p)", "2 T23r".
FOOTNOTE_MARKER = re.compile(r"(?:\s*\*+|\s*\(\s*(?:p|e|r|prov|est|rev)\s*\)|(?<=\d)[pre])\s*$", re.I)
# Characters an SDMX code id may not contain. The standard allows A-Z a-z 0-9 and _ @ $ -
NOT_IN_A_CODE = re.compile(r"[^A-Za-z0-9_@$-]+")
PLAIN_YEAR = re.compile(r"^(?:19|20)\d{2}$")
DAILY_PERIOD = re.compile(r"^(?:19|20)\d{2}-\d{2}-\d{2}$")

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
            area_code, leftover = _resolve_area(area_label, mapping) if area_label else ("", "")
            indicator_label = _join_indicator(leftover, indicator_label, subject)
            measure, breakdown, mapped_unit = _split_indicator(indicator_label, mapping)
            time_code = sdmx_time_period(period)
            records.append(
                {
                    "FREQ": sdmx_frequency(time_code),
                    "REF_AREA": area_code or COUNTRY,
                    "INDICATOR": measure.code,
                    "COMPOSITE_BREAKDOWN": breakdown.code,
                    "TIME_PERIOD": time_code,
                    "OBS_VALUE": parsed.value,
                    "UNIT_MEASURE": _unit_for(indicator_label, mapped_unit, unit),
                    "UNIT_MULT": "0",
                    "OBS_STATUS": _status(parsed.status, (label, column) in failed),
                    "TIME_PERIOD_LABEL": period,
                    "EXTRACTION_METHOD": method if isinstance(method, str) else method.get(label, "unknown"),
                    "SOURCE": source,
                    "REF_AREA_LABEL": area_label if area_code else COUNTRY_LABEL,
                    "INDICATOR_LABEL": measure.label,
                    "COMPOSITE_BREAKDOWN_LABEL": breakdown.label,
                }
            )
    return pd.DataFrame(records, columns=LONG_COLUMNS)


def sdmx_time_period(printed: str) -> str:
    """SDMX time format for the periods a printed table uses as a column name.

    "2024" stays "2024". A range printed "2024/2025" becomes the SDMX reporting year
    "2024-A1": the year the period starts, as the SDMX guidelines require. A quarter
    printed "1 T24" becomes "2024-Q1". Anything else is returned unchanged and will not
    validate as a time period.
    """
    text = _without_footnote(printed.strip())
    if PLAIN_YEAR.match(text):
        return text
    split = SPLIT_YEAR.match(text)
    if split:
        return f"{split.group(1)}-A1"
    # A search rather than a match: two header rows merged into "2021 1 T21" name the same
    # quarter twice, and the quarter is the one that carries the information.
    day = DAY_MONTH_YEAR.search(text)
    if day:
        month = MONTH_NUMBER[day.group(2).casefold()]
        return f"{_four_digit_year(day.group(3))}-{month}-{int(day.group(1)):02d}"
    quarter = QUARTER_THEN_YEAR.search(text) or QUARTER_BEFORE_YEAR.search(text)
    if quarter:
        return f"{_four_digit_year(quarter.group(2))}-Q{quarter.group(1)}"
    return text


def sdmx_frequency(time_period: str) -> str:
    """The FREQ code that goes with a period written by sdmx_time_period."""
    if "-Q" in time_period:
        return "Q"
    if DAILY_PERIOD.match(time_period):
        return "D"
    return "A"


def _without_footnote(text: str) -> str:
    """Drop a footnote marker glued to a period, so "2010*" and "2023 (p)" become periods.

    The marker usually means provisional or revised. It is not dropped from the printed
    label, which TIME_PERIOD_LABEL keeps as it stands.
    """
    stripped = FOOTNOTE_MARKER.sub("", text).strip()
    return stripped or text


def _four_digit_year(year: str) -> str:
    """A quarter is often printed with a two digit year, as in "1 T24". Read it as 20xx."""
    if len(year) == 2:
        return f"20{year}"
    return year


def _axis_kind(labels: list[str], mapping: pd.DataFrame) -> str:
    """ "area", "year" or "other", by majority of the labels on that axis.

    "year" covers any period, a quarter as much as a year, since both become TIME_PERIOD.
    """
    clean = [_strip_suffix(lb) for lb in labels]
    if clean and sum(bool(PERIOD_HEADER.match(lb.strip())) for lb in clean) / len(clean) >= 0.5:
        return "year"
    if _share_matching(clean, mapping, "REF_AREA") >= 0.5:
        return "area"
    return "other"


def _assign_axes(row: str, column: str, row_kind: str, col_kind: str, subject: str, period: str):
    """Returns (area label, indicator label, time period) for one cell."""
    row_clean, col_clean = _strip_suffix(row), column.strip()
    row_kind = _kind_of_label(row_clean, row_kind)
    col_kind = _kind_of_label(col_clean, col_kind)
    area = row_clean if row_kind == "area" else col_clean if col_kind == "area" else ""
    time = row_clean if row_kind == "year" else col_clean if col_kind == "year" else period
    leftovers = [lb for lb, kind in ((row_clean, row_kind), (col_clean, col_kind)) if kind == "other"]
    indicator = LABEL_JOIN.join(leftovers) if leftovers else subject
    return area, indicator, time


def _join_indicator(leftover: str, indicator: str, subject: str) -> str:
    """What is left of an area label joins the indicator.

    When the indicator is only the table subject, the leftover says more on its own and
    replaces it rather than being glued in front of it.
    """
    if not leftover:
        return indicator
    if indicator == subject:
        return leftover
    return LABEL_JOIN.join([leftover, indicator])


def _kind_of_label(label: str, axis_kind: str) -> str:
    """The axis is classified by majority, one label at a time can still disagree.

    A table of periods often ends with a column such as a variation or a share. Forcing it
    into TIME_PERIOD would write a sentence where a period belongs, so it becomes one more
    indicator instead.
    """
    if axis_kind == "year" and not PERIOD_HEADER.match(label):
        return "other"
    return axis_kind


@dataclass(frozen=True)
class Coded:
    """A dimension value: the code written to SDMX and the label as the report printed it."""

    code: str
    label: str


def _split_indicator(label: str, mapping: pd.DataFrame) -> tuple[Coded, Coded, str]:
    """Separate what is measured from the thing it is measured on.

    A printed label such as "Mil / Superficie" names both at once. Written as one code it
    gives a list with one entry per combination, where nothing repeats and nothing can be
    queried. INDICATOR keeps the measure, which is a short closed list, and the rest goes
    to COMPOSITE_BREAKDOWN beside it. This is how the UN SDG structure models the same
    problem, and the SDMX guideline on modelling a domain calls it decomposing an
    indicator set.

    A part that matches no measure leaves INDICATOR not identified rather than minting a
    code that looks official. Nothing is lost: the label still reaches the breakdown.
    """
    measure = Coded("", "")
    breakdown = Coded("", "")
    leftovers: list[str] = []
    unit = ""
    for part in label.split(LABEL_JOIN):
        part = part.strip()
        if not part:
            continue
        code, part_unit = _code_for(part, mapping, "INDICATOR")
        if code and not measure.code:
            measure = Coded(code, part)
            unit = unit or part_unit
            continue
        code, part_unit = _code_for(part, mapping, "COMPOSITE_BREAKDOWN")
        if code and not breakdown.code:
            breakdown = Coded(code, part)
            unit = unit or part_unit
            continue
        leftovers.append(part)

    if leftovers and not breakdown.code:
        text = LABEL_JOIN.join(leftovers)
        breakdown = Coded(sdmx_code(text), text)
    elif leftovers:
        breakdown = Coded(breakdown.code, LABEL_JOIN.join([breakdown.label, *leftovers]))

    return (
        measure if measure.code else Coded(NOT_IDENTIFIED, label),
        breakdown if breakdown.code else Coded(TOTAL, ""),
        unit,
    )


def _code_for(label: str, mapping: pd.DataFrame, dimension: str) -> tuple[str, str]:
    """Exact then fuzzy match against the mapping file. Empty code when nothing matched.

    A caller that can name the thing anyway, such as an indicator, falls back to a slug. A
    caller that cannot, such as a reference area, must not: turning an unmatched label into
    an area code is how livestock and petroleum products ended up declared as countries.
    """
    candidates = mapping[mapping["dimension"] == dimension]
    if candidates.empty:
        return "", ""
    exact = candidates[candidates["label"].str.casefold() == label.casefold()]
    if not exact.empty:
        return sdmx_code(exact["code"].iloc[0]), exact["unit"].iloc[0]
    match = process.extractOne(
        label, candidates["label"].tolist(), scorer=fuzz.WRatio, processor=default_process, score_cutoff=MATCH_THRESHOLD
    )
    if match:
        hit = candidates[candidates["label"] == match[0]].iloc[0]
        return sdmx_code(hit["code"]), hit["unit"]
    return "", ""


def _resolve_area(label: str, mapping: pd.DataFrame) -> tuple[str, str]:
    """(area code, what is left of the label) for a label the axis called an area.

    A row label merged from two printed columns reads "Dosso / Bovins". Only one part names
    an area; the rest belongs to the indicator. When no part names a known area the label is
    not an area at all and comes back whole.
    """
    parts = label.split(LABEL_JOIN)
    for position, part in enumerate(parts):
        code, _ = _code_for(part, mapping, "REF_AREA")
        if code:
            return code, LABEL_JOIN.join(parts[:position] + parts[position + 1 :])
    return "", label


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
    """The unit as a code. A header printing "(kg/ha)" gives KG_HA, not kg/ha.

    Text in trailing parentheses is taken only when it reads as a unit. The mapping file and
    the caller are trusted, so their value is used as it stands.
    """
    found = UNIT_IN_HEADER.search(indicator_label)
    if found and UNIT_TEXT.match(found.group(1).strip()):
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

    Accents are transliterated rather than replaced, so "Régions" gives REGIONS and not
    R_GIONS.
    """
    plain = unicodedata.normalize("NFKD", text.strip())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    code = NOT_IN_A_CODE.sub("_", plain).upper().rstrip("_")
    if not text.startswith("_"):
        code = code.lstrip("_")
    return code or "UNKNOWN"
