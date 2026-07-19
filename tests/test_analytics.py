"""Tests for read-only revision analytics (adds no table to the schema)."""

from __future__ import annotations

from kinea.analytics import compare_as_of, publication_lags, revision_events, revision_summary
from kinea.db import connect
from kinea.models import Observation
from kinea.vintages import ingest_observations


def _seed(conn, series_id="CZ_HICP_CORE_INDEX"):
    conn.execute(
        """
        INSERT INTO metadata (series_id, name, description, country, frequency, unit,
                              observation_count, source_url, collected_at)
        VALUES (?, 'n', 'd', 'CZ', 'monthly', 'index', 0, 'https://x', ?)
        """,
        (series_id, "2026-07-01T10:00:00+00:00"),
    )
    return series_id


def test_no_revisions_returns_empty():
    conn = connect(":memory:")
    sid = _seed(conn)
    ingest_observations(
        conn,
        sid,
        [Observation("2026-06-01", 100.0)],
        vintage_date="2026-07-01",
        collected_at="2026-07-01T10:00:00+00:00",
    )
    assert revision_events(conn) == []
    assert revision_summary(conn) == []


def test_revision_event_first_versus_latest_and_lag():
    conn = connect(":memory:")
    sid = _seed(conn)
    ingest_observations(
        conn,
        sid,
        [Observation("2026-06-01", 100.0)],
        vintage_date="2026-07-01",
        collected_at="2026-07-01T10:00:00+00:00",
    )
    ingest_observations(
        conn,
        sid,
        [Observation("2026-06-01", 101.5)],
        vintage_date="2026-07-18",
        collected_at="2026-07-18T10:00:00+00:00",
    )
    events = revision_events(conn)
    assert len(events) == 1
    event = events[0]
    assert event.n_vintages == 2
    assert event.first_value == 100.0
    assert event.latest_value == 101.5
    assert event.change == 1.5
    assert event.abs_change == 1.5
    assert event.pct_change == 1.5
    assert event.lag_days == 17  # 2026-07-01 -> 2026-07-18


def test_revision_summary_aggregates_per_series():
    conn = connect(":memory:")
    sid = _seed(conn)
    for ref, first, second in [("2026-05-01", 100.0, 99.0), ("2026-06-01", 100.0, 103.0)]:
        ingest_observations(
            conn,
            sid,
            [Observation(ref, first)],
            vintage_date="2026-07-01",
            collected_at="2026-07-01T10:00:00+00:00",
        )
        ingest_observations(
            conn,
            sid,
            [Observation(ref, second)],
            vintage_date="2026-07-18",
            collected_at="2026-07-18T10:00:00+00:00",
        )
    summary = revision_summary(conn)
    assert len(summary) == 1
    row = summary[0]
    assert row.n_revised == 2
    assert row.mean_abs_revision == 2.0  # (|−1| + |3|) / 2
    assert row.max_abs_revision == 3.0
    assert row.mean_lag_days == 17.0


def test_revision_filter_applies_to_events_and_summary():
    conn = connect(":memory:")
    for sid in ("CZ_HICP_CORE_INDEX", "CZ_HICP_FOOD_INDEX"):
        _seed(conn, sid)
        ingest_observations(
            conn,
            sid,
            [Observation("2026-06-01", 100.0)],
            vintage_date="2026-07-01",
            collected_at="2026-07-01T10:00:00+00:00",
        )
        ingest_observations(
            conn,
            sid,
            [Observation("2026-06-01", 99.0)],
            vintage_date="2026-07-18",
            collected_at="2026-07-18T10:00:00+00:00",
        )

    events = revision_events(conn, "CZ_HICP_FOOD_INDEX")
    summary = revision_summary(conn, "CZ_HICP_FOOD_INDEX")

    assert [event.series_id for event in events] == ["CZ_HICP_FOOD_INDEX"]
    assert [row.series_id for row in summary] == ["CZ_HICP_FOOD_INDEX"]
    assert events[0].change == -1.0
    assert events[0].abs_change == 1.0


def test_compare_as_of_reports_new_and_revised_observations():
    conn = connect(":memory:")
    sid = _seed(conn)
    ingest_observations(
        conn,
        sid,
        [Observation("2026-05-01", 100.0)],
        vintage_date="2026-07-01",
        collected_at="2026-07-01T10:00:00+00:00",
    )
    ingest_observations(
        conn,
        sid,
        [Observation("2026-05-01", 101.0), Observation("2026-06-01", 102.0)],
        vintage_date="2026-07-18",
        collected_at="2026-07-18T10:00:00+00:00",
    )

    rows = compare_as_of(conn, "2026-07-10", "2026-07-18")

    assert [(row.reference_date, row.status, row.change) for row in rows] == [
        ("2026-05-01", "revised", 1.0),
        ("2026-06-01", "new", None),
    ]


def test_publication_lag_distinguishes_source_and_observation_dates():
    conn = connect(":memory:")
    sid = _seed(conn)
    ingest_observations(
        conn,
        sid,
        [Observation("2026-06-01", 100.0)],
        vintage_date="2026-07-18",
        collected_at="2026-07-18T10:00:00+00:00",
    )
    conn.execute(
        """
        UPDATE metadata
        SET last_observation='2026-06-01', last_publish_date='2026-07-17'
        WHERE series_id=?
        """,
        (sid,),
    )

    row = publication_lags(conn)[0]

    assert row.reference_to_publish_days == 46
    assert row.publish_to_observed_days == 1
