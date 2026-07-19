"""SQLite schema and read queries matching assignment section 5 exactly."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    series_id           VARCHAR(200) NOT NULL,
    name                VARCHAR(500) NOT NULL,
    description         VARCHAR(2000),
    country             VARCHAR(3) NOT NULL,
    frequency           VARCHAR(20),
    unit                VARCHAR(50),
    first_observation   DATE,
    last_observation    DATE,
    observation_count   INTEGER NOT NULL,
    source_url          VARCHAR(1000) NOT NULL,
    last_publish_date   DATE,
    collected_at        TIMESTAMP NOT NULL,
    CONSTRAINT pk_metadata PRIMARY KEY (series_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS time_series (
    series_id       VARCHAR(200) NOT NULL,
    reference_date  DATE NOT NULL,
    vintage_date    DATE NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    collected_at    TIMESTAMP NOT NULL,
    CONSTRAINT pk_time_series PRIMARY KEY (series_id, reference_date, vintage_date),
    CONSTRAINT fk_time_series_metadata FOREIGN KEY (series_id)
        REFERENCES metadata(series_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TIMESTAMP NOT NULL,
    finished_at  TIMESTAMP NOT NULL,
    status       VARCHAR(20) NOT NULL,
    log_text     VARCHAR(65535) NOT NULL,
    traceback    VARCHAR(65535)
);
"""

CURRENT_QUERY = """
SELECT series_id, reference_date, value, vintage_date, collected_at
FROM (
    SELECT t.*, ROW_NUMBER() OVER (
        PARTITION BY series_id, reference_date
        ORDER BY vintage_date DESC, collected_at DESC
    ) AS rn
    FROM time_series t
) r
WHERE rn = 1
ORDER BY series_id, reference_date
"""

AS_OF_QUERY = """
SELECT series_id, reference_date, value, vintage_date, collected_at
FROM (
    SELECT t.*, ROW_NUMBER() OVER (
        PARTITION BY series_id, reference_date
        ORDER BY vintage_date DESC, collected_at DESC
    ) AS rn
    FROM time_series t
    WHERE vintage_date <= :as_of
) r
WHERE rn = 1
ORDER BY series_id, reference_date
"""


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    create_schema(conn)
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def current_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(CURRENT_QUERY).fetchall()


def as_of_rows(conn: sqlite3.Connection, as_of: str) -> list[sqlite3.Row]:
    return conn.execute(AS_OF_QUERY, {"as_of": as_of}).fetchall()


def get_current_view(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return the latest known vintage for every series/reference-date pair."""

    return current_rows(conn)


def get_as_of_view(conn: sqlite3.Connection, as_of_date: str) -> list[sqlite3.Row]:
    """Return the latest vintage known on or before ``as_of_date``."""

    return as_of_rows(conn, as_of_date)


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("metadata", "time_series", "logs")
    }
