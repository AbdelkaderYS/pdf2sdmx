"""Parse numbers the way a French language report prints them: space thousands, comma decimals."""

import re
from dataclasses import dataclass

from pdf2sdmx.config import settings

# Thousands are separated by a regular, non-breaking or narrow space, sometimes mixed.
_SPACES = re.compile(r"[\s   ]+")
# The dashes are data, not punctuation: a table prints one where a value is missing.
_MISSING = {"", "-", "–", "—", "nd", "n.d", "n.d.", "na", "n/a", "...", "…", "x", "/", "//"}
_FOOTNOTE = re.compile(r"[\*¹²³]+$|\s*\([a-z]\)$")
_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
# "1 234 567,5": groups of exactly three digits after the first. "290 22 955" is two
# cells glued together, not a number, and must be rejected rather than read as 29022955.
_GROUPED = re.compile(r"^[+-]?\d{1,3}(?:[\s   ]\d{3})+(?:[.,]\d+)?$")


@dataclass(frozen=True)
class Parsed:
    value: float | None
    status: str  # "ok", "missing", "error"
    raw: str


def parse_number(raw: object) -> Parsed:
    """Turn a cell string into a float. Returns status "missing" for dashes and "error" for junk."""
    text = "" if raw is None else str(raw).strip()
    if text.lower() in _MISSING:
        return Parsed(None, "missing", text)

    cleaned = _FOOTNOTE.sub("", text).strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()").replace("%", "").strip()
    if _SPACES.search(cleaned):
        if not _GROUPED.match(cleaned):
            return Parsed(None, "error", text)
        cleaned = _SPACES.sub("", cleaned)
    cleaned = _normalise_separators(cleaned)

    if not _NUMBER.match(cleaned):
        return Parsed(None, "error", text)
    value = float(cleaned.replace(",", "."))
    return Parsed(-value if negative else value, "ok", text)


def _normalise_separators(text: str) -> str:
    """Collapse French thousands dots. "1.234.567" -> "1234567", "12.5" stays a decimal."""
    if "," in text:
        return text.replace(".", "")
    if re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+", text):
        return text.replace(".", "")
    return text


# Signs of each way of printing numbers. "1,234" alone could be either, so it is not one.
_ENGLISH = re.compile(r"\d,\d{3}(?:,\d{3}|\.\d)|(?<![\d.,])\d+\.\d{1,2}(?![\d.,])")
_FRENCH = re.compile(r"\d[ \u00a0\u202f]\d{3}(?!\d)|(?<![\d.,])\d+,\d{1,2}(?![\d.,])")
_ENGLISH_NUMBER = re.compile(r"^\(?[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?[*%]?$")


def english_page(text: str) -> bool:
    """Whether a page prints "1,234.5" rather than "1 234,5", by the more frequent sign."""
    english, french = len(_ENGLISH.findall(text)), len(_FRENCH.findall(text))
    return english > french if english != french else settings.number_format == "en"


def to_french(cell: object) -> object:
    """An English number as the parser reads it: "1,234.5" becomes "1234,5". Text is kept."""
    if not isinstance(cell, str):
        return cell
    lines = cell.split("\n")
    return "\n".join(s.replace(",", "").replace(".", ",") if _ENGLISH_NUMBER.match(s.strip()) else s for s in lines)


def looks_numeric(raw: object) -> bool:
    return parse_number(raw).status == "ok"


def is_missing_marker(raw: object) -> bool:
    return parse_number(raw).status == "missing"
