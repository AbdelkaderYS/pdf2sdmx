"""Write the long table as SDMX-CSV 2.0 and SDMX-ML 2.1 (structure and data messages)."""

import pandas as pd

AGENCY = "INS_NE"
DSD_ID = "DSD_PDF2SDMX"
DATAFLOW_ID = "DF_PDF2SDMX"
VERSION = "1.0"

# Same component ids as the World Bank WDI DSD (FREQ, REF_AREA, TIME_PERIOD, UNIT_MULT,
# OBS_VALUE) with INDICATOR where WDI uses SERIES.
DIMENSIONS = ["FREQ", "REF_AREA", "INDICATOR", "TIME_PERIOD"]
ATTRIBUTES = ["UNIT_MEASURE", "UNIT_MULT", "OBS_STATUS", "TIME_PERIOD_LABEL", "EXTRACTION_METHOD", "SOURCE"]
MEASURE = "OBS_VALUE"


def to_sdmx_csv(long: pd.DataFrame) -> str:
    """SDMX-CSV 2.0: three structure columns, then dimensions, measure, attributes."""
    out = long[DIMENSIONS + [MEASURE] + ATTRIBUTES].copy()
    out.insert(0, "ACTION", "I")
    out.insert(0, "STRUCTURE_ID", f"{AGENCY}:{DATAFLOW_ID}({VERSION})")
    out.insert(0, "STRUCTURE", "dataflow")
    return out.to_csv(index=False, lineterminator="\n")


def to_sdmx_ml(long: pd.DataFrame) -> tuple[bytes, bytes]:
    """Returns (structure message, data message) as SDMX-ML 2.1 bytes."""
    import sdmx
    from sdmx.message import DataMessage, StructureMessage
    from sdmx.model.v21 import (
        Agency,
        AttributeValue,
        DataAttribute,
        DataflowDefinition,
        DataSet,
        DataStructureDefinition,
        Dimension,
        Key,
        Observation,
        PrimaryMeasure,
        TimeDimension,
    )

    dsd = DataStructureDefinition(id=DSD_ID, version=VERSION, maintainer=Agency(id=AGENCY))
    dsd.dimensions.append(Dimension(id="FREQ", order=0))
    dsd.dimensions.append(Dimension(id="REF_AREA", order=1))
    dsd.dimensions.append(Dimension(id="INDICATOR", order=2))
    dsd.dimensions.append(TimeDimension(id="TIME_PERIOD", order=3))
    for attr in ATTRIBUTES:
        dsd.attributes.append(DataAttribute(id=attr))
    dsd.measures.append(PrimaryMeasure(id=MEASURE))
    dataflow = DataflowDefinition(id=DATAFLOW_ID, version=VERSION, maintainer=Agency(id=AGENCY), structure=dsd)

    def make_obs(row: pd.Series) -> Observation:
        key = dsd.make_key(Key, {d: str(row[d]) for d in DIMENSIONS})
        attrs = {a.id: AttributeValue(value_for=a, value=str(row[a.id])) for a in dsd.attributes}
        return Observation(dimension=key, attached_attribute=attrs, value_for=dsd.measures[0], value=row[MEASURE])

    observations = [make_obs(row) for _, row in long.iterrows()]
    dataset = DataSet(structured_by=dsd, obs=observations)

    structure_msg = StructureMessage(dataflow={dataflow.id: dataflow}, structure={dsd.id: dsd})
    data_msg = DataMessage(data=[dataset], dataflow=dataflow)
    return sdmx.to_xml(structure_msg, pretty_print=True), sdmx.to_xml(data_msg, pretty_print=True)
