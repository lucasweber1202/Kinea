"""Operational health summary combining collection logs, coverage and semantic quality."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from .client import LiveClient
from .config import Config
from .parser import parse_sdmx_csv
from .quality import evaluate_database


@dataclass(frozen=True)
class SeriesHealth:
    series_id: str
    quality: str
    last_observation: str | None
    live_status: int | None
    live_rows: int | None


@dataclass(frozen=True)
class SourceHealth:
    status: str
    as_of: str
    latest_success: str | None
    latest_error: str | None
    series: tuple[SeriesHealth, ...]


def source_health(
    conn: sqlite3.Connection,
    config: Config,
    *,
    as_of: str | None = None,
    live_client: LiveClient | None = None,
) -> SourceHealth:
    """Evaluate database health and optionally probe every configured live series."""
    check_date = as_of or date.today().isoformat()
    reports = {
        report.series_id: report for report in evaluate_database(conn, config, as_of=check_date)
    }
    latest_success = conn.execute(
        "SELECT MAX(finished_at) FROM logs WHERE status='success'"
    ).fetchone()[0]
    latest_error = conn.execute(
        "SELECT MAX(finished_at) FROM logs WHERE status='error'"
    ).fetchone()[0]
    rows = []
    for spec in config.series:
        live_status = None
        live_rows = None
        if live_client is not None:
            response = live_client.fetch(spec, {"lastNObservations": "3"})
            parsed = parse_sdmx_csv(response.body, expected_external_id=spec.external_id)
            live_status = response.http_status
            live_rows = len(parsed.observations)
        report = reports[spec.series_id]
        rows.append(
            SeriesHealth(
                series_id=spec.series_id,
                quality=report.status,
                last_observation=report.last_observation,
                live_status=live_status,
                live_rows=live_rows,
            )
        )
    healthy = all(
        row.quality != "error"
        and (row.live_status is None or row.live_status == 200)
        and (row.live_rows is None or row.live_rows > 0)
        for row in rows
    )
    return SourceHealth(
        status="pass" if healthy else "fail",
        as_of=check_date,
        latest_success=latest_success,
        latest_error=latest_error,
        series=tuple(rows),
    )


def format_source_health(report: SourceHealth) -> str:
    """Render a concise human-readable health report."""
    lines = [
        "SOURCE HEALTH",
        "=============",
        f"Status: {report.status.upper()}",
        f"As of: {report.as_of}",
        f"Latest success: {report.latest_success or 'none'}",
        f"Latest error: {report.latest_error or 'none'}",
        "",
        "series_id | quality | last_observation | live_status | live_rows",
    ]
    lines.extend(
        " | ".join(
            [
                row.series_id,
                row.quality,
                str(row.last_observation),
                str(row.live_status),
                str(row.live_rows),
            ]
        )
        for row in report.series
    )
    return "\n".join(lines)
