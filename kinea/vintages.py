"""Idempotent ingest implementing every vintage rule in assignment section 5.3."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Iterable

from .models import IngestCounts, Observation


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

    for observation in observations:
        date.fromisoformat(observation.reference_date)
        counts.seen += 1

        same_day = conn.execute(
            """
            SELECT value FROM time_series
             WHERE series_id = ? AND reference_date = ? AND vintage_date = ?
            """,
            (series_id, observation.reference_date, vintage_date),
        ).fetchone()

        if same_day is not None:
            if float(same_day["value"]) == observation.value:
                counts.unchanged += 1
            else:
                conn.execute(
                    """
                    UPDATE time_series
                       SET value = ?, collected_at = ?
                     WHERE series_id = ? AND reference_date = ? AND vintage_date = ?
                    """,
                    (
                        observation.value,
                        collected_at,
                        series_id,
                        observation.reference_date,
                        vintage_date,
                    ),
                )
                counts.updated_same_day += 1
            continue

        latest = conn.execute(
            """
            SELECT value, vintage_date FROM time_series
             WHERE series_id = ? AND reference_date = ?
             ORDER BY vintage_date DESC, collected_at DESC
             LIMIT 1
            """,
            (series_id, observation.reference_date),
        ).fetchone()

        if latest is not None and vintage_date < latest["vintage_date"]:
            raise ValueError(
                "non-monotonic vintage_date: historical knowledge cannot be backfilled"
            )
        if latest is not None and float(latest["value"]) == observation.value:
            counts.unchanged += 1
            continue

        conn.execute(
            """
            INSERT INTO time_series
                (series_id, reference_date, vintage_date, value, collected_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (series_id, observation.reference_date, vintage_date, observation.value, collected_at),
        )
        if latest is None:
            counts.inserted += 1
        else:
            counts.revised += 1

    return counts
