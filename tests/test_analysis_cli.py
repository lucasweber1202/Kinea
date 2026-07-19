from __future__ import annotations

from pathlib import Path

from kinea.cli import main
from kinea.client import OfflineClient
from kinea.collector import collect
from kinea.config import load_config
from kinea.db import connect
from kinea.models import Observation
from kinea.vintages import ingest_observations

ROOT = Path(__file__).resolve().parent.parent


def _add_revision(conn, series_id: str) -> None:
    conn.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            observation_count, source_url, collected_at
        ) VALUES (?, 'name', 'description', 'CZ', 'monthly', 'index', 0, 'url', ?)
        """,
        (series_id, "2026-07-01T10:00:00+00:00"),
    )
    ingest_observations(
        conn,
        series_id,
        [Observation("2026-06-01", 100.0)],
        vintage_date="2026-07-01",
        collected_at="2026-07-01T10:00:00+00:00",
    )
    ingest_observations(
        conn,
        series_id,
        [Observation("2026-06-01", 101.0)],
        vintage_date="2026-07-18",
        collected_at="2026-07-18T10:00:00+00:00",
    )


def test_revisions_cli_filters_events_and_summary(tmp_path, capsys):
    database = tmp_path / "revisions.db"
    conn = connect(database)
    _add_revision(conn, "CZ_HICP_CORE_INDEX")
    _add_revision(conn, "CZ_HICP_FOOD_INDEX")
    conn.commit()
    conn.close()

    assert (
        main(
            [
                "revisions",
                "--db",
                str(database),
                "--series",
                "CZ_HICP_FOOD_INDEX",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "CZ_HICP_FOOD_INDEX" in output
    assert "CZ_HICP_CORE_INDEX" not in output


def test_quality_cli_returns_success_for_clean_database(tmp_path, capsys):
    database = tmp_path / "quality.db"
    conn = connect(database)
    collect(
        conn,
        load_config(),
        OfflineClient(ROOT / "fixtures" / "v2"),
        collected_at="2026-07-18T10:00:00+00:00",
    )
    conn.close()

    assert main(["quality", "--db", str(database), "--as-of", "2026-07-18"]) == 0
    output = capsys.readouterr().out
    assert "Status: PASS" in output
    assert "RESULT: PASS" in output


def test_diff_and_export_cli(tmp_path, capsys):
    database = tmp_path / "analysis.db"
    conn = connect(database)
    _add_revision(conn, "CZ_HICP_CORE_INDEX")
    conn.commit()
    conn.close()

    assert (
        main(
            [
                "diff",
                "--db",
                str(database),
                "--from",
                "2026-07-10",
                "--to",
                "2026-07-18",
            ]
        )
        == 0
    )
    assert "revised" in capsys.readouterr().out
    output = tmp_path / "snapshot.csv"
    assert (
        main(
            [
                "export",
                "--db",
                str(database),
                "--as-of",
                "2026-07-10",
                "--layout",
                "wide",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert output.exists()
    assert "Snapshot export" in capsys.readouterr().out


def test_collect_cli_can_select_one_series_and_dry_run(tmp_path, capsys):
    database = tmp_path / "dry-run.db"
    assert (
        main(
            [
                "collect",
                "--db",
                str(database),
                "--mode",
                "offline",
                "--fixtures",
                str(ROOT / "fixtures" / "v2"),
                "--series",
                "CZ_HICP_CORE_INDEX",
                "--dry-run",
            ]
        )
        == 0
    )
    conn = connect(database)
    assert conn.execute("SELECT COUNT(*) FROM metadata").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 0
    assert "dry_run=true" in capsys.readouterr().out


def test_feature_and_source_health_cli(tmp_path, capsys):
    database = tmp_path / "health.db"
    conn = connect(database)
    config = load_config()
    collect(
        conn,
        config,
        OfflineClient(ROOT / "fixtures" / "v2"),
        collected_at="2026-07-18T10:00:00+00:00",
    )
    conn.close()

    feature_output = tmp_path / "features.csv"
    assert (
        main(
            [
                "features",
                "--db",
                str(database),
                "--as-of",
                "2026-07-18",
                "--output",
                str(feature_output),
            ]
        )
        == 0
    )
    assert feature_output.exists()
    assert main(["source-health", "--db", str(database), "--as-of", "2026-07-18"]) == 0
    assert "Status: PASS" in capsys.readouterr().out
