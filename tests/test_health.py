from __future__ import annotations

from pathlib import Path

from kinea.client import OfflineClient
from kinea.collector import collect
from kinea.config import load_config
from kinea.db import connect
from kinea.health import format_source_health, source_health

ROOT = Path(__file__).resolve().parent.parent


def test_source_health_combines_quality_and_execution_logs():
    conn = connect(":memory:")
    config = load_config()
    collect(
        conn,
        config,
        OfflineClient(ROOT / "fixtures" / "v2"),
        collected_at="2026-07-18T10:00:00+00:00",
    )

    report = source_health(conn, config, as_of="2026-07-18")

    assert report.status == "pass"
    assert report.latest_success
    assert len(report.series) == 5
    assert "Status: PASS" in format_source_health(report)
