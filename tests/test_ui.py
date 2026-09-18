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


def test_the_summary_publishes_the_share_of_numbers_a_check_covered():
    result = pipeline.run_page(SAMPLE, 3)
    summary = ui.summary_markdown([result])
    assert "observations" in summary
    assert "covered by a check" in summary
    checked = ui._cells_checked([result])
    assert 0 < checked < len(result.long), "a page where every number is checked would hide the point"


def test_the_summary_is_empty_before_anything_is_read():
    assert ui.summary_markdown([]) == ""


def test_conformance_is_silent_until_the_files_exist():
    assert ui.conformance_markdown({}) == ""


def test_conformance_reports_the_output_of_a_real_run():
    result = pipeline.run_page(SAMPLE, 3)
    files = ui._output_files(SAMPLE, [result])
    line = ui.conformance_markdown(files)
    assert line.startswith("**SDMX-ML 2.1:**")
    if "not checked" not in line:
        assert "valid against the official schemas" in line


def test_checks_frame_lists_only_what_needs_a_human():
    result = pipeline.run_page(SAMPLE, 3)
    frame = ui.checks_frame([result])
    assert isinstance(frame, pd.DataFrame)
    assert list(frame.columns) == ["page", "check", "row", "column", "detail"]
