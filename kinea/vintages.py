"""Idempotent ingest implementing every vintage rule in assignment section 5.3."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from math import isclose
from typing import Iterable

from .models import IngestCounts, Observation

VALUE_REL_TOLERANCE = 1e-12
VALUE_ABS_TOLERANCE = 1e-12


def values_equal(left: float, right: float) -> bool:
    """Ignore serialization noise while retaining economically meaningful revisions."""
    return isclose(
        float(left),
        float(right),
        rel_tol=VALUE_REL_TOLERANCE,
        abs_tol=VALUE_ABS_TOLERANCE,
    )


def ingest_observations(
    conn: sqlite3.Connection,
    series_id: str,
    observations: Iterable[Observation],
    *,
    vintage_date: str,
    collected_at: str,
) -> IngestCounts:
    """Store observations without destroying history or duplicating unchanged values."""

    date.fromisoformat(vintage_date)
    datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
    counts = IngestCounts()
    materialized = tuple(observations)
    latest_rows = conn.execute(
        """
        SELECT reference_date, value, vintage_date
        FROM (
            SELECT reference_date, value, vintage_date, collected_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY reference_date
                       ORDER BY vintage_date DESC, collected_at DESC
                   ) AS rn
            FROM time_series
            WHERE series_id = ?
        ) ranked
        WHERE rn = 1
        """,
        (series_id,),
    ).fetchall()
    latest_by_reference = {
        row["reference_date"]: (float(row["value"]), row["vintage_date"]) for row in latest_rows
    }
    same_day = {
        reference: value
        for reference, (value, existing_vintage) in latest_by_reference.items()
        if existing_vintage == vintage_date
    }
    inserts: dict[str, tuple[str, str, str, float, str]] = {}
    updates: dict[str, tuple[float, str, str, str, str]] = {}

    for observation in materialized:
        date.fromisoformat(observation.reference_date)
        counts.seen += 1

        reference = observation.reference_date
        if reference in same_day:
            if values_equal(same_day[reference], observation.value):
                counts.unchanged += 1
            else:
                same_day[reference] = observation.value
                if reference in inserts:
                    inserts[reference] = (
                        series_id,
                        reference,
                        vintage_date,
                        observation.value,
                        collected_at,
                    )
                else:
                    updates[reference] = (
                        observation.value,
                        collected_at,
                        series_id,
                        reference,
                        vintage_date,
                    )
                counts.updated_same_day += 1
            continue

        latest = latest_by_reference.get(reference)
        if latest is not None and vintage_date < latest[1]:
            raise ValueError(
                "non-monotonic vintage_date: historical knowledge cannot be backfilled"
            )
        if latest is not None and values_equal(latest[0], observation.value):
            counts.unchanged += 1
            continue

        inserts[reference] = (
            series_id,
            reference,
            vintage_date,
            observation.value,
            collected_at,
        )
        same_day[reference] = observation.value
        if latest is None:
            counts.inserted += 1
        else:
            counts.revised += 1

    if updates:
        conn.executemany(
            """
            UPDATE time_series
               SET value = ?, collected_at = ?
             WHERE series_id = ? AND reference_date = ? AND vintage_date = ?
            """,
            updates.values(),
        )
    if inserts:
        conn.executemany(
            """
            INSERT INTO time_series
                (series_id, reference_date, vintage_date, value, collected_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            inserts.values(),
        )

    return counts
