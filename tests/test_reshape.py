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
    assert set(long["UNIT_MEASURE"]) == {"ha", "t"}
    assert (long["TIME_PERIOD"] == "2024/2025").all()


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
    assert flagged["OBS_STATUS"].iloc[0] == "E"
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
    assert csv.startswith("STRUCTURE,STRUCTURE_ID,ACTION,REF_AREA,INDICATOR,TIME_PERIOD,OBS_VALUE")
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
    assert set(long["UNIT_MEASURE"]) == {"ha", "t"}
