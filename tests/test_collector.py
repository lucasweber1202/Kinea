from pathlib import Path

import pytest

from kinea.client import FetchError, OfflineClient
from kinea.collector import collect
from kinea.config import load_config
from kinea.db import connect, table_counts


ROOT = Path(__file__).resolve().parent.parent


def test_offline_collection_populates_exact_schema():
    conn = connect(":memory:")
    report = collect(
        conn, load_config(), OfflineClient(ROOT / "fixtures" / "v2"),
        collected_at="2026-07-18T10:00:00+00:00",
    )
    assert report.status == "success"
    assert table_counts(conn) == {"metadata": 5, "time_series": 524, "logs": 1}


def test_second_consecutive_run_adds_no_data_rows():
    conn = connect(":memory:")
    config = load_config()
    client = OfflineClient(ROOT / "fixtures" / "v2")
    collect(conn, config, client, collected_at="2026-07-18T10:00:00+00:00")
    before = table_counts(conn)
    second = collect(conn, config, client, collected_at="2026-07-18T11:00:00+00:00")
    after = table_counts(conn)
    assert (before["metadata"], before["time_series"]) == (after["metadata"], after["time_series"])
    assert second.counts.inserted == second.counts.revised == second.counts.updated_same_day == 0


def test_later_fixture_revision_creates_two_vintages():
    conn = connect(":memory:")
    config = load_config()
    collect(conn, config, OfflineClient(ROOT / "fixtures" / "v1"),
            collected_at="2026-07-01T10:00:00+00:00")
    collect(conn, config, OfflineClient(ROOT / "fixtures" / "v2"),
            collected_at="2026-07-18T10:00:00+00:00")
    count = conn.execute(
        """
        SELECT COUNT(*) FROM time_series
         WHERE series_id='CZ_HICP_CORE_INDEX' AND reference_date='2026-06-01'
        """
    ).fetchone()[0]
    assert count == 2


def test_grave_failure_propagates_and_logs_error():
    conn = connect(":memory:")
    with pytest.raises(FetchError):
        collect(conn, load_config(), OfflineClient(ROOT / "fixtures" / "missing"),
                collected_at="2026-07-18T10:00:00+00:00")
    log = conn.execute("SELECT status, traceback FROM logs").fetchone()
    assert log["status"] == "error"
    assert "FetchError" in log["traceback"]
    assert conn.execute("SELECT COUNT(*) FROM metadata").fetchone()[0] == 0


def test_every_successful_run_writes_exactly_one_log():
    conn = connect(":memory:")
    collect(conn, load_config(), OfflineClient(ROOT / "fixtures" / "v1"),
            collected_at="2026-07-18T10:00:00+00:00")
    row = conn.execute("SELECT status, traceback FROM logs").fetchone()
    assert tuple(row) == ("success", None)
