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


def test_revisions_cli_reports_reliability_section(tmp_path, capsys):
    database = tmp_path / "revisions.db"
    conn = connect(database)
    _add_revision(conn, "CZ_HICP_CORE_INDEX")
    conn.commit()
    conn.close()

    assert main(["revisions", "--db", str(database)]) == 0
    output = capsys.readouterr().out
    assert "reliability (trust in the latest print" in output
    assert "CZ_HICP_CORE_INDEX: revised=1/1" in output


def _seed_monthly_pair(conn) -> None:
    """A minimal FX + one HICP component pair so passthrough/diffusion/base-effects have
    something non-trivial to compute over."""
    for series_id, frequency in (("CZ_FX_EURCZK", "daily"), ("CZ_HICP_ENERGY_INDEX", "monthly")):
        conn.execute(
            """
            INSERT INTO metadata (series_id, name, description, country, frequency, unit,
                                  observation_count, source_url, collected_at)
            VALUES (?, 'n', 'd', 'CZ', ?, 'index', 0, 'https://x', '2026-07-01T10:00:00+00:00')
            """,
            (series_id, frequency),
        )
    fx_obs = [Observation(f"2025-{m:02d}-15", 25.0 + m * 0.1) for m in range(1, 9)]
    hicp_obs = [Observation(f"2025-{m:02d}-01", 100.0 + m * 0.5) for m in range(1, 9)]
    ingest_observations(
        conn,
        "CZ_FX_EURCZK",
        fx_obs,
        vintage_date="2026-07-19",
        collected_at="2026-07-19T10:00:00+00:00",
    )
    ingest_observations(
        conn,
        "CZ_HICP_ENERGY_INDEX",
        hicp_obs,
        vintage_date="2026-07-19",
        collected_at="2026-07-19T10:00:00+00:00",
    )


def test_passthrough_cli_reports_best_lag(tmp_path, capsys):
    database = tmp_path / "passthrough.db"
    conn = connect(database)
    _seed_monthly_pair(conn)
    conn.commit()
    conn.close()

    assert (
        main(
            [
                "passthrough",
                "--db",
                str(database),
                "--series",
                "CZ_HICP_ENERGY_INDEX",
                "--max-lag",
                "2",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "CZ_HICP_ENERGY_INDEX vs CZ_FX_EURCZK" in output
    assert "best lag:" in output
    assert "lag_months | correlation | n_pairs" in output


def test_diffusion_cli_lists_monthly_readings(tmp_path, capsys):
    database = tmp_path / "diffusion.db"
    conn = connect(database)
    _seed_monthly_pair(conn)
    conn.commit()
    conn.close()

    assert main(["diffusion", "--db", str(database), "--series", "CZ_HICP_ENERGY_INDEX"]) == 0
    output = capsys.readouterr().out
    assert "reference_date | accel | decel | flat | diffusion | dominant_component" in output
    assert "2025-03-01" in output  # first month with a comparable prior MoM


def test_base_effects_cli_reports_insufficient_history(tmp_path, capsys):
    database = tmp_path / "base_effects.db"
    conn = connect(database)
    _seed_monthly_pair(conn)  # only 8 months -- fewer than the 13 a YoY figure needs
    conn.commit()
    conn.close()

    assert main(["base-effects", "--db", str(database), "--series", "CZ_HICP_ENERGY_INDEX"]) == 0
    output = capsys.readouterr().out
    assert "Not enough history" in output


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
