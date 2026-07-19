"""Read-only revision analytics computed from ``time_series``.

Because the store keeps every vintage, we can quantify how the data revises: how large the
revisions are, in which direction, and how long after the first observed vintage they arrive.
This is exactly what a forecasting desk needs to reason about data reliability — and it is
computed entirely at query time, adding no table to the mandatory three-table schema.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from statistics import fmean


@dataclass(frozen=True)
class RevisionEvent:
    series_id: str
    reference_date: str
    n_vintages: int
    first_value: float
    first_vintage: str
    latest_value: float
    latest_vintage: str
    change: float
    abs_change: float
    pct_change: float | None
    lag_days: int


@dataclass(frozen=True)
class SeriesRevisionSummary:
    series_id: str
    n_revised: int
    mean_abs_revision: float
    max_abs_revision: float
    mean_lag_days: float


def revision_events(conn: sqlite3.Connection, series_id: str | None = None) -> list[RevisionEvent]:
    """Every observation carrying more than one vintage, first-versus-latest.

    One window-function query computes all events so the cost does not grow into an N+1 query
    pattern when a production database accumulates many revised observations.
    """
    where = "WHERE series_id = ?" if series_id is not None else ""
    params: tuple[str, ...] = (series_id,) if series_id is not None else ()
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT
                series_id,
                reference_date,
                value,
                vintage_date,
                collected_at,
                COUNT(*) OVER (
                    PARTITION BY series_id, reference_date
                ) AS n_vintages,
                ROW_NUMBER() OVER (
                    PARTITION BY series_id, reference_date
                    ORDER BY vintage_date, collected_at
                ) AS first_rank,
                ROW_NUMBER() OVER (
                    PARTITION BY series_id, reference_date
                    ORDER BY vintage_date DESC, collected_at DESC
                ) AS latest_rank
            FROM time_series
            {where}
        )
        SELECT
            series_id,
            reference_date,
            MAX(n_vintages) AS n_vintages,
            MAX(CASE WHEN first_rank = 1 THEN value END) AS first_value,
            MAX(CASE WHEN first_rank = 1 THEN vintage_date END) AS first_vintage,
            MAX(CASE WHEN latest_rank = 1 THEN value END) AS latest_value,
            MAX(CASE WHEN latest_rank = 1 THEN vintage_date END) AS latest_vintage
        FROM ranked
        WHERE n_vintages > 1
        GROUP BY series_id, reference_date
        ORDER BY series_id, reference_date
        """,
        params,
    ).fetchall()

    events: list[RevisionEvent] = []
    for row in rows:
        first_value = float(row["first_value"])
        latest_value = float(row["latest_value"])
        change = latest_value - first_value
        pct_change = (change / first_value * 100.0) if first_value else None
        lag_days = (
            date.fromisoformat(row["latest_vintage"]) - date.fromisoformat(row["first_vintage"])
        ).days
        events.append(
            RevisionEvent(
                series_id=row["series_id"],
                reference_date=row["reference_date"],
                n_vintages=int(row["n_vintages"]),
                first_value=first_value,
                first_vintage=row["first_vintage"],
                latest_value=latest_value,
                latest_vintage=row["latest_vintage"],
                change=change,
                abs_change=abs(change),
                pct_change=pct_change,
                lag_days=lag_days,
            )
        )
    return events


def revision_summary(
    conn: sqlite3.Connection, series_id: str | None = None
) -> list[SeriesRevisionSummary]:
    """Per-series revision statistics (count, mean/max magnitude, mean lag)."""
    grouped: dict[str, list[RevisionEvent]] = {}
    for event in revision_events(conn, series_id):
        grouped.setdefault(event.series_id, []).append(event)

    summaries: list[SeriesRevisionSummary] = []
    for sid, events in sorted(grouped.items()):
        magnitudes = [event.abs_change for event in events]
        lags = [event.lag_days for event in events]
        summaries.append(
            SeriesRevisionSummary(
                series_id=sid,
                n_revised=len(events),
                mean_abs_revision=fmean(magnitudes),
                max_abs_revision=max(magnitudes),
                mean_lag_days=fmean(lags),
            )
        )
    return summaries
