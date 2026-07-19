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
