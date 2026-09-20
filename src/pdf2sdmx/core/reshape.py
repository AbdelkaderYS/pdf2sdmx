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
MATCH_THRESHOLD = settings.vocabulary_match_threshold
# Where a table gives no breakdown, the observation is about the publishing country as a
# whole. Which country that is comes from the settings, not from here.
COUNTRY = settings.country
COUNTRY_LABEL = settings.country_name
# SDMX conventions for a dimension that carries no value here: _T when the observation is
# the total over that dimension, _Z when the label could not be identified at all.
TOTAL = "_T"
NOT_IDENTIFIED = "_Z"
# A total is a convention of every dimension, not a member of any one of them. Recognised
# by pattern rather than by a vocabulary row, so that a label carrying the word does not
# make its axis look geographical.
TOTAL_LABEL = re.compile(r"\b(total|totaux|ensemble|toutes?|tous)\b", re.I)
# A total written beside something else, as in "2020 Total", which is the year 2020.
TOTAL_MARKER = re.compile(r"\s*\b(total|totaux|ensemble)\b\s*", re.I)
NOT_IDENTIFIED_LABEL = "not identified"
# What a caller passes when it has no subject to give. It is not something a report printed.
PLACEHOLDER = "UNKNOWN"
# A unit sits in trailing parentheses, or after "en" in a title or a column name.
UNIT_IN_HEADER = re.compile(r"\(([^)]+)\)\s*$")
UNIT_AFTER_EN = re.compile(r"\ben\s+([^,;()]{1,28}?)(?=\s+(?:de|du|des|par|dans|pour)\b|[,;)]|$)", re.I)
# SDMX keeps the multiplier apart: "en milliers de m3" is M3 with UNIT_MULT 3.
MULTIPLIER = re.compile(r"\b(milliers?|millions?|milliards?)\b", re.I)
MULTIPLIER_POWER = {"millier": "3", "milliers": "3", "million": "6", "millions": "6", "milliard": "9", "milliards": "9"}
# An SDMX code carries no symbols, and the published unit lists spell these with letters.
SYMBOL_CODE = {"m\u00b3": "m3", "us\\s*\\$": "usd", "%": "per", "\\$": "usd", "\u20ac": "eur"}
# Where the portal this work feeds already names a unit, use its name rather than ours.
# Read from the reference structures in data/reference.
PORTAL_UNIT = {
    "NOMBRE": "NUMBER",
    "PCT": "PER",
    "POURCENT": "PER",
    "POURCENTS": "PER",
    "TONNE": "T",
    "TONNES": "T",
    "HECTARE": "HA",
    "HECTARES": "HA",
    "LITRE": "L",
    "LITRES": "L",
    "METRE_CUBE": "M3",
    "METRES_CUBES": "M3",
    "KILOGRAMME": "KG",
    "KILOGRAMMES": "KG",
    "UNITE": "NUMBER",
    "UNITES": "NUMBER",
}
# A unit, once any multiplier is taken out. Anything else is not a unit.
UNIT_TEXT = re.compile(
    r"^(?:ha|hectares?|t|tonnes?|kg(?:\s*/\s*\w+)?|g|l|litres?|m3|m\u00b3|km2?|%|pourcents?|"
    r"fcfa|f\s?cfa|us\s*\$(?:\s*/\s*\w+)?|\$|euros?|unit[e\u00e9]s?|nombre|indice|"
    r"habitants?|kwh|gwh|mw|points?|m[e\u00e8]tres?(?:\s+cubes?)?|barils?|t[e\u00ea]tes?)$",
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
    subject: str = "",
    title: str = "",
) -> pd.DataFrame:
    """One observation per numeric cell.

    Each axis is classified as areas, periods or other. `method` is one stage name, or a
    row label to stage name mapping when rows came from different stages.
    """
    labels = row_labels(frame)
    columns = list(frame.columns[1:])
    row_kind = _axis_kind(labels, mapping)
    col_kind = _axis_kind(columns, mapping)
    failed = _failed_cells(checks)
    # A check naming no cell condemns the whole table, so every number under it is suspect.
    table_failed = any(c.status == "fail" and not c.row for c in checks)

    records = []
    for label, row in zip(labels, frame.iloc[:, 1:].itertuples(index=False), strict=True):
        for column, cell in zip(columns, row, strict=True):
            parsed = parse_number(cell)
            if parsed.status == "missing":
                continue
            area_label, indicator_label, period = _assign_axes(label, column, row_kind, col_kind, subject, time_period)
            area_code, leftover = _resolve_area(area_label, mapping) if area_label else ("", "")
            indicator_label = _join_indicator(leftover, indicator_label, subject)
            measure, breakdown, mapped_unit = _split_indicator(indicator_label, mapping, title)
            unit_code, multiplier = _unit_for(indicator_label, mapped_unit, unit)
            time_code = sdmx_time_period(period)
            records.append(
                {
                    "FREQ": sdmx_frequency(time_code),
                    "REF_AREA": area_code or COUNTRY,
                    "INDICATOR": measure.code,
                    "COMPOSITE_BREAKDOWN": breakdown.code,
                    "TIME_PERIOD": time_code,
                    "OBS_VALUE": parsed.value,
                    "UNIT_MEASURE": unit_code,
                    "UNIT_MULT": multiplier,
                    "OBS_STATUS": _status(parsed.status, table_failed or (label, column) in failed),
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
    """A printed period as SDMX writes it.

    "2024/2025" becomes the reporting year "2024-A1", "1 T24" becomes "2024-Q1", and a
    stock date becomes a calendar day. Anything else comes back unchanged and will not
    validate.
    """
    text = _without_footnote(TOTAL_MARKER.sub(" ", printed).strip())
    if PLAIN_YEAR.match(text):
        return text
    split = SPLIT_YEAR.match(text)
    if split:
        return f"{split.group(1)}-A1"
    # A search, not a match: merged header rows read "2021 1 T21", and the quarter wins.
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
    """Drop a provisional or revised marker glued to a period: "2010*", "2023 (p)"."""
    stripped = FOOTNOTE_MARKER.sub("", text).strip()
    return stripped or text


def _four_digit_year(year: str) -> str:
    """A quarter is often printed with a two digit year, as in "1 T24". Read it as 20xx."""
    if len(year) == 2:
        return f"20{year}"
    return year


def _axis_kind(labels: list[str], mapping: pd.DataFrame) -> str:
    """ "area", "year" or "other", by majority. "year" covers any period, quarters included."""
    clean = [_strip_suffix(lb) for lb in labels]
    if not clean:
        return "other"
    periods = sum(bool(PERIOD_HEADER.match(TOTAL_MARKER.sub(" ", lb).strip())) for lb in clean)
    if periods / len(clean) >= 0.5:
        return "year"
    # A total says nothing about which dimension it totals, so it is not evidence of areas.
    named = [lb for lb in clean if not TOTAL_LABEL.fullmatch(lb.strip())]
    if named and _share_matching(named, mapping, "REF_AREA") >= 0.5:
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
    """What is left of an area label joins the indicator, or replaces a bare subject."""
    if not leftover:
        return indicator
    if indicator == subject:
        return leftover
    return LABEL_JOIN.join([leftover, indicator])


def _kind_of_label(label: str, axis_kind: str) -> str:
    """The axis is decided by majority; a label that disagrees becomes an indicator.

    A table of periods often ends with a variation column, which is not a period.
    """
    if axis_kind == "year" and not PERIOD_HEADER.match(TOTAL_MARKER.sub(" ", label).strip()):
        return "other"
    return axis_kind


@dataclass(frozen=True)
class Coded:
    """A dimension value: the code written to SDMX and the label as the report printed it."""

    code: str
    label: str


def _split_indicator(label: str, mapping: pd.DataFrame, title: str = "") -> tuple[Coded, Coded, str]:
    """Separate the measure from the thing it is measured on.

    A label naming both at once needs one code per combination, so nothing repeats and
    nothing can be queried. INDICATOR keeps the measure, a short closed list, and
    COMPOSITE_BREAKDOWN takes the rest, as the SDMX domain modelling guideline advises.

    A table whose rows are what is counted often names the measure only in its title, so
    the title is the last place looked. Failing that, INDICATOR is _Z and the breakdown
    still holds the label.
    """
    measure = Coded("", "")
    breakdown = Coded("", "")
    leftovers: list[str] = []
    unit = ""
    for part in label.split(LABEL_JOIN):
        part = part.strip()
        if not part or part == PLACEHOLDER or TOTAL_LABEL.fullmatch(part):
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

    if not measure.code and title:
        measure = _measure_in(title, mapping)

    if leftovers and not breakdown.code:
        text = LABEL_JOIN.join(leftovers)
        breakdown = Coded(sdmx_code(text), text)
    elif leftovers:
        breakdown = Coded(breakdown.code, LABEL_JOIN.join([breakdown.label, *leftovers]))

    return (
        measure if measure.code else Coded(NOT_IDENTIFIED, NOT_IDENTIFIED_LABEL),
        breakdown if breakdown.code else Coded(TOTAL, ""),
        unit,
    )


def _code_for(label: str, mapping: pd.DataFrame, dimension: str) -> tuple[str, str]:
    """Exact then fuzzy match against the vocabulary. Empty code when nothing matched.

    An area that matched nothing must stay empty: minting a code from the label would
    declare any unrecognised word a place.
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
    """(area code, what is left) for a label the axis called an area.

    A merged label names an area in one part only; the rest belongs to the indicator.
    """
    parts = label.split(LABEL_JOIN)
    for position, part in enumerate(parts):
        if TOTAL_LABEL.fullmatch(part.strip()):
            return TOTAL, LABEL_JOIN.join(parts[:position] + parts[position + 1 :])
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


def _measure_in(text: str, mapping: pd.DataFrame) -> Coded:
    """The first word of a title that names a known measure, if any."""
    for word in re.split(r"[\s,;:.]+", text):
        code, _ = _code_for(word, mapping, "INDICATOR")
        if code:
            return Coded(code, word)
    return Coded("", "")


def _unit_for(indicator_label: str, mapped_unit: str, default: str) -> tuple[str, str]:
    """(unit code, multiplier), from the most specific source that names one."""
    for text in (indicator_label, mapped_unit, default):
        unit, multiplier = unit_and_multiplier(text)
        if unit:
            return unit, multiplier
    return "UNKNOWN", "0"


def unit_and_multiplier(text: str) -> tuple[str, str]:
    """Pull a unit and its power of ten out of a printed phrase.

    "en milliers de m3" gives (M3, 3) and "(kg/ha)" gives (KG_HA, 0). A phrase naming no
    unit gives ("", "0), so the caller can look elsewhere.
    """
    if not text:
        return "", "0"
    candidates = [text]
    found = UNIT_IN_HEADER.search(text)
    if found:
        candidates.insert(0, found.group(1))
    candidates += UNIT_AFTER_EN.findall(text)
    multiplier = MULTIPLIER.search(text)
    power = MULTIPLIER_POWER[multiplier.group(1).casefold()] if multiplier else "0"
    for candidate in candidates:
        stripped = MULTIPLIER.sub("", candidate)
        stripped = re.sub(r"^\s*(?:en\s+)?(?:de\s+|du\s+|des\s+|d[\u2019\']\s*)?", "", stripped).strip()
        if UNIT_TEXT.match(stripped):
            return _unit_code(stripped), power
    return "", "0"


def _unit_code(text: str) -> str:
    """A unit as an SDMX code, with the symbols spelled out first."""
    for pattern, letters in SYMBOL_CODE.items():
        text = re.sub(pattern, f" {letters} ", text, flags=re.I)
    code = sdmx_code(text)
    return PORTAL_UNIT.get(code, code)


def _status(parse_status: str, failed_check: bool) -> str:
    if parse_status == "error" or failed_check:
        return STATUS_FAILED_CHECK
    return STATUS_OK


def _failed_cells(checks: list[Check]) -> set[tuple[str, str]]:
    return {(c.row, c.column) for c in checks if c.status == "fail" and c.row}


def _strip_suffix(label: str) -> str:
    return DUPLICATE_SUFFIX.sub("", label.strip())


def sdmx_code(text: str) -> str:
    """Text as an SDMX code id.

    The standard allows letters, digits and `_ @ $ -`, so "NE-1" and "_T" pass through and
    "kg/ha" becomes KG_HA. Accents are transliterated, not replaced. A leading underscore
    survives only if the text had one, which keeps the total code `_T`.
    """
    plain = unicodedata.normalize("NFKD", text.strip())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    code = NOT_IN_A_CODE.sub("_", plain).upper().rstrip("_")
    if not text.startswith("_"):
        code = code.lstrip("_")
    return code or "UNKNOWN"
