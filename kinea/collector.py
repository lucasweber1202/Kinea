"""Collection orchestration: fetch, parse, ingest, refresh metadata, always log."""

from __future__ import annotations

import json
import sqlite3
import traceback as traceback_module
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .archive import archive_response
from .client import FetchResult, LiveClient, OfflineClient
from .config import Config, SeriesSpec
from .db import create_schema
from .identifiers import derive_description, derive_name, parse_series_id
from .models import IngestCounts, Observation
from .parser import ParseResult, parse_sdmx_csv
from .quality import (
    DataQualityError,
    QualityReport,
    blocking_issues,
    evaluate_observations,
)
from .vintages import ingest_observations


class Client(Protocol):
    mode: str

    def fetch(self, spec: SeriesSpec, params: dict[str, str] | None = None) -> FetchResult: ...


@dataclass(frozen=True)
class CollectReport:
    status: str
    counts: IngestCounts
    series_collected: int
    warnings: tuple[str, ...]
    quality_reports: tuple[QualityReport, ...]
    log_text: str
    run_id: str = ""
    dry_run: bool = False


@dataclass(frozen=True)
class PreparedSeries:
    """Fetched and validated payload ready for a short database transaction."""

    spec: SeriesSpec
    result: FetchResult
    parsed: ParseResult
    quality: QualityReport


def build_client(
    config: Config, mode: str, fixtures: str | None = None
) -> LiveClient | OfflineClient:
    if mode == "offline":
        if not fixtures:
            raise ValueError("--fixtures is required in offline mode")
        return OfflineClient(fixtures)
    return LiveClient(config)


def _ensure_metadata(
    conn: sqlite3.Connection,
    spec: SeriesSpec,
    source_url: str,
    collected_at: str,
    last_publish_date: str | None,
) -> None:
    country = parse_series_id(spec.series_id).country
    conn.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            first_observation, last_observation, observation_count,
            source_url, last_publish_date, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?, ?, ?)
        ON CONFLICT(series_id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            country = excluded.country,
            frequency = excluded.frequency,
            unit = excluded.unit,
            source_url = excluded.source_url,
            last_publish_date = COALESCE(excluded.last_publish_date, metadata.last_publish_date),
            collected_at = excluded.collected_at
        """,
        (
            spec.series_id,
            derive_name(spec.series_id),
            derive_description(spec.series_id),
            country,
            spec.frequency,
            spec.unit,
            source_url,
            last_publish_date,
            collected_at,
        ),
    )


def _refresh_metadata(conn: sqlite3.Connection, series_id: str) -> None:
    conn.execute(
        """
        UPDATE metadata
           SET first_observation = (
                   SELECT MIN(reference_date) FROM time_series WHERE series_id = ?
               ),
               last_observation = (
                   SELECT MAX(reference_date) FROM time_series WHERE series_id = ?
               ),
               observation_count = (
                   SELECT COUNT(DISTINCT reference_date) FROM time_series WHERE series_id = ?
               )
         WHERE series_id = ?
        """,
        (series_id, series_id, series_id, series_id),
    )


def _previous_observation(
    conn: sqlite3.Connection, series_id: str, before: str
) -> tuple[str, float] | None:
    row = conn.execute(
        """
        SELECT reference_date, value
        FROM (
            SELECT t.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY reference_date
                       ORDER BY vintage_date DESC, collected_at DESC
                   ) AS rn
            FROM time_series t
            WHERE series_id = ? AND reference_date < ?
        ) ranked
        WHERE rn = 1
        ORDER BY reference_date DESC
        LIMIT 1
        """,
        (series_id, before),
    ).fetchone()
    if row is None:
        return None
    return row["reference_date"], float(row["value"])


def _series_metric(prepared: PreparedSeries) -> dict[str, object]:
    result = prepared.result
    return {
        "series_id": prepared.spec.series_id,
        "http_status": result.http_status,
        "attempts": result.attempt_count,
        "duration_ms": round(result.duration_ms, 1),
        "bytes": result.response_bytes or len(result.body.encode("utf-8")),
        "observations": len(prepared.parsed.observations),
        "parser_warnings": len(prepared.parsed.warnings),
        "quality": prepared.quality.status,
    }


def _log_summary(
    *,
    mode: str,
    run_id: str,
    series: int,
    counts: IngestCounts,
    warning_count: int,
    quality_status: str,
    quality_issue_count: int,
    prepared: list[PreparedSeries],
    dry_run: bool,
) -> str:
    metrics = json.dumps([_series_metric(item) for item in prepared], separators=(",", ":"))
    return (
        f"run_id={run_id}; mode={mode}; dry_run={str(dry_run).lower()}; series={series}; "
        f"seen={counts.seen}; inserted={counts.inserted}; revised={counts.revised}; "
        f"updated_same_day={counts.updated_same_day}; unchanged={counts.unchanged}; "
        f"warnings={warning_count}; quality={quality_status}; "
        f"quality_issues={quality_issue_count}; series_metrics={metrics}"
    )


def collect(
    conn: sqlite3.Connection,
    config: Config,
    client: Client,
    *,
    params: dict[str, str] | None = None,
    collected_at: str | None = None,
    vintage_date: str | None = None,
    quality_policy: str = "warn",
    dry_run: bool = False,
    archive_dir: str | Path | None = None,
    run_id: str | None = None,
) -> CollectReport:
    """Fetch/validate first, then apply one short atomic write transaction.

    Normal executions always write exactly one log row in ``finally``. A dry run produces no
    persistent side effects: database changes, execution logs and raw-payload archives are skipped.
    """
    started = collected_at or datetime.now(timezone.utc).isoformat()
    vintage = vintage_date or started[:10]
    collection_id = run_id or uuid4().hex
    counts = IngestCounts()
    warning_messages: list[str] = []
    quality_reports: list[QualityReport] = []
    prepared_series: list[PreparedSeries] = []
    series_collected = 0
    status = "error"
    log_text = "collection failed before completion"
    traceback_text: str | None = None

    try:
        # Schema creation lives inside the try so a transient failure here (lock contention,
        # a permissions/disk-full error) still flows through the except/finally logic below and
        # produces one status='error' log row, instead of escaping before any row is written.
        create_schema(conn)
        # Network I/O and semantic validation happen without holding a SQLite write lock.
        for spec in config.series:
            result = client.fetch(spec, params=params)
            if archive_dir is not None and not dry_run:
                archive_response(archive_dir, spec, result, run_id=collection_id)
            parsed = parse_sdmx_csv(result.body, expected_external_id=spec.external_id)
            warning_messages.extend(f"{spec.series_id}: {message}" for message in parsed.warnings)
            previous_row = (
                None
                if not parsed.observations
                else _previous_observation(
                    conn, spec.series_id, parsed.observations[0].reference_date
                )
            )
            previous = None if previous_row is None else Observation(*previous_row)
            quality = evaluate_observations(
                spec,
                parsed.observations,
                as_of=vintage,
                previous=previous,
            )
            quality_reports.append(quality)
            blocked = blocking_issues(quality, policy=quality_policy)
            warning_messages.extend(
                f"{spec.series_id}: quality {issue.code}: {issue.message}"
                for issue in quality.issues
                if issue not in blocked
            )
            if blocked:
                details = "; ".join(f"{issue.code}: {issue.message}" for issue in blocked)
                raise DataQualityError(f"{spec.series_id}: semantic quality gate failed: {details}")
            prepared_series.append(PreparedSeries(spec, result, parsed, quality))

        # Keep the write lock only for the set-based ingest and metadata refresh.
        conn.execute("BEGIN IMMEDIATE")
        for prepared in prepared_series:
            spec = prepared.spec
            result = prepared.result
            parsed = prepared.parsed
            _ensure_metadata(conn, spec, result.source_url, started, parsed.last_publish_date)
            series_counts = ingest_observations(
                conn,
                spec.series_id,
                parsed.observations,
                vintage_date=vintage,
                collected_at=started,
            )
            counts.add(series_counts)
            _refresh_metadata(conn, spec.series_id)
            series_collected += 1

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

        status = "success"
        quality_issue_count = sum(len(report.issues) for report in quality_reports)
        quality_status = "warning" if quality_issue_count else "pass"
        log_text = _log_summary(
            mode=client.mode,
            run_id=collection_id,
            series=series_collected,
            counts=counts,
            warning_count=len(warning_messages),
            quality_status=quality_status,
            quality_issue_count=quality_issue_count,
            prepared=prepared_series,
            dry_run=dry_run,
        )
        return CollectReport(
            status=status,
            counts=counts,
            series_collected=series_collected,
            warnings=tuple(warning_messages),
            quality_reports=tuple(quality_reports),
            log_text=log_text,
            run_id=collection_id,
            dry_run=dry_run,
        )
    except Exception as exc:
        conn.rollback()
        traceback_text = traceback_module.format_exc()
        quality_issue_count = sum(len(report.issues) for report in quality_reports)
        quality_status = "error" if isinstance(exc, DataQualityError) else "not_run"
        log_text = _log_summary(
            mode=client.mode,
            run_id=collection_id,
            series=series_collected,
            counts=counts,
            warning_count=len(warning_messages),
            quality_status=quality_status,
            quality_issue_count=quality_issue_count,
            prepared=prepared_series,
            dry_run=dry_run,
        )
        raise
    finally:
        if not dry_run:
            finished = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO logs (started_at, finished_at, status, log_text, traceback)
                VALUES (?, ?, ?, ?, ?)
                """,
                (started, finished, status, log_text, traceback_text),
            )
            conn.commit()
