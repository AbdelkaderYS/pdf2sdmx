"""Compare extracted values with hand-typed ground truth. Writes data/processed/metrics.json.

Truth files live in truth/<pdf stem>_p<page>_truth.csv with columns row,column,value,
where row and column are the printed labels and value is the number as typed by a person.
The baseline is stage 1 alone (pdfplumber). The model is the full cascade.
"""

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdf2sdmx.config import settings  # noqa: E402
from pdf2sdmx.core import validate  # noqa: E402
from pdf2sdmx.core.ingest import cascade, pdfplumber_stage  # noqa: E402

TRUTH_DIR = settings.data_raw.parent.parent / "truth"
TRUTH_NAME = re.compile(r"(?P<stem>.+)_p(?P<page>\d+)_truth\.csv")


def score(values: pd.DataFrame | None, truth: pd.DataFrame) -> tuple[int, int]:
    """Count truth cells found with the same value. A missing table scores zero."""
    if values is None:
        return 0, len(truth)
    hits = 0
    for t in truth.itertuples():
        row = _find_label(values.index, t.row)
        col = _find_label(values.columns, t.column)
        if row is None or col is None:
            continue
        got = values.at[row, col]
        if pd.notna(got) and abs(got - float(t.value)) <= 0.5:
            hits += 1
    return hits, len(truth)


def _find_label(labels, wanted: str):
    wanted = _norm(wanted)
    return next((lb for lb in labels if _norm(lb) == wanted or wanted in _norm(lb)), None)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def evaluate_file(truth_path: Path) -> dict:
    match = TRUTH_NAME.match(truth_path.name)
    pdf_path = settings.data_raw / f"{match['stem']}.pdf"
    page = int(match["page"])
    truth = pd.read_csv(truth_path, dtype={"row": str, "column": str})

    baseline = cascade.run(pdf_path, page, stages=[(pdfplumber_stage.METHOD, pdfplumber_stage.extract)])
    full = cascade.run(pdf_path, page)
    base_values = validate.numeric_frame(baseline.frame) if not baseline.frame.empty else None
    full_values = validate.numeric_frame(full.frame) if not full.frame.empty else None
    base_hits, n = score(base_values, truth)
    full_hits, _ = score(full_values, truth)
    caught = sum(c.status == "fail" for c in validate.run_checks(full.frame)) if not full.frame.empty else 0
    return {
        "file": pdf_path.name,
        "page": page,
        "truth_cells": n,
        "baseline_hits": base_hits,
        "cascade_hits": full_hits,
        "resolved_by": full.resolved_by,
        "checks_failed": caught,
    }


def main() -> None:
    rows = [evaluate_file(p) for p in sorted(TRUTH_DIR.glob("*_truth.csv"))]
    if not rows:
        sys.exit("No truth files found in truth/")
    total = sum(r["truth_cells"] for r in rows)
    metrics = {
        "model_score": round(sum(r["cascade_hits"] for r in rows) / total, 4),
        "baseline_score": round(sum(r["baseline_hits"] for r in rows) / total, 4),
        "baseline_name": "pdfplumber alone",
        "metric": "share of hand-typed truth cells recovered exactly",
        "evaluated_on": f"{len(rows)} pages, {total} cells, {datetime.now(tz=UTC).date().isoformat()}",
        "pages": rows,
    }
    settings.data_processed.mkdir(parents=True, exist_ok=True)
    (settings.data_processed / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"\ncascade {metrics['model_score']:.1%}  baseline {metrics['baseline_score']:.1%}  on {total} cells")


if __name__ == "__main__":
    main()
