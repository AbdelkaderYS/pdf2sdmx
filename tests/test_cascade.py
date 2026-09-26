from pathlib import Path

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


def test_table_with_only_blank_columns_yields_no_candidate():
    from pdf2sdmx.core.ingest.cascade import _gate_all
    from pdf2sdmx.core.table import ExtractedTable

    table = ExtractedTable([["Liste des tableaux", ""], ["Tableau 03.01", ""], ["Tableau 03.02", ""]], 1, "pdfplumber")
    accepted, rejected = _gate_all("pdfplumber", [table])
    assert accepted == [] and rejected is None


def test_two_tables_on_one_page_are_both_kept_and_matched_to_their_donor():
    from pdf2sdmx.core.ingest import cascade
    from pdf2sdmx.core.table import ExtractedTable

    header = ["Région", "Bovins", "Ovins", "Total"]
    first = [header, ["Agadez", "10", "20", "30"], ["Diffa", "1 2", "5", "6"], ["Total", "11", "25", "36"]]
    second = [header, ["Agadez", "100", "200", "300"], ["Diffa", "10", "50", "60"], ["Total", "110", "250", "360"]]
    fixed_first = [header, ["Agadez", "10", "20", "30"], ["Diffa", "1", "5", "6"], ["Total", "11", "25", "36"]]
    broken_second = [
        header,
        ["Agadez", "100 200", "", "300"],
        ["Diffa", "10", "50", "60"],
        ["Total", "110", "250", "360"],
    ]

    def stage_a(_pdf, _page):
        return [ExtractedTable(first, 1, "a"), ExtractedTable(second, 1, "a")]

    def stage_b(_pdf, _page):
        return [ExtractedTable(broken_second, 1, "b"), ExtractedTable(fixed_first, 1, "b")]

    result = cascade.run(Path("x.pdf"), 1, stages=[("a", stage_a), ("b", stage_b)])
    assert len(result.tables) == 2
    # Stage a wins on a tie. Its first table is repaired from b's matching table, not from
    # b's other table that shares the same headers and labels but holds different numbers.
    assert result.tables[0].frame.iloc[1, 1] == "1"
    assert result.tables[0].row_methods["Diffa"] == "b"
    assert result.tables[1].frame.iloc[0, 1] == "100"
    assert result.tables[1].resolved_by == "a"


def test_paddleocr_stage_reads_html_and_markdown_tables_from_the_model_output():
    from pdf2sdmx.core.ingest import paddleocr_stage

    result = {
        "res": {
            "parsing_res_list": [
                {"block_label": "text", "block_content": "Tableau 03.01"},
                {
                    "block_label": "table",
                    "block_content": "<table><tr><td>Région</td><td>Bovins</td></tr>"
                    "<tr><td>Agadez</td><td>55 507</td></tr></table>",
                },
                {"block_label": "table", "block_content": "| Région | Ovins |\n|---|---|\n| Diffa | 1 142 557 |"},
            ]
        }
    }
    grids = paddleocr_stage.tables_in(result)
    assert grids == [[["Région", "Bovins"], ["Agadez", "55 507"]], [["Région", "Ovins"], ["Diffa", "1 142 557"]]]


def test_rows_read_as_one_are_spread_back_into_the_blank_rows_below():
    from pdf2sdmx.core.table import unstack_rows

    cells = [
        ["Item", "Measure 1", "290\n22 955\n6 657", "-\n-\n-", "1 266"],
        [None, "Measure 2", None, None, "21 232"],
        [None, "Measure 3", None, None, "26 880"],
    ]
    out = unstack_rows(cells)
    assert [row[2] for row in out] == ["290", "22 955", "6 657"]
    assert [row[3] for row in out] == ["-", "-", "-"]
    assert [row[4] for row in out] == ["1 266", "21 232", "26 880"]


def test_a_wrapped_label_or_an_irregular_stack_is_left_alone():
    from pdf2sdmx.core.table import unstack_rows

    wrapped = [["Wrapped\nlabel", "Measure 1", "11 013"], [None, "Measure 2", "20 791"]]
    assert unstack_rows(wrapped) == wrapped
    uneven = [["A", "1\n2", "3\n4\n5"], ["B", None, None], ["C", None, None]]
    assert unstack_rows(uneven) == uneven
    occupied = [["A", "1\n2"], ["B", "9"]]
    assert unstack_rows(occupied) == occupied
