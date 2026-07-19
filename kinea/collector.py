"""Collection orchestration: fetch, parse, ingest, refresh metadata, always log."""

from __future__ import annotations

import sqlite3
import traceback as traceback_module
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .client import FetchResult, LiveClient, OfflineClient
from .config import Config, SeriesSpec
from .db import create_schema
from .identifiers import derive_description, derive_name, parse_series_id
from .models import IngestCounts
from .parser import parse_sdmx_csv
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
    log_text: str


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


def collect(
    conn: sqlite3.Connection,
    config: Config,
    client: Client,
    *,
    params: dict[str, str] | None = None,
    collected_at: str | None = None,
    vintage_date: str | None = None,
) -> CollectReport:
    """Run one atomic collection and write exactly one log row in ``finally``."""

    create_schema(conn)
    started = collected_at or datetime.now(timezone.utc).isoformat()
    vintage = vintage_date or started[:10]
    counts = IngestCounts()
    warning_messages: list[str] = []
    series_collected = 0
    status = "error"
    log_text = "collection failed before completion"
    traceback_text: str | None = None

    try:
        conn.execute("BEGIN")
        for spec in config.series:
            result = client.fetch(spec, params=params)
            parsed = parse_sdmx_csv(result.body, expected_external_id=spec.external_id)
            warning_messages.extend(f"{spec.series_id}: {message}" for message in parsed.warnings)
            if not parsed.observations:
                raise ValueError(f"{spec.series_id}: response contained zero valid observations")
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
        conn.commit()
        status = "success"
        log_text = (
            f"mode={client.mode}; series={series_collected}; seen={counts.seen}; "
            f"inserted={counts.inserted}; revised={counts.revised}; "
            f"updated_same_day={counts.updated_same_day}; unchanged={counts.unchanged}; "
            f"warnings={len(warning_messages)}"
        )
        return CollectReport(
            status=status,
            counts=counts,
            series_collected=series_collected,
            warnings=tuple(warning_messages),
            log_text=log_text,
        )
    except Exception:
        conn.rollback()
        traceback_text = traceback_module.format_exc()
        log_text = f"mode={client.mode}; collection aborted after {series_collected} series"
        raise
    finally:
        finished = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO logs (started_at, finished_at, status, log_text, traceback)
            VALUES (?, ?, ?, ?, ?)
            """,
            (started, finished, status, log_text, traceback_text),
        )
        conn.commit()
