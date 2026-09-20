"""Turn a corpus of reports into a worklist for the vocabulary file.

The engine reads any report but can only code what the vocabulary names. This collects
every label printed, collapses the spellings of one thing, ranks what is left by the
observations it would unlock, and writes a CSV whose `code` column is empty. Nothing here
invents a code.

    python scripts/harvest_vocabulary.py data/raw/*.pdf
    python scripts/harvest_vocabulary.py --from-csv run.csv
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdf2sdmx.config import settings  # noqa: E402
from pdf2sdmx.core import pipeline, reshape  # noqa: E402

# Each dimension and the column holding the label as the report printed it.
DIMENSIONS = {
    "REF_AREA": "REF_AREA_LABEL",
    "INDICATOR": "INDICATOR_LABEL",
    "COMPOSITE_BREAKDOWN": "COMPOSITE_BREAKDOWN_LABEL",
}
# The threshold is high on purpose: an extra row costs one line, a wrong merge destroys a
# distinction in silence.
SAME_CONCEPT = 95
# Our own placeholder for a label we could not name. It is not something a report printed.
NOT_A_LABEL = {"UNKNOWN", ""}
OUTPUT = Path("mapping") / "to_name.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="*", type=Path, help="reports to read")
    parser.add_argument("--from-csv", type=Path, help="a long CSV from an earlier run, instead of reading PDFs")
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args()

    if args.from_csv:
        long = pd.read_csv(args.from_csv, dtype=str)
    elif args.pdfs:
        long = read_reports(args.pdfs)
    else:
        parser.error("give at least one PDF, or --from-csv")

    known = reshape.load_mapping(settings.mapping_file)
    worklist = build_worklist(long, known)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    worklist.to_csv(args.out, index=False)
    report(worklist, len(long), args.out)


def read_reports(pdfs: list[Path]) -> pd.DataFrame:
    """Every observation from every page of every report, in one frame."""
    frames = []
    for pdf in pdfs:
        n_pages = pipeline.page_count(pdf)
        print(f"reading {pdf.name}, {n_pages} pages", flush=True)
        for page in range(1, n_pages + 1):
            result = pipeline.run_page(pdf, page)
            if not result.long.empty:
                frames.append(result.long)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def build_worklist(long: pd.DataFrame, known: pd.DataFrame) -> pd.DataFrame:
    """One row per concept, most rewarding first, with an empty code column to fill."""
    rows = []
    for dimension, label_column in DIMENSIONS.items():
        if label_column not in long.columns:
            continue
        labels = long[label_column].fillna("").replace("", pd.NA).dropna()
        if labels.empty:
            continue
        named = set(known.loc[known["dimension"] == dimension, "label"].str.casefold())
        total = len(labels)
        covered = 0
        for concept, variants, count in cluster(labels.value_counts()):
            covered += count
            rows.append(
                {
                    "dimension": dimension,
                    "label": concept,
                    "code": "",
                    "observations": count,
                    "share": round(count / total, 4),
                    "cumulative": round(covered / total, 4),
                    "already_named": "yes" if concept.casefold() in named else "",
                    "variants": " | ".join(variants[1:6]),
                }
            )
    return pd.DataFrame(rows).sort_values(["dimension", "observations"], ascending=[True, False])


def cluster(counts: pd.Series) -> list[tuple[str, list[str], int]]:
    """Group spellings of one thing, keeping the most frequent as the name.

    The fuzzy match in reshape reaches the others from it.
    """
    labels = [str(label) for label in counts.index if str(label) not in NOT_A_LABEL]
    taken: set[int] = set()
    groups = []
    for position, label in enumerate(labels):
        if position in taken:
            continue
        variants = [label]
        count = int(counts[label])
        for other_position in range(position + 1, len(labels)):
            if other_position in taken:
                continue  # already absorbed by an earlier, more frequent spelling
            if _similarity(label, labels[other_position]) >= SAME_CONCEPT:
                taken.add(other_position)
                variants.append(labels[other_position])
                count += int(counts[labels[other_position]])
        groups.append((label, variants, count))
    return sorted(groups, key=lambda group: group[2], reverse=True)


def _similarity(one: str, other: str) -> float:
    """How close two labels are once coded, which folds away accents and case."""
    return fuzz.ratio(reshape.sdmx_code(one), reshape.sdmx_code(other))


def report(worklist: pd.DataFrame, observations: int, out: Path) -> None:
    print(f"\n{observations} observations, {len(worklist)} concepts to name")
    for dimension in DIMENSIONS:
        part = worklist[worklist["dimension"] == dimension]
        if part.empty:
            continue
        done = int((part["already_named"] == "yes").sum())
        print(f"\n{dimension}: {len(part)} concepts, {done} already named")
        for top in (25, 100, 200):
            if len(part) >= top:
                print(f"   naming the top {top:>3} would cover {part['cumulative'].iloc[top - 1]:.0%}")
    print(f"\nwritten to {out}. Fill the code column from the top and run the pipeline again.")


if __name__ == "__main__":
    main()
