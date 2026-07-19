import pytest

from kinea.db import connect
from kinea.models import Observation
from kinea.vintages import ingest_observations

SERIES = "CZ_HICP_CORE_INDEX"


def _conn():
    conn = connect(":memory:")
    conn.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            observation_count, source_url, collected_at
        ) VALUES (?, 'name', 'description', 'CZ', 'monthly', 'index', 0, 'url', ?)
        """,
        (SERIES, "2026-01-01T00:00:00+00:00"),
    )
    return conn


def _ingest(conn, value, vintage, timestamp=None):
    return ingest_observations(
        conn,
        SERIES,
        [Observation("2026-01-01", value)],
        vintage_date=vintage,
        collected_at=timestamp or f"{vintage}T10:00:00+00:00",
    )


def test_first_observation_uses_collection_day_as_vintage():
    conn = _conn()
    counts = _ingest(conn, 100.0, "2026-02-10")
    row = conn.execute("SELECT * FROM time_series").fetchone()
    assert counts.inserted == 1
    assert row["vintage_date"] == "2026-02-10"


def test_unchanged_later_collection_is_noop():
    conn = _conn()
    _ingest(conn, 100.0, "2026-02-10")
    counts = _ingest(conn, 100.0, "2026-03-10")
    assert counts.unchanged == 1
    assert conn.execute("SELECT COUNT(*) FROM time_series").fetchone()[0] == 1


def test_changed_later_collection_appends_vintage():
    conn = _conn()
    _ingest(conn, 100.0, "2026-02-10")
    counts = _ingest(conn, 101.0, "2026-03-10")
    assert counts.revised == 1
    assert [
        row[0] for row in conn.execute("SELECT value FROM time_series ORDER BY vintage_date")
    ] == [100.0, 101.0]


def test_later_revision_preserves_entire_old_row():
    conn = _conn()
    _ingest(conn, 100.0, "2026-02-10", "2026-02-10T09:00:00+00:00")
    _ingest(conn, 101.0, "2026-03-10", "2026-03-10T09:00:00+00:00")
    old = conn.execute(
        "SELECT value, collected_at FROM time_series WHERE vintage_date='2026-02-10'"
    ).fetchone()
    assert tuple(old) == (100.0, "2026-02-10T09:00:00+00:00")


def test_changed_same_day_updates_in_place():
    conn = _conn()
    _ingest(conn, 100.0, "2026-02-10", "2026-02-10T09:00:00+00:00")
    counts = _ingest(conn, 102.0, "2026-02-10", "2026-02-10T18:00:00+00:00")
    row = conn.execute("SELECT value, collected_at FROM time_series").fetchone()
    assert counts.updated_same_day == 1
    assert tuple(row) == (102.0, "2026-02-10T18:00:00+00:00")


def test_unchanged_same_day_does_not_touch_collected_at():
    conn = _conn()
    _ingest(conn, 100.0, "2026-02-10", "2026-02-10T09:00:00+00:00")
    _ingest(conn, 100.0, "2026-02-10", "2026-02-10T18:00:00+00:00")
    timestamp = conn.execute("SELECT collected_at FROM time_series").fetchone()[0]
    assert timestamp == "2026-02-10T09:00:00+00:00"


def test_float_serialization_noise_does_not_create_false_revision():
    conn = _conn()
    _ingest(conn, 100.0, "2026-02-10")

    counts = _ingest(conn, 100.0 + 1e-13, "2026-03-10")

    assert counts.unchanged == 1
    assert conn.execute("SELECT COUNT(*) FROM time_series").fetchone()[0] == 1


def test_economically_meaningful_float_change_still_creates_revision():
    conn = _conn()
    _ingest(conn, 100.0, "2026-02-10")

    counts = _ingest(conn, 100.0001, "2026-03-10")

    assert counts.revised == 1


def test_rejects_non_monotonic_vintage_backfill():
    conn = _conn()
    _ingest(conn, 101.0, "2026-03-10")
    with pytest.raises(ValueError, match="non-monotonic"):
        _ingest(conn, 100.0, "2026-02-10")


def test_primary_key_prevents_duplicate_vintage_rows():
    import sqlite3

    conn = _conn()
    _ingest(conn, 100.0, "2026-02-10")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO time_series VALUES (?, ?, ?, ?, ?)",
            (SERIES, "2026-01-01", "2026-02-10", 100.0, "2026-02-10T11:00:00+00:00"),
        )
