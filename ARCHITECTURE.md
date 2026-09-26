# Architecture

Technical choices, thresholds and tool names. The README says what the project does and
how well. This file says how.

## The chain

```
PDF page
  -> stage 1  pdfplumber        text layer, ruling lines then whitespace alignment
  -> stage 2  Camelot 2.0 ml    Table Transformer finds rows and columns, text from the PDF
  -> stage 3  PaddleOCR-VL 1.6  vision language model, page read as an image, flagged for review
  -> gate     quality.gate_table
  -> checks   validate.run_checks
  -> long     reshape.to_long   one observation per row, codes from mapping/labels_to_codes.csv
  -> out      sdmx_out          SDMX-CSV 2.0, SDMX-ML 2.1 structure and data messages
```

A page may hold several tables; every stage returns all of them. The cascade stops early at
a stage whose tables all pass the gate with zero failed checks. Otherwise every stage runs,
the stage with the most accepted tables (then the fewest failed checks) wins, and each of
its tables is repaired from the matching table of another stage (see below). If no stage
passes the gate, the best attempt is still shown, labelled `manual`, and nothing is trusted.

Tables are matched across stages by identical data column headers and the number of cells
that read the same number. Two tables on one page often share headers and row labels
(livestock 2024 and 2025 on the same page), so labels alone would pair them wrongly.

## Row repair (`ingest/cascade.py: repair_rows`)

Measured on the 3T 2025 bulletin: pdfplumber glues the last three rows of the page into
one (8 unreadable cells), Camelot ml reads those rows correctly but merges label cells
elsewhere (40 failed checks). Neither stage alone gets the page. So the winner keeps its
frame and, row by row, a donor row with the same label replaces:

- a row with unreadable cells, if the table-level failed-check count goes down;
- a row with gaps, if the donor has a number where the winner has none and the count does
  not go up.

In both cases a donor row that disagrees on any cell both stages read as a number is
refused. Arithmetic consistency alone is not enough: a row from the wrong table on the
same page can be consistent with its own total.

Donor rows must share the data column headers. `EXTRACTION_METHOD` is written per
observation, so a repaired page reads `pdfplumber` on most rows and `camelot_ml` on the
repaired ones. That page now needs no repair: stage 1 reads its 94 truth cells on its own,
since rows printed without a rule between them are split back at reading (`table.unstack_rows`).

## Why these three stages

| Stage | Tool | Decisive property | Source |
|---|---|---|---|
| 1 | pdfplumber 0.11 | Reads the text layer. Deterministic, no model, no cost. | pdfplumber docs |
| 2 | Camelot 2.0.0 (June 2026), `flavor="ml"` | Table Transformer (`microsoft/table-transformer-detection` and `table-transformer-structure-recognition-v1.1-all`) supplies the structure, cell text comes from the PDF text layer, so the model cannot alter a value. `parsing_report` gives a per-table score. | camelot release notes v2.0.0 |
| 3 | PaddleOCR-VL 1.6 (Baidu, 0.9B parameters, Apache 2.0) | Highest score on OmniDocBench v1.6 (96.33) among open document parsers; reads the page as an image, so it also handles scans. Only stage that can misread a digit, so its output is always flagged. Slow on CPU, so the cascade reaches it only when the text stages fail. | PaddleOCR-VL-1.6 model card and usage guide, paddleocr.ai |

Not used, and why:

- MinerU 3.x scores close to PaddleOCR-VL on OmniDocBench but its VLM backends need a GPU
  and its `pipeline` backend downloads several models.
- Docling (IBM, TableFormer) was the first choice for stage 3; replaced by PaddleOCR-VL 1.6
  for its benchmark lead and its licence. Docling would still be a reasonable stage 3 on
  a machine without the Paddle runtime.
- Stage 3 is not installed on the deployed service: the Paddle runtime plus the model is
  heavy for the free tier, and the two sample bulletins have a text layer.

## Gate thresholds (`core/quality.py`)

| Rule | Value | Why |
|---|---|---|
| minimum size | 2 rows x 2 columns | anything smaller is a caption, not a table |
| numeric share of body cells | >= 50% | INS tables are mostly numbers; a text block is not a table |
| unreadable share of body cells | <= 15% | above that the columns are probably shifted |

Score = numeric share x (1 - unreadable share). Used only to break ties between stages
with the same number of failed checks.

## Checks (`core/validate.py`)

| Check | Rule | Tolerance | On failure |
|---|---|---|---|
| unreadable | every body cell parses as a number or a missing marker | none | fail, cell listed |
| bounds | no value above 1e9; negatives only in rate columns | none | fail or warn |
| column_total | a row named Total, Ensemble, Niger or National equals the sum of the others | 0.5% | fail, or warn if the column header looks like a rate |
| row_total | a column named Total equals the sum of the other columns | 0.5% | fail |
| area_x_yield | production (t) = area (ha) x yield (kg/ha) / 1000 | 2% | fail |
| year_jump | adjacent years in one table differ by less than a factor of 5 | none | warn |
| period_jump | same area and indicator across editions differ by less than a factor of 5 (refresh only) | none | warn, listed in `data/processed/to_review.csv` |

Every failed cell sets `OBS_STATUS = E` on its observation and goes to `to_review.csv`.
Nothing is dropped silently.

## Number parsing (`core/numbers.py`)

INS prints thousands with a regular, non-breaking or narrow space and decimals with a comma.
Dots are treated as thousands separators only in the pattern `1.234.567`. A single dot
followed by three digits, as in `1.234`, is also read as thousands. This is a documented
ambiguity; INS does not print decimal dots.

## Long format and SDMX

Columns: `FREQ, REF_AREA, INDICATOR, TIME_PERIOD, OBS_VALUE, UNIT_MEASURE, UNIT_MULT,
OBS_STATUS, TIME_PERIOD_LABEL, EXTRACTION_METHOD, SOURCE`, plus the two printed labels for
traceability.

### Conventions checked against published DSDs (2026-09-16)

| Component | World Bank WDI DSD `WB:WDI(1.0)` | pdf2sdmx | Status |
|---|---|---|---|
| Dimensions | `FREQ, SERIES, REF_AREA, TIME_PERIOD` | `FREQ, REF_AREA, INDICATOR, TIME_PERIOD` | verified, read from api.worldbank.org/v2/sdmx/rest |
| Measure | `OBS_VALUE` | `OBS_VALUE` | verified |
| Attributes | `UNIT_MULT` | `UNIT_MEASURE, UNIT_MULT, OBS_STATUS, TIME_PERIOD_LABEL, EXTRACTION_METHOD, SOURCE` | verified for WDI; the extra ones are SDMX cross-domain concepts plus two provenance attributes |
| OBS_STATUS | not used in WDI | `A` normal, `U` low reliability (failed a check) | codes verified in CL_OBS_STATUS 2.3 from registry.sdmx.org. `E` means estimated and is not used |
| TIME_PERIOD | calendar year `2024` | `2024` for a year, `2024-A1` for a campaign printed `2024/2025` | SDMX reporting-year format, the year the period starts. The printed text is kept in `TIME_PERIOD_LABEL` |
| REF_AREA | ISO 3166-1 alpha-3 (`NER`) | ISO 3166-1 alpha-2 (`NE`) and ISO 3166-2 (`NE-1` to `NE-8`), `_T` for totals | the SDMX cross-domain `CL_AREA` uses alpha-2; regions have no WDI equivalent |
| Indicator codes | WDI series ids such as `AG.PRD.CREL.MT` | `MILLET_PROD_T`, from `mapping/labels_to_codes.csv` | local codes; to be replaced by the INS DSD when one exists |

African Development Bank and INS Niger portals, checked by the project owner in a browser
on 2026-09-16 (the endpoints refuse scripted access):

- `https://dataportal.opendataforafrica.org/api/1.0/sdmx` and
  `https://niger.opendataforafrica.org/api/1.0/sdmx` answer with an SDMX-ML **2.0** structure
  message (namespaces `.../SDMXML/schemas/v2_0/...`), sender `Knoema`.
- The message lists one `KeyFamily` (the SDMX 2.0 name for a DSD) per dataset, with an
  opaque id and a name. Niger datasets relevant here: `nxlreub` Production Agricole,
  `wvovabg` Statistiques de l'Elevage, `kfvhzqc` Données sur l'Agriculture.
- The dimension names inside those KeyFamilies were not read yet. Next step for question
  Q4 of the brief: open `https://niger.opendataforafrica.org/api/1.0/sdmx/nxlreub` and
  compare its dimensions and codelists with `REF_AREA`, `INDICATOR`, `TIME_PERIOD` here.
- pdf2sdmx writes SDMX-ML 2.1 and SDMX-CSV 2.0. SDMX 2.1 is the version the World Bank API
  serves and the one sdmx1 writes; the Knoema portals still speak 2.0. ODP 2.0 is announced
  as SDMX native (Addis Ababa workshop, July 2025); its version was not verified.

Units are written as printed (`ha`, `kg/ha`, `t`); the SDMX `CL_UNIT_MEASURE` codes were not
verified and are not claimed.

Which axis holds what:

| Row labels look like regions | Column headers are years | REF_AREA | INDICATOR | TIME_PERIOD |
|---|---|---|---|---|
| yes | no | row | column header | user or page text |
| yes | yes | row | table subject | column |
| no | yes | NE | row | column |
| no | no | NE | row / column | user or page text |

Region codes are ISO 3166-2:NE. Totals use the SDMX `_T` convention. The mapping file
`mapping/labels_to_codes.csv` is matched exactly first, then fuzzily (rapidfuzz WRatio,
cutoff 88). An unknown label gets an upper-case slug, never a guessed code.

The DSD is built on the fly with sdmx1 (`INS_NE:DSD_PDF2SDMX(1.0)`). No official INS DSD
was available when this was written; see BRIEF.md question Q4.

## Layering

`core` has no import from `api` or `ui`. Both call `core.pipeline.run_page`.

Deviation from the shared standard: the deployed service runs the Gradio UI and the
FastAPI routes in one process (`app.py` mounts Gradio on the FastAPI app). The UI calls
`core` directly instead of going through HTTP, because the free tier gives one container
and no second process. The API contract (`/health`, `/metadata`, `/extract`, `/metrics`)
is still served on the same port, so integrators do not need the UI.

## Refresh

INS publishes the quarterly bulletin four times a year. `refresh.yml` runs quarterly,
downloads any PDF in `data/sources.csv` that is not already cached, extracts the listed
pages, runs `check_long_dataset`, and only then writes `data/processed/observations.csv`
with a `DATA_DATE` column. A cached PDF is never re-downloaded. A failed gate keeps the
previous file.

## SDMX and DSD resources

Where the conventions used here come from. Each was read on 2026-09-16 unless noted.

| Resource | What it gives | Link |
|---|---|---|
| SDMX standard, versions 2.1 and 3.0 | the information model, SDMX-ML, SDMX-JSON and SDMX-CSV formats | https://sdmx.org/standards-2/ |
| SDMX cross-domain codelists | `CL_OBS_STATUS` (A normal, U low reliability, E estimated), `CL_FREQ`, `CL_UNIT_MULT`, `CL_AREA` | https://sdmx.org/sdmx_cdcl/ and https://github.com/SDMX-SWG/CDCL |
| SDMX Global Registry REST | the codelists above as SDMX-ML, for example `codelist/SDMX/CL_OBS_STATUS/latest` | https://registry.sdmx.org/ |
| SDMX time formats | `YYYY` for a calendar year, `YYYY-A1` for a reporting year that starts inside the calendar year | https://wiki.sdmxcloud.org/SDMX_Time_Formats |
| World Bank SDMX API | the WDI DSD `WB:WDI(1.0)`: dimensions `FREQ, SERIES, REF_AREA, TIME_PERIOD`, attribute `UNIT_MULT` | https://api.worldbank.org/v2/sdmx/rest/datastructure/WB/WDI/1.0 and https://datahelpdesk.worldbank.org/knowledgebase/articles/1886701-sdmx-api-queries |
| IMF SDMX Central | public DSDs to reuse as models | https://sdmxcentral.imf.org/ |
| SDMX tools catalogue | official inventory of SDMX software, where this tool can be submitted once published | https://www.sdmx.io/software/ and https://github.com/SDMX-Outreach/sdmx-tools-catalogue |
| sdmx1 | the Python library used here to write SDMX-ML | https://sdmx1.readthedocs.io/ |
| Learning SDMX structural modelling | how a DSD is built: concepts, codelists, dimensions, attributes | https://www.sdmx.io/learning/essential-sdmx-structural-modelling/ |
| AfDB Open Data Platform, Africa Information Highway | the portals INS Niger publishes on. Their SDMX endpoint returns SDMX-ML 2.0 with one KeyFamily per dataset (checked in a browser on 2026-09-16; scripted access is refused). ODP 2.0 is announced as SDMX native | https://dataportal.opendataforafrica.org/ and https://dataportal.opendataforafrica.org/api/1.0/sdmx |
| INS Niger open data portal | the existing Niger datasets, about 100, CC-BY 4.0. Agriculture datasets: `nxlreub` Production Agricole, `kfvhzqc` Données sur l'Agriculture, `wvovabg` Statistiques de l'Elevage | https://niger.opendataforafrica.org/ and https://niger.opendataforafrica.org/api/1.0/sdmx |
| INS Niger publications | the PDF bulletins this tool reads | https://www.stat-niger.org/ |
