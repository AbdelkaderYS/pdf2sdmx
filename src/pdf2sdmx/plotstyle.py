"""Shared figure style. Import and call setup() before plotting."""

import matplotlib as mpl

# Okabe-Ito, colourblind safe
OKABE_ITO = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442", "#000000"]

MM = 1 / 25.4
SINGLE_COLUMN = 89 * MM
DOUBLE_COLUMN = 183 * MM


def setup() -> None:
    mpl.rcParams.update(
        {
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 9,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 7,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": "#DDDDDD",
            "grid.linewidth": 0.5,
            "axes.axisbelow": True,
            "lines.linewidth": 1.2,
            "axes.prop_cycle": mpl.cycler(color=OKABE_ITO),
        }
    )


def save(fig, path_without_extension) -> None:
    """Save both vector and raster versions."""
    fig.savefig(f"{path_without_extension}.pdf")
    fig.savefig(f"{path_without_extension}.png")
