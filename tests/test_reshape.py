import io

import pandas as pd

from pdf2sdmx.config import settings
from pdf2sdmx.core import reshape, sdmx_out
from pdf2sdmx.core.validate import Check, run_checks

MAPPING = reshape.load_mapping(settings.mapping_file)


def regions_by_indicator():
    return pd.DataFrame(
        {
            "Région": ["Maradi", "Tillabery", "Niger"],
            "Superficie (ha)": ["400 000", "300 000", "700 000"],
            "Production (t)": ["240 000", "150 000", "390 000"],
        }
    )


def test_regions_get_iso_codes_and_units_come_from_headers():
    long = reshape.to_long(
        regions_by_indicator(),
        mapping=MAPPING,
        time_period="2024/2025",
        unit="",
        method="pdfplumber",
        source="x.pdf",
        checks=[],
    )
    assert len(long) == 6
    assert set(long["REF_AREA"]) == {"NE-4", "NE-6", "NE"}
    assert set(long["INDICATOR"]) == {"AREA_HA", "PROD_T"}
    assert set(long["UNIT_MEASURE"]) == {"HA", "T"}  # units are SDMX codes, so upper case
    assert (long["TIME_PERIOD"] == "2024-A1").all()  # SDMX reporting year, start year
    assert (long["TIME_PERIOD_LABEL"] == "2024/2025").all()
    assert (long["FREQ"] == "A").all() and (long["UNIT_MULT"] == "0").all()


def test_years_in_header_become_time_period():
    frame = pd.DataFrame({"Produit": ["Mil", "Sorgho"], "2020": ["100", "200"], "2021": ["110", "-"]})
    long = reshape.to_long(
        frame,
        mapping=MAPPING,
        time_period="",
        unit="t",
        method="pdfplumber",
        source="x.pdf",
        checks=[],
    )
    assert len(long) == 3  # the dash is a missing marker and is dropped
    assert set(long["TIME_PERIOD"]) == {"2020", "2021"}
    assert set(long["INDICATOR"]) == {"MILLET", "SORGHUM"}
    assert (long["REF_AREA"] == "NE").all()


def test_failed_check_flags_observation_status():
    frame = regions_by_indicator()
    checks = [Check("column_total", "fail", "bad", row="Niger", column="Production (t)")]
    long = reshape.to_long(
        frame,
        mapping=MAPPING,
        time_period="2024",
        unit="",
        method="m",
        source="s",
        checks=checks,
    )
    flagged = long[(long["REF_AREA"] == "NE") & (long["INDICATOR"] == "PROD_T")]
    assert flagged["OBS_STATUS"].iloc[0] == "U"  # CL_OBS_STATUS: low reliability
    assert (long.drop(flagged.index)["OBS_STATUS"] == "A").all()


def test_sdmx_csv_and_ml_round_trip():
    import sdmx

    frame = regions_by_indicator()
    long = reshape.to_long(
        frame,
        mapping=MAPPING,
        time_period="2024",
        unit="",
        method="m",
        source="s",
        checks=run_checks(frame),
    )
    csv = sdmx_out.to_sdmx_csv(long)
    assert csv.startswith("STRUCTURE,STRUCTURE_ID,ACTION,FREQ,REF_AREA,INDICATOR,TIME_PERIOD,OBS_VALUE")
    assert csv.count("\n") == len(long) + 1

    structure_xml, data_xml = sdmx_out.to_sdmx_ml(long)
    structure = sdmx.read_sdmx(io.BytesIO(structure_xml))
    assert sdmx_out.DSD_ID in structure.structure
    data = sdmx.read_sdmx(io.BytesIO(data_xml), structure=structure.structure[sdmx_out.DSD_ID])
    assert sum(len(ds.obs) for ds in data.data) == len(long)


def test_regions_in_upper_case_columns_become_ref_area():
    frame = pd.DataFrame(
        {
            "PRODUIT / col": ["Mil / Superficie", "Mil / Production"],
            "AGADEZ": ["297", "141"],
            "TILLABERY": ["1 252 813", "578 250"],
            "NIAMEY.": ["18 682", "9 698"],
            "ENSEMBLE": ["6 804 476", "3 836 013"],
        }
    )
    long = reshape.to_long(
        frame,
        mapping=MAPPING,
        time_period="2024/2025",
        unit="",
        method="m",
        source="s",
        checks=[],
    )
    assert set(long["REF_AREA"]) == {"NE-1", "NE-6", "NE-8", "_T"}
    assert set(long["INDICATOR"]) == {"MILLET_AREA_HA", "MILLET_PROD_T"}
    assert set(long["UNIT_MEASURE"]) == {"HA", "T"}  # units are SDMX codes, so upper case


def test_run_page_accepts_none_options(tmp_path):
    """Gradio sends None for an empty textbox. run_page must not crash on it."""
    from pdf2sdmx.core import pipeline

    sample = settings.data_raw.parent / "samples" / "ins_bulletin_3T25_p20-23.pdf"
    result = pipeline.run_page(sample, 1, time_period=None, unit=None, subject=None)
    assert result.page == 1


def test_sdmx_time_period_follows_the_reporting_year_convention():
    assert reshape.sdmx_time_period("2024") == "2024"
    assert reshape.sdmx_time_period("2024/2025") == "2024-A1"
    assert reshape.sdmx_time_period("2023-2024") == "2023-A1"
    assert reshape.sdmx_time_period("UNKNOWN") == "UNKNOWN"


def test_sdmx_code_keeps_characters_the_standard_allows():
    """ISO region codes and the SDMX total code must survive untouched."""
    assert reshape.sdmx_code("NE-1") == "NE-1"
    assert reshape.sdmx_code("_T") == "_T"


def test_sdmx_code_replaces_characters_the_standard_forbids():
    assert reshape.sdmx_code("kg/ha") == "KG_HA"
    assert reshape.sdmx_code("(kg/ha)") == "KG_HA"
    assert reshape.sdmx_code("Mil / Superficie") == "MIL_SUPERFICIE"


def test_sdmx_code_never_returns_an_empty_id():
    assert reshape.sdmx_code("") == "UNKNOWN"
    assert reshape.sdmx_code("   ") == "UNKNOWN"


def test_a_quarter_becomes_an_sdmx_quarter_and_carries_its_frequency():
    assert reshape.sdmx_time_period("1 T24") == "2024-Q1"
    assert reshape.sdmx_time_period("T1 2024") == "2024-Q1"
    assert reshape.sdmx_frequency("2024-Q1") == "Q"
    assert reshape.sdmx_frequency("2024-A1") == "A"


def test_a_footnote_marker_does_not_spoil_a_period():
    """A printed period often carries a provisional or revised marker."""
    assert reshape.sdmx_time_period("2010*") == "2010"
    assert reshape.sdmx_time_period("2023 (p)") == "2023"
    assert reshape.sdmx_time_period("2024/2025*") == "2024-A1"


def test_a_marker_on_its_own_is_left_alone():
    """Stripping everything would invent a period out of nothing."""
    assert reshape.sdmx_time_period("(p)") == "(p)"


def test_a_revision_marker_on_a_quarter_is_dropped():
    assert reshape.sdmx_time_period("2 T23r") == "2023-Q2"


def test_a_merged_two_level_header_keeps_the_quarter():
    """Two header rows join into one name that says the same period twice."""
    assert reshape.sdmx_time_period("2021 1 T21") == "2021-Q1"


def test_a_column_that_is_not_a_period_stays_an_indicator():
    """A table of periods often ends with a variation column. It is not a period."""
    frame = pd.DataFrame(
        {
            "Désignation": ["Serie A", "Serie B"],
            "2022": ["10", "20"],
            "2023": ["12", "24"],
            "Variation en glissement annuel (%)": ["20", "20"],
        }
    )
    long = reshape.to_long(frame, mapping=MAPPING, time_period="2023", unit="", method="m", source="s", checks=[])
    periods = set(long["TIME_PERIOD"])
    assert periods == {"2022", "2023"}
    assert any("VARIATION" in code for code in long["INDICATOR"])
