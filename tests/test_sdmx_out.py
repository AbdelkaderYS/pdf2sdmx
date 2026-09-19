"""The SDMX output must be readable by someone who did not write it.

Two independent checks: the official XSD schemas say the files are well formed SDMX-ML
2.1, and sdmx1, another implementation, reads back the same structure and the same
observations. A test that only reads back our own writer would prove nothing.
"""

import pandas as pd
import pytest

from pdf2sdmx.core import sdmx_out

sdmx = pytest.importorskip("sdmx")


def long_table() -> pd.DataFrame:
    """Two observations covering both statuses and two regions."""
    return pd.DataFrame(
        [
            {
                "FREQ": "A",
                "REF_AREA": "NE_AGADEZ",
                "INDICATOR": "AREA_HA",
                "COMPOSITE_BREAKDOWN": "MILLET",
                "TIME_PERIOD": "2024-A1",
                "OBS_VALUE": 1234.0,
                "UNIT_MEASURE": "HA",
                "UNIT_MULT": "0",
                "OBS_STATUS": "A",
                "TIME_PERIOD_LABEL": "2024/2025",
                "EXTRACTION_METHOD": "pdfplumber",
                "SOURCE": "bulletin.pdf",
                "REF_AREA_LABEL": "Agadez",
                "INDICATOR_LABEL": "Superficie",
                "COMPOSITE_BREAKDOWN_LABEL": "Mil",
            },
            {
                "FREQ": "A",
                "REF_AREA": "NE_DIFFA",
                "INDICATOR": "PROD_T",
                "COMPOSITE_BREAKDOWN": "_T",
                "TIME_PERIOD": "2024-A1",
                "OBS_VALUE": 987.0,
                "UNIT_MEASURE": "T",
                "UNIT_MULT": "0",
                "OBS_STATUS": "U",
                "TIME_PERIOD_LABEL": "2024/2025",
                "EXTRACTION_METHOD": "camelot_ml",
                "SOURCE": "bulletin.pdf",
                "REF_AREA_LABEL": "Diffa",
                "INDICATOR_LABEL": "Production",
                "COMPOSITE_BREAKDOWN_LABEL": "",
            },
        ]
    )


def schemas_installed() -> bool:
    """Probe with a real message, because validate() only looks at SDMX root elements."""
    structure_xml, _ = sdmx_out.to_sdmx_ml(long_table())
    return sdmx_out.validate(structure_xml) is not None


requires_schemas = pytest.mark.skipif(
    not schemas_installed(), reason="SDMX 2.1 schemas not installed, run `make schemas`"
)


@requires_schemas
def test_both_messages_pass_the_official_schemas():
    structure_xml, data_xml = sdmx_out.to_sdmx_ml(long_table())
    assert sdmx_out.validate(structure_xml, data_xml) is True


def test_another_implementation_reads_back_the_structure(tmp_path):
    structure_xml, _ = sdmx_out.to_sdmx_ml(long_table())
    path = tmp_path / "structure.xml"
    path.write_bytes(structure_xml)

    message = sdmx.read_sdmx(path)
    dsd = message.structure[sdmx_out.DSD_ID]
    assert [d.id for d in dsd.dimensions] == sdmx_out.DIMENSIONS
    assert [a.id for a in dsd.attributes] == sdmx_out.ATTRIBUTES
    assert [m.id for m in dsd.measures] == [sdmx_out.MEASURE]
    assert sorted(message.codelist) == sorted(sdmx_out.CODED.values())


def test_another_implementation_reads_back_every_observation(tmp_path):
    long = long_table()
    _, data_xml = sdmx_out.to_sdmx_ml(long)
    path = tmp_path / "data.xml"
    path.write_bytes(data_xml)

    observations = sdmx.read_sdmx(path).data[0].obs
    assert len(observations) == len(long)
    first = observations[0]
    assert str(first.dimension["REF_AREA"].value) == "NE_AGADEZ"
    assert float(first.value) == 1234.0
    assert str(first.attrib["OBS_STATUS"].value) == "A"


def test_codelists_cover_every_value_used():
    long = long_table()
    lists = sdmx_out.codelists(long)
    for component, list_id in sdmx_out.CODED.items():
        for value in long[component]:
            assert value in lists[list_id], f"{value} missing from {list_id}"


def test_codelists_name_codes_from_the_label_column():
    lists = sdmx_out.codelists(long_table())
    assert lists["CL_REF_AREA"]["NE_AGADEZ"] == "Agadez"
    assert lists["CL_OBS_STATUS"]["U"] == "Low reliability"


def test_sdmx_csv_starts_with_the_three_structure_columns():
    header = sdmx_out.to_sdmx_csv(long_table()).splitlines()[0].split(",")
    assert header[:3] == ["STRUCTURE", "STRUCTURE_ID", "ACTION"]
    assert header[3 : 3 + len(sdmx_out.DIMENSIONS)] == sdmx_out.DIMENSIONS
    assert header[3 + len(sdmx_out.DIMENSIONS)] == sdmx_out.MEASURE


@requires_schemas
def test_validate_rejects_a_message_that_is_not_sdmx():
    assert sdmx_out.validate(b"<NotAnSdmxMessage/>") is False
