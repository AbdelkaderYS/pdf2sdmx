"""Write the long table as SDMX-CSV 2.0 and SDMX-ML 2.1.

The XML is built element by element rather than through a library. The two messages we
emit are fixed, one data structure definition with its code lists and one generic data
set, so writing them out directly keeps every required attribute visible. It also makes
the conformance test meaningful: the same bytes go through the official XSD schemas in
tests/test_sdmx_out.py.

The data message uses the generic format rather than the structure specific one. Generic
data validates against the published schemas on its own; structure specific data needs a
schema generated from the DSD, which a receiver would have to build first.
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd

from pdf2sdmx.config import settings

MESSAGE = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
STRUCTURE = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
COMMON = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"
GENERIC = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

AGENCY = settings.agency
AGENCY_NAME = settings.agency_name
DSD_ID = "DSD_PDF2SDMX"
DATAFLOW_ID = "DF_PDF2SDMX"
CONCEPTS_ID = "CS_PDF2SDMX"
VERSION = "1.0"
DSD_NAME = "Tables read from a statistical report"
DATAFLOW_NAME = "Tables extracted from a statistical report"

# Same component ids as the World Bank WDI DSD (FREQ, REF_AREA, TIME_PERIOD, UNIT_MULT,
# OBS_VALUE) with INDICATOR where WDI uses SERIES.
# INDICATOR carries the measure and COMPOSITE_BREAKDOWN what it is measured on, the way
# the UN SDG structure separates a series from the breakdowns applied to it. Writing both
# in one dimension gives a code list with one entry per combination.
DIMENSIONS = ["FREQ", "REF_AREA", "INDICATOR", "COMPOSITE_BREAKDOWN", "TIME_PERIOD"]
ATTRIBUTES = ["UNIT_MEASURE", "UNIT_MULT", "OBS_STATUS", "TIME_PERIOD_LABEL", "EXTRACTION_METHOD", "SOURCE"]
MEASURE = "OBS_VALUE"

# Components listed here take their values from a code list. The others carry free text.
CODED = {
    "FREQ": "CL_FREQ",
    "REF_AREA": "CL_REF_AREA",
    "INDICATOR": "CL_INDICATOR",
    "COMPOSITE_BREAKDOWN": "CL_COMPOSITE_BREAKDOWN",
    "UNIT_MEASURE": "CL_UNIT_MEASURE",
    "OBS_STATUS": "CL_OBS_STATUS",
}

# Every coded dimension needs these two, and neither is a value found in the data.
SHARED_CODES = {"_T": "Total", "_Z": "Not identified"}

# Components whose values belong to a list maintained by the SDMX Technical Working Group.
# The registry copy is preferred; what follows is the fallback when it is not on disk.
OFFICIAL = {"CL_FREQ": "CL_FREQ", "CL_OBS_STATUS": "CL_OBS_STATUS"}

FIXED_CODES = {
    "CL_FREQ": {"A": "Annual", "Q": "Quarterly", "M": "Monthly", "D": "Daily"},
    "CL_OBS_STATUS": {
        "A": "Normal value",
        "E": "Estimated value",
        "U": "Low reliability",
        "M": "Missing value",
    },
}

CODELIST_NAMES = {
    "CL_FREQ": "Frequency",
    "CL_REF_AREA": "Reference area",
    "CL_INDICATOR": "Indicator",
    "CL_COMPOSITE_BREAKDOWN": "Composite breakdown",
    "CL_UNIT_MEASURE": "Unit of measure",
    "CL_OBS_STATUS": "Observation status",
}

# Column of the long table holding a readable name for a coded value.
LABEL_COLUMN = {
    "REF_AREA": "REF_AREA_LABEL",
    "INDICATOR": "INDICATOR_LABEL",
    "COMPOSITE_BREAKDOWN": "COMPOSITE_BREAKDOWN_LABEL",
}

CONCEPT_NAMES = {
    "FREQ": "Frequency",
    "REF_AREA": "Reference area",
    "INDICATOR": "Indicator",
    "COMPOSITE_BREAKDOWN": "Composite breakdown",
    "TIME_PERIOD": "Time period",
    "OBS_VALUE": "Observation value",
    "UNIT_MEASURE": "Unit of measure",
    "UNIT_MULT": "Unit multiplier",
    "OBS_STATUS": "Observation status",
    "TIME_PERIOD_LABEL": "Time period as printed",
    "EXTRACTION_METHOD": "Extraction stage",
    "SOURCE": "Source document",
}


def to_sdmx_csv(long: pd.DataFrame) -> str:
    """SDMX-CSV 2.0: three structure columns, then dimensions, measure, attributes."""
    out = long[DIMENSIONS + [MEASURE] + ATTRIBUTES].copy()
    out.insert(0, "ACTION", "I")
    out.insert(0, "STRUCTURE_ID", f"{AGENCY}:{DATAFLOW_ID}({VERSION})")
    out.insert(0, "STRUCTURE", "dataflow")
    return out.to_csv(index=False, lineterminator="\n")


def to_sdmx_ml(long: pd.DataFrame) -> tuple[bytes, bytes]:
    """Returns (structure message, data message) as SDMX-ML 2.1 bytes."""
    return _structure_message(codelists(long)), _data_message(long)


def validate(*messages: bytes) -> bool | None:
    """True when every message passes the official SDMX-ML 2.1 schemas.

    Returns None when the schemas are not on this machine, so a caller can say "not
    checked" instead of "invalid". Install them once with `make schemas`.
    """
    import sdmx

    with tempfile.TemporaryDirectory() as directory:
        for number, message in enumerate(messages):
            path = Path(directory) / f"message_{number}.xml"
            path.write_bytes(message)
            try:
                if not sdmx.validate_xml(path, version="2.1"):
                    return False
            except FileNotFoundError:  # the schemas are not installed
                return None
            except NotImplementedError:  # the root element is not an SDMX message
                return False
    return True


def codelists(long: pd.DataFrame) -> dict[str, dict[str, str]]:
    """One code list per coded component: code to readable name.

    Fixed lists keep their published codes. The others hold the codes actually used in
    this run, named from the label column when the mapping provided one.
    """
    lists: dict[str, dict[str, str]] = {}
    for component, list_id in CODED.items():
        if list_id in FIXED_CODES:
            lists[list_id] = _official_or_fallback(list_id)
            continue
        codes: dict[str, str] = {}
        label_column = LABEL_COLUMN.get(component, "")
        for code, name in SHARED_CODES.items():
            codes[code] = name
        if component not in long.columns:  # a partial frame still gets a usable list
            lists[list_id] = codes
            continue
        for _, row in long.iterrows():
            code = str(row[component])
            label = str(row[label_column]) if label_column and label_column in long.columns else ""
            codes.setdefault(code, label or code)
        lists[list_id] = codes
    return lists


def _official_or_fallback(list_id: str) -> dict[str, str]:
    """The registry's own list when it is on disk, otherwise the few codes written here."""
    from pdf2sdmx.core import registry

    official = registry.codelist(OFFICIAL[list_id]) if list_id in OFFICIAL else None
    return dict(official or FIXED_CODES[list_id])


def codes_outside_official_lists(long: pd.DataFrame) -> dict[str, set[str]] | None:
    """Values we wrote that no official list contains, per component.

    An empty dict is the answer worth having: every value in those components exists in a
    list this repository does not maintain. None means no official list was available, so
    nothing was checked, which is not the same as passing.
    """
    from pdf2sdmx.core import registry

    checked = False
    outside: dict[str, set[str]] = {}
    for component, list_id in CODED.items():
        if list_id not in OFFICIAL or component not in long.columns:
            continue
        unknown = registry.unknown_codes(set(long[component].astype(str)), OFFICIAL[list_id])
        if unknown is None:
            continue
        checked = True
        if unknown:
            outside[component] = unknown
    return outside if checked else None


def _structure_message(lists: dict[str, dict[str, str]]) -> bytes:
    root = _root("Structure")
    _header(root)
    structures = ET.SubElement(root, f"{{{MESSAGE}}}Structures")

    # The schema fixes this order: dataflows, then code lists, then concepts, then DSDs.
    dataflows = ET.SubElement(structures, f"{{{STRUCTURE}}}Dataflows")
    dataflow = _maintainable(dataflows, "Dataflow", DATAFLOW_ID, DATAFLOW_NAME)
    _ref(ET.SubElement(dataflow, f"{{{STRUCTURE}}}Structure"), DSD_ID, "datastructure", "DataStructure")

    codelists_element = ET.SubElement(structures, f"{{{STRUCTURE}}}Codelists")
    for list_id, codes in lists.items():
        codelist = _maintainable(codelists_element, "Codelist", list_id, CODELIST_NAMES[list_id])
        for code, name in codes.items():
            _name(ET.SubElement(codelist, f"{{{STRUCTURE}}}Code", id=code), name)

    concepts = ET.SubElement(structures, f"{{{STRUCTURE}}}Concepts")
    scheme = _maintainable(concepts, "ConceptScheme", CONCEPTS_ID, "Concepts used by the extracted tables")
    for concept_id, name in CONCEPT_NAMES.items():
        _name(ET.SubElement(scheme, f"{{{STRUCTURE}}}Concept", id=concept_id), name)

    data_structures = ET.SubElement(structures, f"{{{STRUCTURE}}}DataStructures")
    dsd = _maintainable(data_structures, "DataStructure", DSD_ID, DSD_NAME)
    _components(ET.SubElement(dsd, f"{{{STRUCTURE}}}DataStructureComponents"))
    return _render(root)


def _components(parent: ET.Element) -> None:
    """Dimensions, then attributes, then the measure, in the order the schema requires."""
    # The schema fixes these three ids, they are not free names.
    dimension_list = ET.SubElement(parent, f"{{{STRUCTURE}}}DimensionList", id="DimensionDescriptor")
    for position, dimension_id in enumerate(DIMENSIONS, 1):
        is_time = dimension_id == "TIME_PERIOD"
        tag = "TimeDimension" if is_time else "Dimension"
        dimension = ET.SubElement(dimension_list, f"{{{STRUCTURE}}}{tag}", id=dimension_id, position=str(position))
        _concept_identity(dimension, dimension_id)
        if is_time:
            # A time dimension must state its representation; the schema has no default.
            representation = ET.SubElement(dimension, f"{{{STRUCTURE}}}LocalRepresentation")
            ET.SubElement(representation, f"{{{STRUCTURE}}}TextFormat", textType="ObservationalTimePeriod")
        else:
            _enumeration(dimension, dimension_id)

    attribute_list = ET.SubElement(parent, f"{{{STRUCTURE}}}AttributeList", id="AttributeDescriptor")
    for attribute_id in ATTRIBUTES:
        # Conditional: the attribute may be absent from an observation.
        attribute = ET.SubElement(
            attribute_list, f"{{{STRUCTURE}}}Attribute", id=attribute_id, assignmentStatus="Conditional"
        )
        _concept_identity(attribute, attribute_id)
        _enumeration(attribute, attribute_id)
        relationship = ET.SubElement(attribute, f"{{{STRUCTURE}}}AttributeRelationship")
        # Every attribute is carried by the observation itself, not by a dimension or a group.
        measure_ref = ET.SubElement(relationship, f"{{{STRUCTURE}}}PrimaryMeasure")
        _local_ref(measure_ref, MEASURE)

    measure_list = ET.SubElement(parent, f"{{{STRUCTURE}}}MeasureList", id="MeasureDescriptor")
    measure = ET.SubElement(measure_list, f"{{{STRUCTURE}}}PrimaryMeasure", id=MEASURE)
    _concept_identity(measure, MEASURE)
    # Say the observation value is a number, so a reader does not have to guess.
    representation = ET.SubElement(measure, f"{{{STRUCTURE}}}LocalRepresentation")
    ET.SubElement(representation, f"{{{STRUCTURE}}}TextFormat", textType="Double")


def _concept_identity(parent: ET.Element, concept_id: str) -> None:
    element = ET.SubElement(parent, f"{{{STRUCTURE}}}ConceptIdentity")
    _item_ref(element, concept_id, CONCEPTS_ID, "conceptscheme", "Concept")


def _enumeration(parent: ET.Element, component_id: str) -> None:
    """Point a coded component at its code list. Uncoded components get nothing."""
    list_id = CODED.get(component_id)
    if list_id is None:
        return
    representation = ET.SubElement(parent, f"{{{STRUCTURE}}}LocalRepresentation")
    _ref(ET.SubElement(representation, f"{{{STRUCTURE}}}Enumeration"), list_id, "codelist", "Codelist")


def _data_message(long: pd.DataFrame) -> bytes:
    root = _root("GenericData")
    header = _header(root)
    structure = ET.SubElement(
        header,
        f"{{{MESSAGE}}}Structure",
        structureID=DSD_ID,
        dimensionAtObservation="AllDimensions",
    )
    _ref(ET.SubElement(structure, f"{{{COMMON}}}Structure"), DSD_ID, "datastructure", "DataStructure")

    dataset = ET.SubElement(root, f"{{{MESSAGE}}}DataSet", structureRef=DSD_ID, action="Replace")
    for _, row in long.iterrows():
        observation = ET.SubElement(dataset, f"{{{GENERIC}}}Obs")
        key = ET.SubElement(observation, f"{{{GENERIC}}}ObsKey")
        for dimension_id in DIMENSIONS:
            ET.SubElement(key, f"{{{GENERIC}}}Value", id=dimension_id, value=str(row[dimension_id]))
        ET.SubElement(observation, f"{{{GENERIC}}}ObsValue", value=str(row[MEASURE]))
        attributes = ET.SubElement(observation, f"{{{GENERIC}}}Attributes")
        for attribute_id in ATTRIBUTES:
            ET.SubElement(attributes, f"{{{GENERIC}}}Value", id=attribute_id, value=str(row[attribute_id]))
    return _render(root)


def _root(tag: str) -> ET.Element:
    for prefix, uri in (("mes", MESSAGE), ("str", STRUCTURE), ("com", COMMON), ("gen", GENERIC)):
        ET.register_namespace(prefix, uri)
    return ET.Element(f"{{{MESSAGE}}}{tag}")


def _header(root: ET.Element) -> ET.Element:
    """The four fields every SDMX-ML 2.1 message header must carry, in order."""
    header = ET.SubElement(root, f"{{{MESSAGE}}}Header")
    ET.SubElement(header, f"{{{MESSAGE}}}ID").text = f"{DATAFLOW_ID}_{_now():%Y%m%d%H%M%S}"
    ET.SubElement(header, f"{{{MESSAGE}}}Test").text = "false"
    ET.SubElement(header, f"{{{MESSAGE}}}Prepared").text = _now().strftime("%Y-%m-%dT%H:%M:%S")
    ET.SubElement(header, f"{{{MESSAGE}}}Sender", id=AGENCY)
    return header


def _maintainable(parent: ET.Element, tag: str, object_id: str, name: str) -> ET.Element:
    """A dataflow, code list, concept scheme or DSD, with the attributes the schema wants."""
    element = ET.SubElement(
        parent,
        f"{{{STRUCTURE}}}{tag}",
        id=object_id,
        agencyID=AGENCY,
        version=VERSION,
        isFinal="false",
        isExternalReference="false",
    )
    _name(element, name)
    return element


def _name(parent: ET.Element, text: str) -> None:
    ET.SubElement(parent, f"{{{COMMON}}}Name", **{XML_LANG: "en"}).text = text


def _ref(parent: ET.Element, object_id: str, package: str, object_class: str) -> None:
    """A reference to a maintainable object. The Ref element carries no namespace."""
    ET.SubElement(
        parent,
        "Ref",
        id=object_id,
        agencyID=AGENCY,
        version=VERSION,
        package=package,
        **{"class": object_class},
    )


def _local_ref(parent: ET.Element, object_id: str) -> None:
    """A reference to a component of the same DSD. Only the id is allowed here."""
    ET.SubElement(parent, "Ref", id=object_id)


def _item_ref(parent: ET.Element, item_id: str, scheme_id: str, package: str, object_class: str) -> None:
    """A reference to an item inside a scheme. It names its parent instead of a version."""
    ET.SubElement(
        parent,
        "Ref",
        id=item_id,
        maintainableParentID=scheme_id,
        maintainableParentVersion=VERSION,
        agencyID=AGENCY,
        package=package,
        **{"class": object_class},
    )


def _render(root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _now() -> datetime:
    return datetime.now(UTC)
