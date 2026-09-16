"""Cereal production by region for the two campaigns extracted so far.

Reads data/processed/observations.csv (written by the refresh) and writes
figures/production_by_region.{pdf,png}. The point of the figure: a regional series
now exists where before there were only two PDF pages.
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf2sdmx.plotstyle import DOUBLE_COLUMN, OKABE_ITO, save, setup  # noqa: E402

CROPS = {"MILLET_PROD_T": "Millet", "SORGHUM_PROD_T": "Sorghum", "COWPEA_PROD_T": "Cowpea"}
REGION_ORDER = ["NE-1", "NE-2", "NE-3", "NE-4", "NE-5", "NE-6", "NE-7", "NE-8"]
REGION_NAMES = {
    "NE-1": "Agadez",
    "NE-2": "Diffa",
    "NE-3": "Dosso",
    "NE-4": "Maradi",
    "NE-5": "Tahoua",
    "NE-6": "Tillabéri",
    "NE-7": "Zinder",
    "NE-8": "Niamey",
}


def main() -> None:
    obs = pd.read_csv(ROOT / "data" / "processed" / "observations.csv")
    data_date = obs["DATA_DATE"].iloc[0]
    obs = obs[obs["INDICATOR"].isin(CROPS) & obs["REF_AREA"].isin(REGION_ORDER) & (obs["OBS_STATUS"] == "A")]
    periods = sorted(obs["TIME_PERIOD_LABEL"].unique())
    table = obs.pivot_table(index=["INDICATOR", "REF_AREA"], columns="TIME_PERIOD_LABEL", values="OBS_VALUE") / 1000

    setup()
    fig, axes = plt.subplots(1, len(CROPS), figsize=(DOUBLE_COLUMN, 2.4), sharey=False)
    width = 0.8 / len(periods)
    x = np.arange(len(REGION_ORDER))
    for ax, (code, name) in zip(axes, CROPS.items(), strict=True):
        for i, period in enumerate(periods):
            values = [table.loc[(code, r), period] if (code, r) in table.index else np.nan for r in REGION_ORDER]
            ax.bar(x + (i - (len(periods) - 1) / 2) * width, values, width, color=OKABE_ITO[i], label=period)
        ax.set_title(name, loc="left")
        ax.set_xticks(x, [REGION_NAMES[r] for r in REGION_ORDER], rotation=60, ha="right")
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("Production (thousand tonnes)")
    axes[1].legend(frameon=False, title="Campaign", loc="upper left")
    fig.text(
        0,
        -0.12,
        f"Source: INS Niger, Bulletin trimestriel de statistique, table 03.01, extracted with pdf2sdmx "
        f"(data date {data_date}). Cells that failed a check are excluded.",
        fontsize=6,
        color="#555555",
        wrap=True,
    )
    out = ROOT / "figures" / "production_by_region"
    out.parent.mkdir(exist_ok=True)
    save(fig, out)
    print("wrote", out.with_suffix(".png"))


if __name__ == "__main__":
    main()
