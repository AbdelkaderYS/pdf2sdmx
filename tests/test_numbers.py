import pytest

from pdf2sdmx.core.numbers import parse_number


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 234 567", 1234567.0),
        ("1 234 567", 1234567.0),
        ("1 234", 1234.0),
        ("12,5", 12.5),
        ("1 234,5", 1234.5),
        ("1.234.567", 1234567.0),
        ("12.5", 12.5),
        ("(123)", -123.0),
        ("-45", -45.0),
        ("12,5%", 12.5),
        ("1 234*", 1234.0),
        ("512 340 (a)", 512340.0),
        (2024, 2024.0),
    ],
)
def test_parses_french_formats(raw, expected):
    parsed = parse_number(raw)
    assert parsed.status == "ok"
    assert parsed.value == expected


@pytest.mark.parametrize("raw", ["", "-", "–", "nd", "n.d.", "...", "…", None, "NA"])
def test_missing_markers(raw):
    assert parse_number(raw).status == "missing"


@pytest.mark.parametrize("raw", ["Maradi", "12 abc", "1,2,3", "12.34.5"])
def test_unreadable(raw):
    assert parse_number(raw).status == "error"
