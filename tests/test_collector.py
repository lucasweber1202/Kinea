from pathlib import Path

import pytest

from kinea.client import FetchError, FetchResult, OfflineClient
from kinea.collector import collect
from kinea.config import load_config
from kinea.db import connect, table_counts

ROOT = Path(__file__).resolve().parent.parent


def test_offline_collection_populates_exact_schema():
    conn = connect(":memory:")
    report = collect(
        conn,
        load_config(),
        OfflineClient(ROOT / "fixtures" / "v2"),
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
    collect(
        conn,
        config,
        OfflineClient(ROOT / "fixtures" / "v1"),
        collected_at="2026-07-01T10:00:00+00:00",
    )
    collect(
        conn,
        config,
        OfflineClient(ROOT / "fixtures" / "v2"),
        collected_at="2026-07-18T10:00:00+00:00",
    )
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
        collect(
            conn,
            load_config(),
            OfflineClient(ROOT / "fixtures" / "missing"),
            collected_at="2026-07-18T10:00:00+00:00",
        )
    log = conn.execute("SELECT status, traceback FROM logs").fetchone()
    assert log["status"] == "error"
    assert "FetchError" in log["traceback"]
    assert conn.execute("SELECT COUNT(*) FROM metadata").fetchone()[0] == 0


def test_failed_execution_writes_exactly_one_complete_log():
    conn = connect(":memory:")
    with pytest.raises(FetchError):
        collect(
            conn,
            load_config(),
            OfflineClient(ROOT / "fixtures" / "missing"),
            collected_at="2026-07-18T10:00:00+00:00",
        )
    rows = conn.execute("SELECT * FROM logs").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["started_at"] and row["finished_at"]
    assert row["status"] == "error"
    assert row["log_text"]
    assert "Traceback" in row["traceback"]


def test_every_successful_run_writes_exactly_one_log():
    conn = connect(":memory:")
    collect(
        conn,
        load_config(),
        OfflineClient(ROOT / "fixtures" / "v1"),
        collected_at="2026-07-18T10:00:00+00:00",
    )
    row = conn.execute("SELECT status, traceback FROM logs").fetchone()
    assert tuple(row) == ("success", None)


def test_success_log_has_timestamps_and_summary():
    conn = connect(":memory:")
    collect(
        conn,
        load_config(),
        OfflineClient(ROOT / "fixtures" / "v1"),
        collected_at="2026-07-18T10:00:00+00:00",
    )
    row = conn.execute("SELECT * FROM logs").fetchone()
    assert row["started_at"] == "2026-07-18T10:00:00+00:00"
    assert row["finished_at"]
    assert row["status"] == "success"
    assert "series=5" in row["log_text"]
    assert row["traceback"] is None


def test_external_values_are_bound_as_sql_parameters():
    class InjectionClient:
        mode = "offline"

        def fetch(self, spec, params=None):
            del params
            body = f"KEY,TIME_PERIOD,OBS_VALUE\n{spec.external_id},2026-06,101.2\n"
            return FetchResult(
                body=body,
                source_url="x'); DROP TABLE metadata; --",
                http_status=200,
                fetched_at="2026-07-18T10:00:00+00:00",
            )

    conn = connect(":memory:")
    collect(conn, load_config(), InjectionClient(), collected_at="2026-07-18T10:00:00+00:00")
    assert conn.execute("SELECT COUNT(*) FROM metadata").fetchone()[0] == 5
    assert (
        conn.execute("SELECT DISTINCT source_url FROM metadata").fetchone()[0]
        == "x'); DROP TABLE metadata; --"
    )


def test_zero_valid_observations_is_a_grave_failure():
    class EmptyClient:
        mode = "live"

        def fetch(self, spec, params=None):
            del params
            return FetchResult(
                body=f"KEY,TIME_PERIOD,OBS_VALUE\n{spec.external_id},bad-date,not-a-number\n",
                source_url="https://data-api.ecb.europa.eu/test",
                http_status=200,
                fetched_at="2026-07-18T10:00:00+00:00",
            )

    conn = connect(":memory:")
    with pytest.warns(RuntimeWarning), pytest.raises(ValueError, match="zero valid observations"):
        collect(
            conn,
            load_config(),
            EmptyClient(),
            collected_at="2026-07-18T10:00:00+00:00",
        )
    assert table_counts(conn) == {"metadata": 0, "time_series": 0, "logs": 1}
    log = conn.execute("SELECT status, traceback FROM logs").fetchone()
    assert log["status"] == "error"
    assert "zero valid observations" in log["traceback"]
