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

from .vintages import values_equal


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
    """Per-series revision statistics.

    ``mean_abs_revision``/``max_abs_revision`` report magnitude only. ``mean_revision``,
    ``n_upward`` and ``n_downward`` additionally expose the sign kept but discarded by
    ``revision_events()``: near-zero ``mean_revision`` with a roughly even up/down split
    suggests noise around a stable estimate, while a persistent sign is a systematic bias a
    forecaster should adjust for (e.g. a source that always revises energy prices upward on the
    second release). ``mean_pct_revision`` is the signed percent counterpart, comparable across
    series with different index bases.
    """

    series_id: str
    n_revised: int
    mean_abs_revision: float
    max_abs_revision: float
    mean_lag_days: float
    mean_revision: float
    n_upward: int
    n_downward: int
    mean_pct_revision: float | None


@dataclass(frozen=True)
class AsOfDifference:
    series_id: str
    reference_date: str
    status: str
    left_value: float | None
    right_value: float | None
    change: float | None
    left_vintage: str | None
    right_vintage: str | None


@dataclass(frozen=True)
class PublicationLag:
    series_id: str
    reference_date: str
    publish_date: str | None
    first_observed_vintage: str
    reference_to_publish_days: int | None
    publish_to_observed_days: int | None


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
        signed = [event.change for event in events]
        lags = [event.lag_days for event in events]
        pct_signed = [event.pct_change for event in events if event.pct_change is not None]
        summaries.append(
            SeriesRevisionSummary(
                series_id=sid,
                n_revised=len(events),
                mean_abs_revision=fmean(magnitudes),
                max_abs_revision=max(magnitudes),
                mean_lag_days=fmean(lags),
                mean_revision=fmean(signed),
                n_upward=sum(1 for change in signed if change > 0),
                n_downward=sum(1 for change in signed if change < 0),
                mean_pct_revision=fmean(pct_signed) if pct_signed else None,
            )
        )
    return summaries


@dataclass(frozen=True)
class RevisionReliability:
    """How much a forecaster should trust the newest print for one series.

    ``noise_to_signal`` divides the series' mean absolute revision by the mean absolute
    month-over-month (or day-over-day, for daily series) move of its own *current* values --
    the same denominator kinea/quality.py's ``max_change_pct`` check implicitly compares
    against. A ratio near 0 means revisions are small relative to how much the series normally
    moves anyway (trust the latest print); a ratio approaching or exceeding 1 means a revision
    is typically as large as a genuine month-to-month move, so the newest print should be
    treated as provisional. ``None`` when there is not yet enough history to judge either side.
    """

    series_id: str
    n_revised: int
    n_observations: int
    mean_abs_revision: float | None
    mean_abs_period_change: float | None
    noise_to_signal: float | None
    bias_direction: str
    """'upward', 'downward', or 'mixed' -- 'mixed' also covers the zero/one-revision case where
    direction cannot yet be judged."""


def revision_reliability(
    conn: sqlite3.Connection, series_id: str | None = None
) -> list[RevisionReliability]:
    """Deepen revision_summary() with a trust signal a forecasting desk can act on directly.

    Degrades gracefully rather than erroring when a series has too little history: with zero or
    one revised observation, ``mean_abs_revision``/``noise_to_signal`` are ``None`` and
    ``bias_direction`` is reported as ``'mixed'`` rather than asserting a bias that is not yet
    statistically meaningful.
    """
    where = "WHERE series_id = ?" if series_id is not None else ""
    params: tuple[str, ...] = (series_id,) if series_id is not None else ()
    current_rows = conn.execute(
        f"""
        SELECT series_id, reference_date, value
        FROM (
            SELECT t.*, ROW_NUMBER() OVER (
                PARTITION BY series_id, reference_date ORDER BY vintage_date DESC, collected_at DESC
            ) AS rn
            FROM time_series t
            {where}
        ) ranked
        WHERE rn = 1
        ORDER BY series_id, reference_date
        """,
        params,
    ).fetchall()
    current_by_series: dict[str, list[float]] = {}
    for row in current_rows:
        current_by_series.setdefault(row["series_id"], []).append(float(row["value"]))

    revisions_by_series: dict[str, list[RevisionEvent]] = {}
    for event in revision_events(conn, series_id):
        revisions_by_series.setdefault(event.series_id, []).append(event)

    results: list[RevisionReliability] = []
    for sid in sorted(current_by_series):
        values = current_by_series[sid]
        events = revisions_by_series.get(sid, [])
        period_changes = [abs(b - a) for a, b in zip(values, values[1:], strict=False)]
        mean_abs_period_change = fmean(period_changes) if period_changes else None
        mean_abs_revision = fmean([e.abs_change for e in events]) if events else None
        noise_to_signal = (
            mean_abs_revision / mean_abs_period_change
            if mean_abs_revision is not None and mean_abs_period_change
            else None
        )
        up = sum(1 for e in events if e.change > 0)
        down = sum(1 for e in events if e.change < 0)
        if len(events) >= 2 and up != down:
            bias_direction = "upward" if up > down else "downward"
        else:
            bias_direction = "mixed"
        results.append(
            RevisionReliability(
                series_id=sid,
                n_revised=len(events),
                n_observations=len(values),
                mean_abs_revision=mean_abs_revision,
                mean_abs_period_change=mean_abs_period_change,
                noise_to_signal=noise_to_signal,
                bias_direction=bias_direction,
            )
        )
    return results


def compare_as_of(
    conn: sqlite3.Connection,
    left_date: str,
    right_date: str,
    *,
    series_ids: list[str] | tuple[str, ...] | None = None,
    include_unchanged: bool = False,
) -> list[AsOfDifference]:
    """Compare two knowledge snapshots without leaking a later vintage into either side."""
    date.fromisoformat(left_date)
    date.fromisoformat(right_date)
    selected = tuple(sorted(set(series_ids or ())))
    clause = ""
    if selected:
        clause = f" AND series_id IN ({', '.join('?' for _ in selected)})"
    params: list[str] = [left_date, *selected, right_date, *selected]
    rows = conn.execute(
        f"""
        WITH left_ranked AS (
            SELECT series_id, reference_date, value, vintage_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY series_id, reference_date
                       ORDER BY vintage_date DESC, collected_at DESC
                   ) AS rn
            FROM time_series
            WHERE vintage_date <= ? {clause}
        ), left_view AS (
            SELECT series_id, reference_date, value, vintage_date
            FROM left_ranked WHERE rn = 1
        ), right_ranked AS (
            SELECT series_id, reference_date, value, vintage_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY series_id, reference_date
                       ORDER BY vintage_date DESC, collected_at DESC
                   ) AS rn
            FROM time_series
            WHERE vintage_date <= ? {clause}
        ), right_view AS (
            SELECT series_id, reference_date, value, vintage_date
            FROM right_ranked WHERE rn = 1
        ), keys AS (
            SELECT series_id, reference_date FROM left_view
            UNION
            SELECT series_id, reference_date FROM right_view
        )
        SELECT k.series_id, k.reference_date,
               l.value AS left_value, l.vintage_date AS left_vintage,
               r.value AS right_value, r.vintage_date AS right_vintage
        FROM keys k
        LEFT JOIN left_view l USING (series_id, reference_date)
        LEFT JOIN right_view r USING (series_id, reference_date)
        ORDER BY k.series_id, k.reference_date
        """,
        params,
    ).fetchall()
    differences: list[AsOfDifference] = []
    for row in rows:
        left = None if row["left_value"] is None else float(row["left_value"])
        right = None if row["right_value"] is None else float(row["right_value"])
        if left is None:
            status = "new"
        elif right is None:
            status = "removed"
        elif not values_equal(left, right):
            status = "revised"
        else:
            status = "unchanged"
        if status == "unchanged" and not include_unchanged:
            continue
        differences.append(
            AsOfDifference(
                series_id=row["series_id"],
                reference_date=row["reference_date"],
                status=status,
                left_value=left,
                right_value=right,
                change=None if left is None or right is None else right - left,
                left_vintage=row["left_vintage"],
                right_vintage=row["right_vintage"],
            )
        )
    return differences


def publication_lags(conn: sqlite3.Connection) -> list[PublicationLag]:
    """Report source-publication and first-observation lag when the source exposes a date."""
    rows = conn.execute(
        """
        SELECT m.series_id, m.last_observation AS reference_date, m.last_publish_date,
               MIN(t.vintage_date) AS first_observed_vintage
        FROM metadata m
        JOIN time_series t
          ON t.series_id = m.series_id AND t.reference_date = m.last_observation
        GROUP BY m.series_id, m.last_observation, m.last_publish_date
        ORDER BY m.series_id
        """
    ).fetchall()
    result = []
    for row in rows:
        reference = date.fromisoformat(row["reference_date"])
        observed = date.fromisoformat(row["first_observed_vintage"])
        published = (
            None
            if row["last_publish_date"] is None
            else date.fromisoformat(row["last_publish_date"])
        )
        result.append(
            PublicationLag(
                series_id=row["series_id"],
                reference_date=row["reference_date"],
                publish_date=None if published is None else published.isoformat(),
                first_observed_vintage=observed.isoformat(),
                reference_to_publish_days=(published - reference).days if published else None,
                publish_to_observed_days=(observed - published).days if published else None,
            )
        )
    return result
