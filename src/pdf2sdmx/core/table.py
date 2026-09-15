"""The one data shape every stage produces: a grid of strings plus where it came from."""

from dataclasses import dataclass, field

import pandas as pd

from pdf2sdmx.core.numbers import is_missing_marker, looks_numeric

MAX_HEADER_ROWS = 3
MAX_LABEL_COLUMNS = 3
LABEL_JOIN = " / "


@dataclass
class ExtractedTable:
    cells: list[list[str]]
    page: int
    method: str
    report: dict = field(default_factory=dict)

    @property
    def n_rows(self) -> int:
        return len(self.cells)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.cells), default=0)

    def to_frame(self) -> pd.DataFrame:
        """Rectangular frame: header rows merged into column names, label columns merged into column 0."""
        grid = _pad(self.cells, self.n_cols)
        n_header = _count_header_rows(grid)
        header = _merge_header_rows(grid[:n_header]) if n_header else _default_header(self.n_cols)
        frame = pd.DataFrame(grid[n_header:], columns=_dedupe(header))
        frame = frame.loc[:, (frame != "").any(axis=0)]  # ruling lines leave empty border columns
        return merge_label_columns(frame)


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def merge_label_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """INS prints nested row labels in two or three columns, the outer one only on its first
    row. Fill the outer labels down and join them so the rest of the pipeline sees one label.
    """
    n_labels = _count_label_columns(frame)
    if n_labels <= 1 or frame.empty:
        return frame
    labels = frame.iloc[:, :n_labels].astype(str).apply(lambda s: s.str.strip())
    outer = labels.iloc[:, :-1].replace("", pd.NA).ffill().fillna("")
    parts = pd.concat([outer, labels.iloc[:, -1]], axis=1)
    joined = parts.apply(lambda row: LABEL_JOIN.join(p for p in row if p), axis=1)
    out = frame.iloc[:, n_labels:].copy()
    out.insert(0, LABEL_JOIN.join(frame.columns[:n_labels]), joined)
    return out


def _count_label_columns(frame: pd.DataFrame) -> int:
    """Leading columns that hold almost no numbers are labels, not data."""
    count = 0
    for column in frame.columns[:MAX_LABEL_COLUMNS]:
        cells = [c for c in frame[column].astype(str) if c.strip()]
        numeric = sum(looks_numeric(c) for c in cells)
        if not cells or numeric / len(cells) < 0.3:
            count += 1
        else:
            break
    return max(count, 1)


def _pad(cells: list[list[str]], width: int) -> list[list[str]]:
    return [[clean_cell(c) for c in row] + [""] * (width - len(row)) for row in cells]


def _count_header_rows(grid: list[list[str]]) -> int:
    """Leading rows with almost no numbers are header rows. INS tables often stack two or three."""
    count = 0
    for row in grid[:MAX_HEADER_ROWS]:
        body = row[1:]
        numeric = sum(looks_numeric(c) for c in body)
        if body and numeric / len(body) < 0.3:
            count += 1
        else:
            break
    return count


def _merge_header_rows(rows: list[list[str]]) -> list[str]:
    merged = []
    for col in zip(*rows, strict=True):
        parts = [p for p in col if p]
        merged.append(" ".join(dict.fromkeys(parts)) or "")
    return merged


def _default_header(width: int) -> list[str]:
    return ["label"] + [f"col_{i}" for i in range(1, width)]


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for name in names:
        name = name or "col"
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return out


def body_stats(frame: pd.DataFrame) -> dict:
    """Share of body cells that are numbers, missing markers, or unreadable."""
    body = frame.iloc[:, 1:]
    total = body.size or 1
    numeric = int(body.map(looks_numeric).to_numpy().sum())
    missing = int(body.map(is_missing_marker).to_numpy().sum())
    return {
        "rows": len(frame),
        "cols": frame.shape[1],
        "numeric_share": numeric / total,
        "missing_share": missing / total,
        "unreadable_share": (total - numeric - missing) / total,
    }
