"""The interface must say why a page produced nothing, and how much was really checked.

A demo that answers "no table found" with no reason is the thing these tests guard
against.
"""

import pandas as pd

from pdf2sdmx.config import settings
from pdf2sdmx.core import pipeline
from pdf2sdmx.core.ingest.cascade import Attempt
from pdf2sdmx.core.pipeline import PageResult
from pdf2sdmx.core.quality import GateResult
from pdf2sdmx.ui import app as ui

SAMPLE = settings.data_raw.parent / "samples" / "ins_bulletin_3T25_p20-23.pdf"


def page_result(attempts: list[Attempt]) -> PageResult:
    return PageResult("s.pdf", 1, "manual", False, [], attempts, "2024")


def rejected_gate(reason: str) -> GateResult:
    return GateResult(False, 0.1, reason, {})


def test_a_skipped_page_reports_why_it_was_skipped():
    result = page_result([Attempt("text_scan", 0, None, 0.0, error="fewer than 100 digits")])
    assert ui.no_table_reason(result) == "fewer than 100 digits"


def test_a_rejected_table_reports_the_gate_that_rejected_it():
    result = page_result([Attempt("pdfplumber", 1, rejected_gate("numeric share 18%"), 0.2)])
    assert ui.no_table_reason(result) == "numeric share 18%"


def test_a_stage_that_never_ran_does_not_become_the_reason():
    """PaddleOCR reports "not installed" last. The rejected table is the real reason."""
    result = page_result(
        [
            Attempt("pdfplumber", 1, rejected_gate("numeric share 18%"), 0.2),
            Attempt("paddleocr_vl", 0, None, 0.0, error="not installed"),
        ]
    )
    assert ui.no_table_reason(result) == "numeric share 18%"


def test_a_page_with_no_attempt_at_all_still_gives_a_sentence():
    assert ui.no_table_reason(page_result([])) == "nothing that looks like a table"


def test_the_sample_contents_page_explains_itself():
    """Page 1 of the sample is a list of tables, not a table. The run must say so."""
    result = pipeline.run_page(SAMPLE, 1)
    assert not result.tables
    assert "digits" in ui.no_table_reason(result)


def test_the_figures_publish_the_share_of_numbers_a_check_covered():
    result = pipeline.run_page(SAMPLE, 3)
    block = ui.stats_html([result])
    assert "covered by a check" in block
    assert "observations" in block
    checked = ui._cells_checked([result])
    assert 0 < checked < len(result.long), "a page where every number is checked would hide the point"


def test_large_figures_are_grouped_so_they_can_be_read():
    assert ui._number(6106) == "6\u202f106"
    assert ui._number(53) == "53"


def test_the_figures_are_empty_before_anything_is_read():
    assert ui.stats_html([]) == ""


def test_conformance_is_silent_until_the_files_exist():
    assert ui.conformance_pill({}) == ""


def test_conformance_reports_the_output_of_a_real_run():
    result = pipeline.run_page(SAMPLE, 3)
    files = ui._output_files(SAMPLE, [result])
    pill = ui.conformance_pill(files)
    assert "SDMX-ML 2.1" in pill
    if "not checked" not in pill:
        assert "valid" in pill and "invalid" not in pill


def test_a_table_is_shown_under_the_title_the_report_gives_it():
    result = pipeline.run_page(SAMPLE, 3)
    labels = [slot["label"] for slot in ui.table_slots(result) if slot.get("visible")]
    assert labels[0].startswith("Tableau 03.02")
    assert "read by" in labels[0]


def test_the_stage_line_says_whether_the_vision_model_is_there():
    line = ui.stages_markdown()
    assert "PaddleOCR-VL" in line
    assert "installed" in line


def test_checks_frame_lists_only_what_needs_a_human():
    result = pipeline.run_page(SAMPLE, 3)
    frame = ui.checks_frame([result])
    assert isinstance(frame, pd.DataFrame)
    assert list(frame.columns) == ["page", "check", "row", "column", "detail"]


def test_each_code_is_shown_next_to_the_label_it_stands_for():
    """Scrolling eight columns to check a code against its label defeats the point."""
    result = pipeline.run_page(SAMPLE, 2)
    columns = list(ui.observations_frame(result.long).columns)
    for code in ("REF_AREA", "INDICATOR", "TIME_PERIOD"):
        assert columns.index(f"{code}_LABEL") == columns.index(code) + 1


def test_the_written_files_keep_the_sdmx_column_order():
    """The reordering is for the screen. A file must stay in the order SDMX expects."""
    result = pipeline.run_page(SAMPLE, 2)
    header = ui._output_files(SAMPLE, [result])[f"{SAMPLE.stem}_long.csv"].splitlines()[0]
    assert header.startswith("FREQ,REF_AREA,INDICATOR,COMPOSITE_BREAKDOWN,TIME_PERIOD,OBS_VALUE")


def test_the_observations_say_what_a_row_is_and_how_many():
    result = pipeline.run_page(SAMPLE, 2)
    note = ui.observations_note(result.long)
    assert "observations" in note
    assert "One row per number read" in note


def test_nothing_is_said_about_observations_before_a_run():
    assert ui.observations_note(pd.DataFrame()) == ""


def test_the_unit_printed_under_a_caption_reaches_the_observations():
    """Roughly a third of units are on a line under the caption, not in a column header."""
    headings = pipeline.captions_on_page(SAMPLE, 4)
    assert [unit for _, unit in headings] == ["Nombre", "Nombre"]
    result = pipeline.run_page(SAMPLE, 4)
    assert set(result.long["UNIT_MEASURE"]) == {"NOMBRE"}
