"""Compare what a report prints with what the open data portal already publishes.

Three outcomes are all worth having. They agree, and the reading is validated against
figures produced elsewhere. They disagree, and two official publications contradict each
other. The portal stops earlier, and the gap is measured rather than claimed.

    python scripts/audit_against_portal.py --page 2 --period 2024
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdf2sdmx.config import settings  # noqa: E402
from pdf2sdmx.core import pipeline  # noqa: E402

PORTAL = settings.reference_dir / "afdb" / "DF_AGRI_PROD_data.csv"
SAMPLE = settings.data_raw.parent / "samples" / "ins_bulletin_3T25_p20-23.pdf"
# The portal has its own area scheme; ours is ISO 3166-2. This is the pairing, as a join
# needs it. The vocabulary file records the same thing for a reader.
THEIR_AREA = {
    "AFNER": "NE",  # the country, as the report codes a total over every region
    "AFNER1": "NE-1",
    "AFNER2": "NE-2",
    "AFNER3": "NE-3",
    "AFNER4": "NE-4",
    "AFNER5": "NE-5",
    "AFNER6": "NE-6",
    "AFNER7": "NE-7",
    "AFNER8": "NE-8",
}
RELATIVE_TOLERANCE = 0.005
# A printed table rounds to the unit, so a whole number against 2.8228 is not a difference.
ABSOLUTE_TOLERANCE = 1.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=SAMPLE)
    parser.add_argument("--page", type=int, default=2)
    parser.add_argument("--period", default="2024", help="the year to compare, as the portal writes it")
    parser.add_argument("--indicator", default="PROD")
    parser.add_argument("--out", type=Path, default=Path("data/processed/portal_audit.csv"))
    args = parser.parse_args()

    if not PORTAL.exists():
        parser.error(f"no portal extract at {PORTAL}. See README, section Audit.")

    ours = read_report(args.pdf, args.page, args.indicator)
    theirs = read_portal(args.period)
    compared = compare(ours, theirs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    compared.to_csv(args.out, index=False)
    report(ours, compared, args.out)


def read_report(pdf: Path, page: int, indicator: str) -> pd.DataFrame:
    long = pipeline.run_page(pdf, page).long
    long = long[long["INDICATOR"] == indicator]
    return long[["REF_AREA", "COMPOSITE_BREAKDOWN", "OBS_VALUE"]].reset_index(drop=True)


def read_portal(period: str) -> pd.DataFrame:
    portal = pd.read_csv(PORTAL, dtype=str)
    portal["OBS_VALUE"] = pd.to_numeric(portal["OBS_VALUE"], errors="coerce")
    portal = portal[portal["TIME_PERIOD"] == period].copy()
    portal["REF_AREA"] = portal["REF_AREA"].map(THEIR_AREA)
    return portal.rename(columns={"SPECULATION": "COMPOSITE_BREAKDOWN"}).dropna(subset=["REF_AREA"])


def compare(ours: pd.DataFrame, theirs: pd.DataFrame) -> pd.DataFrame:
    """One row per value present on both sides, with the gap between them.

    The portal may split a series where the report does not, so one printed figure can face
    several. The closest is kept: any other would report a difference of scope as an error.
    """
    columns = ["REF_AREA", "COMPOSITE_BREAKDOWN", "TYPE_CULTURE", "OBS_VALUE"]
    merged = ours.merge(theirs[columns], on=["REF_AREA", "COMPOSITE_BREAKDOWN"], suffixes=("_report", "_portal"))
    if merged.empty:
        return merged
    merged["gap"] = (merged["OBS_VALUE_report"] - merged["OBS_VALUE_portal"]).abs()
    closest = merged.groupby(["REF_AREA", "COMPOSITE_BREAKDOWN"])["gap"].idxmin()
    merged = merged.loc[closest].copy()
    merged["relative_gap"] = merged["gap"] / merged["OBS_VALUE_portal"].replace(0, pd.NA)
    merged["agrees"] = (merged["relative_gap"] < RELATIVE_TOLERANCE) | (merged["gap"] <= ABSOLUTE_TOLERANCE)
    return merged.sort_values("relative_gap", ascending=False)


def report(ours: pd.DataFrame, compared: pd.DataFrame, out: Path) -> None:
    if compared.empty:
        print("nothing in common between the report and the portal extract")
        return
    agreeing = int(compared["agrees"].sum())
    print(f"values read from the report      : {len(ours)}")
    print(f"also published by the portal     : {len(compared)}")
    print(f"  agreeing                       : {agreeing} ({agreeing / len(compared):.0%})")
    print(f"  differing                      : {len(compared) - agreeing}")
    print(f"in the report only               : {len(ours) - len(compared)}")
    differing = compared[~compared["agrees"]]
    if not differing.empty:
        print("\ndifferences, largest first:")
        show = ["REF_AREA", "COMPOSITE_BREAKDOWN", "TYPE_CULTURE", "OBS_VALUE_report", "OBS_VALUE_portal"]
        print(differing[show].to_string(index=False))
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
