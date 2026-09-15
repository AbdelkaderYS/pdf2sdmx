import pandas as pd

from pdf2sdmx.core import validate
from pdf2sdmx.core.ingest.cascade import Candidate, repair_rows
from pdf2sdmx.core.quality import gate_table

COLUMNS = ["PRODUIT / col", "AGADEZ", "TAHOUA", "ENSEMBLE"]


def candidate(method: str, rows: list[list[str]]) -> Candidate:
    frame = pd.DataFrame(rows, columns=COLUMNS)
    failed = sum(c.status == "fail" for c in validate.run_checks(frame))
    return Candidate(method, frame, gate_table(frame), failed)


def test_unreadable_row_is_replaced_by_a_donor_row_that_lowers_failures():
    winner = candidate(
        "pdfplumber",
        [
            ["Mil / Superficie", "100", "200", "300"],
            ["Poivron / Superficie", "290 22 955 6 657", "9 21 111 190", "299"],
            ["Poivron / Rendement", "", "", "21 232"],
            ["Poivron / Production", "", "", "6 847"],
        ],
    )
    donor = candidate(
        "camelot_ml",
        [
            ["Mil / Superficie", "100", "200", "300"],
            ["Poivron / Superficie", "290", "9", "299"],
            ["Poivron / Rendement", "22 955", "21 111", "21 232"],
            ["Poivron / Production", "6 657", "190", "6 847"],
        ],
    )
    frame, methods = repair_rows(winner, [donor])
    assert frame.iloc[1].tolist()[1:] == ["290", "9", "299"]
    assert frame.iloc[2].tolist()[1:] == ["22 955", "21 111", "21 232"]
    assert methods == {
        "Mil / Superficie": "pdfplumber",
        "Poivron / Superficie": "camelot_ml",
        "Poivron / Rendement": "camelot_ml",
        "Poivron / Production": "camelot_ml",
    }


def test_donor_that_disagrees_on_a_read_cell_is_refused():
    winner = candidate("pdfplumber", [["Mil / Production", "", "200", "300"], ["Sorgho / Production", "5", "5", "10"]])
    donor = candidate(
        "camelot_ml", [["Mil / Production", "100", "999", "300"], ["Sorgho / Production", "5", "5", "10"]]
    )
    frame, methods = repair_rows(winner, [donor])
    assert frame.iloc[0].tolist()[1:] == ["", "200", "300"]
    assert set(methods.values()) == {"pdfplumber"}


def test_donor_with_different_columns_is_ignored():
    winner = candidate("pdfplumber", [["Mil / Production", "1 2 3", "200", "300"]])
    donor = Candidate("camelot_ml", pd.DataFrame([["Mil / Production", "1", "2"]], columns=["a", "b", "c"]), None, 0)
    frame, methods = repair_rows(winner, [donor])
    assert frame.iloc[0, 1] == "1 2 3"
