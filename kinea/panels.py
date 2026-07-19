"""Point-in-time panels for honest, look-ahead-free model backtests."""

from __future__ import annotations

import calendar
import csv
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class PanelRow:
    knowledge_date: str
    series_id: str
    reference_date: str
    value: float
    vintage_date: str
    collected_at: str


PANEL_COLUMNS = tuple(PanelRow.__dataclass_fields__)


def knowledge_date_grid(start: str, end: str, frequency: str = "monthly") -> tuple[str, ...]:
    """Build an inclusive daily, weekly, or monthly grid of knowledge dates."""
    current = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    if current > stop:
        raise ValueError("panel start date must not be after end date")
    if frequency not in {"daily", "weekly", "monthly"}:
        raise ValueError("panel frequency must be daily, weekly, or monthly")

    dates: list[str] = []
    anchor_day = current.day
    while current <= stop:
        dates.append(current.isoformat())
        if frequency == "daily":
            current += timedelta(days=1)
        elif frequency == "weekly":
            current += timedelta(days=7)
        else:
            absolute_month = current.year * 12 + current.month
            year, zero_based_month = divmod(absolute_month, 12)
            month = zero_based_month + 1
            day = min(anchor_day, calendar.monthrange(year, month)[1])
            current = date(year, month, day)
    return tuple(dates)


def as_of_panel(
    conn: sqlite3.Connection,
    dates: Iterable[str],
    *,
    series_ids: Sequence[str] | None = None,
) -> tuple[PanelRow, ...]:
    """Return the latest vintage known on each requested knowledge date.

    The result is long-form and intentionally includes both ``reference_date`` and
    ``knowledge_date``. No row with a later ``vintage_date`` can enter an earlier snapshot.
    """
    knowledge_dates = tuple(sorted({date.fromisoformat(item).isoformat() for item in dates}))
    if not knowledge_dates:
        return ()

    date_values = ", ".join("(?)" for _ in knowledge_dates)
    params: list[str] = list(knowledge_dates)
    series_filter = ""
    if series_ids:
        selected = tuple(sorted(set(series_ids)))
        series_filter = f" AND t.series_id IN ({', '.join('?' for _ in selected)})"
        params.extend(selected)

    query = f"""
        WITH knowledge_dates(knowledge_date) AS (
            VALUES {date_values}
        ), ranked AS (
            SELECT
                k.knowledge_date,
                t.series_id,
                t.reference_date,
                t.value,
                t.vintage_date,
                t.collected_at,
                ROW_NUMBER() OVER (
                    PARTITION BY k.knowledge_date, t.series_id, t.reference_date
                    ORDER BY t.vintage_date DESC, t.collected_at DESC
                ) AS rn
            FROM knowledge_dates k
            JOIN time_series t ON t.vintage_date <= k.knowledge_date
            WHERE 1 = 1 {series_filter}
        )
        SELECT knowledge_date, series_id, reference_date, value, vintage_date, collected_at
        FROM ranked
        WHERE rn = 1
        ORDER BY knowledge_date, series_id, reference_date
    """
    rows = conn.execute(query, params).fetchall()
    return tuple(
        PanelRow(
            knowledge_date=row["knowledge_date"],
            series_id=row["series_id"],
            reference_date=row["reference_date"],
            value=float(row["value"]),
            vintage_date=row["vintage_date"],
            collected_at=row["collected_at"],
        )
        for row in rows
    )


def write_panel(rows: Sequence[PanelRow], output: str | Path, file_format: str) -> Path:
    """Write a panel as CSV (stdlib) or an optional Parquet/Feather artifact."""
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    normalized_format = file_format.lower()
    records = [asdict(row) for row in rows]

    if normalized_format == "csv":
        with destination.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=PANEL_COLUMNS)
            writer.writeheader()
            writer.writerows(records)
        return destination

    if normalized_format not in {"parquet", "feather"}:
        raise ValueError("panel format must be csv, parquet, or feather")
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - exercised in minimal installations
        raise RuntimeError(
            "Parquet/Feather export requires: python -m pip install -e '.[modeling]'"
        ) from exc

    frame = pd.DataFrame.from_records(records, columns=PANEL_COLUMNS)
    if normalized_format == "parquet":
        frame.to_parquet(destination, index=False)
    else:
        frame.to_feather(destination)
    return destination
