import pandas as pd

from pdf2sdmx.core.table import ExtractedTable
from pdf2sdmx.core.validate import check_header, run_checks


def agri_table(production_maradi="240 000"):
    cells = [
        ["Région", "Superficie (ha)", "Rendement (kg/ha)", "Production (t)"],
        ["Maradi", "400 000", "600", production_maradi],
        ["Zinder", "300 000", "500", "150 000"],
        ["Niger", "700 000", "557", "390 000"],
    ]
    return ExtractedTable(cells, page=1, method="test").to_frame()


def statuses(checks, name):
    return [c.status for c in checks if c.name == name]


def test_header_rows_are_merged_into_columns():
    frame = agri_table()
    assert list(frame.columns) == ["Région", "Superficie (ha)", "Rendement (kg/ha)", "Production (t)"]
    assert len(frame) == 3


def test_column_totals_pass_on_consistent_table():
    checks = run_checks(agri_table())
    assert "fail" not in statuses(checks, "column_total")


def test_yield_column_total_is_warning_not_failure():
    checks = run_checks(agri_table())
    yield_checks = [c for c in checks if c.name == "column_total" and "Rendement" in c.column]
    assert yield_checks and yield_checks[0].status == "warn"


def test_area_times_yield_catches_a_wrong_production():
    checks = run_checks(agri_table(production_maradi="24 000"))
    failed = [c for c in checks if c.name == "area_x_yield" and c.status == "fail"]
    assert failed and failed[0].row == "Maradi"


def test_column_total_fails_when_a_part_is_misread():
    frame = agri_table()
    frame.loc[frame["Région"] == "Zinder", "Production (t)"] = "15 000"  # a dropped digit
    failed = [c for c in run_checks(frame) if c.name == "column_total" and c.status == "fail"]
    assert any(c.column == "Production (t)" for c in failed)


def test_unreadable_cell_is_reported_with_position():
    frame = agri_table()
    frame.loc[frame["Région"] == "Maradi", "Superficie (ha)"] = "4OO 000"  # letter O instead of zero
    failed = [c for c in run_checks(frame) if c.name == "unreadable"]
    assert failed[0].status == "fail" and failed[0].row == "Maradi"


def test_year_jump_warns_on_factor_five():
    frame = pd.DataFrame({"Produit": ["Mil", "Sorgho"], "2020": ["100", "200"], "2021": ["600", "210"]})
    warns = [c for c in run_checks(frame) if c.name == "year_jump" and c.status == "warn"]
    assert len(warns) == 1 and warns[0].row == "Mil"


def test_a_data_row_glued_into_the_header_is_a_failure():
    """A header merged with the first row leaves the numbers right and the columns wrong.

    Nothing else notices: the totals still add up. Counting it as a failure is what makes
    the cascade try another stage.
    """
    glued = pd.DataFrame(columns=["Désignation", "31 déc.22 63 799", "31 déc.23 61 630"])
    check = check_header(glued)[0]
    assert check.status == "fail"
    assert "glued" in check.detail


def test_a_period_or_a_unit_in_a_column_name_is_not_a_glued_row():
    clean = pd.DataFrame(columns=["Désignation", "31 déc.22", "2024", "Superficie (ha)", "1 T24"])
    assert check_header(clean)[0].status == "pass"
