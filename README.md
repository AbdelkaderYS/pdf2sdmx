---
title: pdf2sdmx
emoji: 📄
colorFrom: gray
colorTo: green
sdk: gradio
sdk_version: 6.17.3
app_file: app.py
pinned: false
license: mit
---

# pdf2sdmx

Turn a table printed in a statistical PDF report into validated observations in SDMX-CSV
and SDMX-ML. Three open source extraction stages run in order, the one with the fewest
failed arithmetic checks wins, its broken rows are repaired from the others when that
lowers the failure count, and every observation records which stage it came from.

**Question this answers:** does what a statistical office prints agree with what its open
data portal publishes, and what does the printed report carry that the portal does not?

The project started from a different question, whether the regional detail existed only in
PDF. It does not. The AfDB portal for Niger carries regional agricultural production from
1990 to 2024, so that premise is settled and `scripts/audit_against_portal.py` exists
because of it. The measured answer is below.

## Headline numbers

Measured on table 03.01 of the INS quarterly bulletin, 3rd quarter 2025 (campagne
agricole 2024/2025), against 94 cells transcribed by hand from the page image.

| | |
|---|---|
| Cells recovered exactly, stage 1 (pdfplumber) alone | 84 / 94 (89.4%) |
| Cells recovered exactly, full cascade | 94 / 94 (100%) |
| Wrong values that reached the output | 0 |
| How the cascade got the last 10 | pdfplumber glued the three Poivron rows into one. Camelot ml read that block correctly but broke 30 other cells. The cascade kept pdfplumber and took only the Poivron rows from Camelot, because each swap lowered the number of failed checks. Every observation carries the stage it came from (816 pdfplumber, 13 camelot_ml). |
| Extraction errors intercepted by the checks | 8 unreadable cells on that page before repair, 0 after, 0 silent |
| Source errors surfaced by comparing editions | 32 cross-campaign jumps above a factor of 5 listed in `data/processed/to_review.csv`, including Niamey cowpea area printed as 1 316 237 ha in the 1T 2024 bulletin (15 632 ha a year later) |
| Pages processed so far | 2 reports of 71 pages each, 18 months apart. A whole report takes 2 to 4 minutes on CPU. |

The 100% is on one page of one table type and should be read as "the cascade and the
checks work on this layout", not as a general accuracy figure. The number that matters
more is the zero: no wrong value reached the output on either page, because every
unreadable cell was refused and listed rather than passed through.

Across two whole 71-page reports, with no page selected by hand:

| | 1st quarter 2024 | 3rd quarter 2025 |
|---|---|---|
| Pages holding a table | 42 / 71 | 43 / 71 |
| Tables read | 53 | 57 |
| Tables shown under the caption printed above them | 43 | 46 |
| Observations | 5 943 | 5 875 |
| Observations covered by an arithmetic check | 10% | 10% |
| Observations flagged for review | 376 | 373 |
| Values written as an invalid SDMX time period | 0 | 0 |
| SDMX-ML 2.1 against the official schemas | valid | valid |

Read the 10% before anything else. It is the share of numbers that an arithmetic check
actually touched. The other 90% carry `OBS_STATUS = A` because nothing contradicted them,
not because anything confirmed them, and the interface says so on screen rather than
leaving a reader to assume otherwise. Raising that share means more checks, not more
extraction.

![Cereal production by region](figures/production_by_region.png)

## Against the portal

The same office publishes an open data portal. `make audit` compares a page of the report
with what the portal already holds, for the agriculture table of the 3rd quarter 2025
bulletin against the portal's 2024 production:

| | |
|---|---|
| Values read from the report | 123 |
| Also published by the portal | 58 |
| Agreeing within 0.5% | 56 (97%) |
| Differing | 2 |
| In the report only | 65 |

The two differences are national totals for market garden crops, where the portal counts a
scope the report's campaign table does not. The agreement is against figures this
repository did not produce, which is worth more than any internal check.

The 65 values with no counterpart, and the campaign the portal has not published yet, are
what reading the report adds. That is the argument, measured, rather than an assumption
about what is missing.

## What comes out

Drop a PDF, press Start. Every page is read in turn; the left panel shows the page being
read, the right panel the tables found on it (a page can hold several) and a running count
of tables, observations and flagged cells. At the end one zip holds five files for the
whole document.

| File | Content |
|---|---|
| `*_long.csv` | one observation per row: `FREQ, REF_AREA, INDICATOR, TIME_PERIOD, OBS_VALUE, UNIT_MEASURE, UNIT_MULT, OBS_STATUS, TIME_PERIOD_LABEL, EXTRACTION_METHOD, SOURCE` plus the printed labels |
| `*_sdmx.csv` | SDMX-CSV 2.0 |
| `*_structure.xml` | SDMX-ML 2.1 structure message: concept scheme, code lists, DSD and dataflow `INS_NE:DF_PDF2SDMX(1.0)` |
| `*_data.xml` | SDMX-ML 2.1 generic data message |
| `*_to_review.csv` | every cell that failed a check, with the reason |

Both XML messages are checked against the official SDMX-ML 2.1 schemas, not against our own
reader. Install the schemas once with `make schemas`, then `pytest tests/test_sdmx_out.py`
fails if the output stops conforming, and the interface says so on screen after a run. The
data message uses the generic format because it validates against the published schemas on
its own; structure specific data would need a schema generated from the DSD first. Every
coded value is passed through `reshape.sdmx_code`, so a unit printed `kg/ha` is written
`KG_HA` while the ISO region code `NE-1` and the SDMX total code `_T` are left alone.

Component ids follow the SDMX cross-domain concepts, the same ones the World Bank WDI DSD
uses (`FREQ, REF_AREA, TIME_PERIOD, OBS_VALUE, UNIT_MULT`). Region codes follow ISO 3166-2:NE.
`OBS_STATUS` uses CL_OBS_STATUS 2.3: `A` for a value that passed every check, `U` (low
reliability) for one that failed. A campaign printed `2024/2025` becomes the SDMX reporting
year `2024-A1`, with the printed text kept in `TIME_PERIOD_LABEL`. Nothing is dropped silently.
The comparison with the WDI DSD is in `ARCHITECTURE.md`.

## Run it

```bash
git clone <repo> && cd 09-ins-niger-sdmx
uv venv && source .venv/bin/activate
make install          # stage 1 only, fast
make install-ml       # adds Camelot ml with CPU torch, about 400 MB
# stage 3, vision, optional: Baidu's CPU wheel first, then the OCR package
pip install paddlepaddle==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install "paddleocr[doc-parser]"     # PaddleOCR-VL 1.6 downloads its model on first use
make schemas          # official SDMX 2.1 schemas, once, for the conformance check
python app.py         # UI and API on http://localhost:7860, docs at /docs
```

Or `docker compose up`. The Load sample button opens `data/samples/ins_bulletin_3T25_p20-23.pdf`,
four pages of the INS quarterly bulletin (about 40 s on CPU). The full bulletins used for
the measurements are downloaded by `python -m pdf2sdmx.core.ingest.refresh`, which also
writes `data/processed/observations.csv` with a `DATA_DATE` column.

API: `GET /health`, `GET /metadata`, `POST /extract` (multipart PDF + page), `GET /metrics`.

## Audit against a portal

```bash
make audit
```

It needs an extract of the portal in `data/reference/afdb/`. The service is
`https://ne.sdmx.afdb.org/ns-ws/rest/`, plain SDMX REST, unlike the browser host which
refuses anything that is not a browser:

```bash
curl -o data/reference/afdb/dataflows.xml \
  "https://ne.sdmx.afdb.org/ns-ws/rest/dataflow/NE1"
curl -o data/reference/afdb/DF_AGRI_PROD.xml \
  "https://ne.sdmx.afdb.org/ns-ws/rest/dataflow/NE1/DF_AGRI_PROD/1.0/?detail=Full&references=Descendants"
```

Their agriculture structure splits the measure (`INDIC_AGRI`) from the thing measured
(`SPECULATION`), the same split this repository uses, and `mapping/labels_to_codes.csv`
now carries their codes where ours were invented.

## Deploy it

The Space runs on the Gradio SDK, so it installs `requirements.txt` and runs `app.py`. It
never builds the Dockerfile, which is there for `docker compose up` only. Anything the app
needs at run time, including the SDMX schemas, is fetched by the warm-up thread in
`app.py`.

```bash
huggingface-cli login
huggingface-cli repo create pdf2sdmx --type space --space_sdk gradio
git remote add space https://huggingface.co/spaces/<user>/pdf2sdmx
git push space main
```

The YAML header at the top of this file is what the Space reads: `sdk_version` pins Gradio,
`app_file` points at the entry point. Keep `sdk_version` equal to the Gradio version the
numbers above were measured on, or the interface may shift under you.

First start takes a few minutes: the Table Transformer models are about 230 MB and the
sample PDFs are downloaded. Until the schemas land, the badge reads "not checked" rather
than claiming a conformance nobody verified.

## Build the vocabulary

The engine reads any report. It can only *code* what the vocabulary names, and the
interface publishes how much of the run that covers. `mapping/labels_to_codes.csv` is the
vocabulary: one row per printed label, with the SDMX code it stands for and which
dimension it belongs to. It is an input, like the PDF, not a part of the tool. On an empty
vocabulary the same numbers still come out, with every code reading `_Z`.

```bash
make reference    # official cross-domain code lists from the SDMX Global Registry
make harvest      # writes mapping/to_name.csv from the reports in data/raw
```

`to_name.csv` is a worklist, not a result. It holds every label the reports printed, with
the spellings of one thing collapsed into one row, ranked by how many observations each
would unlock, and an empty `code` column. Someone who knows the data fills it from the top
and stops when the coverage is enough. Nothing in it is a code until a person writes one.

Two things are checked against lists this repository does not maintain: `FREQ` and
`OBS_STATUS` come from the SDMX Global Registry, and the interface says whether every code
written belongs to them. A code list built from a run's own output cannot fail, so that
badge is the only one that can say no.

## Measure it yourself

```bash
make evaluate     # compares each truth/*.csv with stage 1 alone and with the cascade
make figures      # figures/production_by_region.{pdf,png}
make test
```

Add a page: transcribe 10 to 30 cells into `truth/<pdf stem>_p<page>_truth.csv`
(format in `truth/README.md`) and run `make evaluate` again.

## Limits

- Two documents, one country, one language. The layout rules are general, but the words
  are French: the caption, the unit line, the month names, the multiplier words, the total
  labels and the number format. Another language means another set of those constants, not
  a rewrite. Another country means another vocabulary file, which is an input.
- Only 10% of the observations are covered by an arithmetic check. A table with no total
  row, no total column and no area-yield-production triple has every check skipped, and its
  numbers leave unverified.
- A check validates numbers, not meaning. A table whose header was misread can still add
  up. That is why an unnamed header now counts as a failed check, but a header that is
  wrong rather than missing is not caught.
- Pages holding a table in landscape, or a table running across two pages, are not handled
  yet. The caption inventory that would measure what is missed is not built.
- The DSD is built by the tool. No official INS Niger DSD was found; when one exists the
  codes in `mapping/labels_to_codes.csv` should be replaced by it.
- Conformance is checked against the XSD schemas, which validate the shape of the message.
  They do not check that a code exists in its code list or that a period is real. A
  registry such as FMR would.
- Stage 3 (PaddleOCR-VL 1.6, vision) is wired but not installed in the Space. The sample
  reports have a text layer, so it was never needed. Scanned reports would need it, and the
  interface states whether it is installed.
- Camelot ml downloads two Table Transformer models (about 230 MB) at Space start-up.
  Stage 2 takes about 15 s per page on CPU; stage 1 takes 2 s.
- Row repair only swaps a row when the donor agrees on every cell the winner already read
  and the page-level failure count does not rise. It cannot fix a row both stages misread.
- Ground truth was transcribed from the rendered page image for this repository, not by
  INS. It should be re-checked by a second person.
- The cross-edition jump check cannot say which edition is wrong. It lists both.
- No update is guaranteed. The refresh job runs quarterly and only downloads PDFs already
  listed in `data/sources.csv`; someone has to add new bulletins to that list.

## What is and is not new here

SDMX, ODP, pdfplumber, Camelot, Table Transformer, PaddleOCR-VL and sdmx1 all exist and are
credited in `ARCHITECTURE.md`. A multi-agent architecture for SDMX compliance starting
from Excel sources was proposed at IAOS 2026. This repository works one step upstream,
on the PDF publications that are the actual form of diffusion for many national
statistics institutes, and reports a measured error rate on real pages. The mapping file
between INS labels and codes is the part that cannot be downloaded from anywhere.

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

## Layout

```
app.py                     Space entry point, mounts the Gradio UI on the FastAPI app
src/pdf2sdmx/core/         ingest (three stages + cascade), numbers, table, quality,
                           validate, reshape, sdmx_out, pipeline
src/pdf2sdmx/api/          FastAPI routes and schemas
src/pdf2sdmx/ui/           Gradio front end
mapping/labels_to_codes.csv
truth/                     hand-typed ground truth
data/sources.csv           PDFs and pages the refresh processes
data/raw/                  PDFs, never edited (gitignored)
data/processed/            observations.csv, to_review.csv, metrics.json
scripts/evaluate.py        accuracy against truth
scripts/make_figures.py
ARCHITECTURE.md            stages, thresholds, tool choices, deviations
BRIEF.md                   the project brief this was built from
```

Licence: MIT for the code. The PDFs belong to INS Niger; the extracted observations
are published under the same CC-BY 4.0 terms as the INS open data portal.
