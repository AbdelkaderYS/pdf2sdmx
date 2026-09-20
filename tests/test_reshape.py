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
    assert set(long["INDICATOR"]) == {"SUP", "PROD"}
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
    # The crops name what is measured, not the measure, so they sit in the breakdown and
    # INDICATOR says it could not name a measure rather than inventing one.
    assert set(long["INDICATOR"]) == {"_Z"}
    assert set(long["COMPOSITE_BREAKDOWN"]) == {"MIL", "SOR"}
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
    flagged = long[(long["REF_AREA"] == "NE") & (long["INDICATOR"] == "PROD")]
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
    assert csv.startswith(
        "STRUCTURE,STRUCTURE_ID,ACTION,FREQ,REF_AREA,INDICATOR,COMPOSITE_BREAKDOWN,TIME_PERIOD,OBS_VALUE"
    )
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
    # The total over every region is the country, which has a code of its own.
    assert set(long["REF_AREA"]) == {"NE-1", "NE-6", "NE-8", "NE"}
    # One code per measure, one per crop, instead of one per combination of the two.
    assert set(long["INDICATOR"]) == {"SUP", "PROD"}
    assert set(long["COMPOSITE_BREAKDOWN"]) == {"MIL"}
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
    assert any("VARIATION" in code for code in long["COMPOSITE_BREAKDOWN"])


def test_a_label_that_names_no_known_area_does_not_become_one():
    """Reading an unmatched label as an area is how livestock became a country."""
    frame = pd.DataFrame({"Désignation": ["Bovins", "Ovins"], "2024": ["18133707", "21597536"]})
    long = reshape.to_long(frame, mapping=MAPPING, time_period="2024", unit="", method="m", source="s", checks=[])
    assert set(long["REF_AREA"]) == {"NE"}
    # The species name the thing measured, so they land in the breakdown, and INDICATOR
    # says it named no measure rather than repeating the label.
    assert set(long["COMPOSITE_BREAKDOWN_LABEL"]) == {"Bovins", "Ovins"}
    assert set(long["INDICATOR"]) == {"_Z"}


def test_a_nested_area_label_keeps_only_the_area():
    """A label merged from two printed columns reads "Dosso / Bovins"."""
    code, leftover = reshape._resolve_area("Dosso / Bovins", MAPPING)
    assert code == "NE-3"
    assert leftover == "Bovins"


def test_text_in_parentheses_is_a_unit_only_when_it_is_one():
    assert reshape._unit_for("Rendement (kg/ha)", "", "") == ("KG_HA", "0")
    assert reshape._unit_for("Tranche (1 à 10 m3/mois)", "", "") == ("UNKNOWN", "0")
    assert reshape._unit_for("Minibus (17 à 22 places)", "", "") == ("UNKNOWN", "0")


def test_a_unit_is_found_wherever_the_report_prints_it():
    """In parentheses, after "en" in a title, or after "en" in a column name."""
    assert reshape.unit_and_multiplier("Prix moyens en FCFA du bétail") == ("FCFA", "0")
    assert reshape.unit_and_multiplier("Or en US$/g") == ("USD_G", "0")
    assert reshape.unit_and_multiplier("Taux (glissement annuel en %)") == ("PER", "0")


def test_a_multiplier_is_kept_apart_from_the_unit():
    """SDMX writes "en milliers de m3" as M3 with UNIT_MULT 3, not as one glued code."""
    assert reshape.unit_and_multiplier("en milliers de m³") == ("M3", "3")
    assert reshape.unit_and_multiplier("en millions de FCFA") == ("FCFA", "6")


def test_a_quarter_written_with_a_hyphen_is_still_a_quarter():
    """A separator of a hyphen instead of a space left 35% of dates inside the indicator."""
    assert reshape.sdmx_time_period("1T-22") == "2022-Q1"
    assert reshape.sdmx_time_period("3T-24") == "2024-Q3"
    assert reshape.sdmx_time_period("2024 1T-24r") == "2024-Q1"


def test_a_stock_date_becomes_a_calendar_day():
    """A stock is printed at a date: "31 déc-22" is the day, not the year."""
    assert reshape.sdmx_time_period("31 déc-22") == "2022-12-31"
    assert reshape.sdmx_time_period("30 sept-24") == "2024-09-30"
    assert reshape.sdmx_frequency("2024-09-30") == "D"


def test_the_measure_and_the_thing_measured_go_to_separate_dimensions():
    """One code per measure and one per crop, not one per combination of the two.

    Written as a single code, a table of 500 items by 20 measures needs 10 000 codes and
    none of them repeats. This is the decomposition the SDMX domain modelling guideline
    describes, and the shape the UN SDG structure uses.
    """
    measure, breakdown, unit = reshape._split_indicator("Mil / Superficie", MAPPING)
    assert (measure.code, breakdown.code) == ("SUP", "MIL")
    assert unit == "ha"


def test_a_measure_on_its_own_leaves_the_breakdown_total():
    measure, breakdown, _ = reshape._split_indicator("Production", MAPPING)
    assert (measure.code, breakdown.code) == ("PROD", "_T")


def test_a_label_naming_no_measure_says_so_instead_of_inventing_one():
    """_Z is not a failure to record the label: the breakdown still carries it."""
    measure, breakdown, _ = reshape._split_indicator("Passagers / Trafic national", MAPPING)
    assert measure.code == "_Z"
    assert breakdown.label == "Passagers / Trafic national"


def test_the_engine_runs_on_a_document_it_knows_nothing_about(tmp_path):
    """With an empty vocabulary the numbers must still come out, and the labels with them.

    Extraction is general. Coding is not, and cannot be: no engine knows what a word means
    until it is told. What it must never do is pretend, so every code says _Z or falls back
    to the country, and the printed label is kept beside it.
    """
    empty = tmp_path / "empty.csv"
    empty.write_text("label,code,dimension,unit,note\n")
    frame = pd.DataFrame({"Désignation": ["Quelque chose", "Autre chose"], "2024": ["10", "20"]})
    known = reshape.to_long(frame, mapping=MAPPING, time_period="2024", unit="", method="m", source="s", checks=[])
    unknown = reshape.to_long(
        frame,
        mapping=reshape.load_mapping(empty),
        time_period="2024",
        unit="",
        method="m",
        source="s",
        checks=[],
    )
    assert list(unknown["OBS_VALUE"]) == list(known["OBS_VALUE"])
    assert set(unknown["INDICATOR"]) == {"_Z"}
    assert set(unknown["COMPOSITE_BREAKDOWN_LABEL"]) == {"Quelque chose", "Autre chose"}


def test_a_table_level_failure_marks_every_number_it_produced():
    """A header that swallowed a data row makes every value under it suspect."""
    frame = pd.DataFrame({"Désignation": ["Serie A"], "31 déc.22 63 799": ["10"]})
    long = reshape.to_long(
        frame,
        mapping=MAPPING,
        time_period="2022",
        unit="",
        method="m",
        source="s",
        checks=run_checks(frame),
    )
    assert set(long["OBS_STATUS"]) == {"U"}


def test_a_measure_named_only_in_the_title_is_still_found():
    """A table whose rows are what is counted names the measure in its caption."""
    frame = pd.DataFrame({"Désignation": ["Bovins", "Ovins"], "2024": ["10", "20"]})
    long = reshape.to_long(
        frame,
        mapping=MAPPING,
        time_period="2024",
        unit="",
        method="m",
        source="s",
        checks=[],
        title="Tableau 03.02. : Effectif du cheptel",
    )
    assert set(long["INDICATOR"]) == {"HEADCOUNT"}
    assert set(long["COMPOSITE_BREAKDOWN"]) == {"CATTLE", "SHEEP"}


def test_an_unidentified_measure_says_so_instead_of_leaving_a_blank():
    frame = pd.DataFrame({"Désignation": ["Quelque chose"], "2024": ["10"]})
    long = reshape.to_long(frame, mapping=MAPPING, time_period="2024", unit="", method="m", source="s", checks=[])
    assert long["INDICATOR"].iloc[0] == "_Z"
    assert long["INDICATOR_LABEL"].iloc[0] == "not identified"


def test_a_total_is_not_a_place():
    """The word makes any axis carrying it look geographical, and the periods are lost."""
    columns = ["2020 Total", "2021 Total", "2022 Total", "2023 Total", "2024 1 T24"]
    assert reshape._axis_kind(columns, MAPPING) == "year"
    assert reshape.sdmx_time_period("2020 Total") == "2020"


def test_a_total_over_regions_is_the_country():
    """_T is for a dimension whose total has no name. The total of all regions has one."""
    assert reshape._resolve_area("Ensemble", MAPPING) == ("NE", "")
    assert reshape._resolve_area("Total / Bovins", MAPPING) == ("NE", "Bovins")


def test_a_placeholder_never_becomes_a_code():
    """ "UNKNOWN" is what a caller passes when it has nothing, not something a report printed."""
    measure, breakdown, _ = reshape._split_indicator("UNKNOWN", MAPPING)
    assert (measure.code, breakdown.code) == ("_Z", "_T")


def test_a_period_marked_total_is_still_that_period():
    """Two header rows merge into "2020 Total", which is the year 2020 and not a place."""
    frame = pd.DataFrame(
        {
            "Désignation": ["Serie A"],
            "2020 Total": ["10"],
            "2021 Total": ["20"],
            "2022 Total": ["30"],
            "2023 Total": ["40"],
        }
    )
    long = reshape.to_long(frame, mapping=MAPPING, time_period="2024", unit="", method="m", source="s", checks=[])
    assert set(long["TIME_PERIOD"]) == {"2020", "2021", "2022", "2023"}
    assert set(long["REF_AREA"]) == {"NE"}
