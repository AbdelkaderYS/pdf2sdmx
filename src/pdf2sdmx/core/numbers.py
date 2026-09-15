"""Parse numbers the way INS Niger prints them: space thousands, comma decimals."""

import re
from dataclasses import dataclass

# INS tables use a mix of regular, non-breaking and narrow spaces as thousands separators.
_SPACES = re.compile(r"[\s   ]+")
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


def looks_numeric(raw: object) -> bool:
    return parse_number(raw).status == "ok"


def is_missing_marker(raw: object) -> bool:
    return parse_number(raw).status == "missing"
