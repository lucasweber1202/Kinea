from __future__ import annotations

import csv

from kinea.db import connect
from kinea.exports import snapshot_rows, write_snapshot
from kinea.models import Observation
from kinea.vintages import ingest_observations


def _database():
    conn = connect(":memory:")
    conn.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            observation_count, source_url, collected_at
        ) VALUES (?, 'name', 'description', 'CZ', 'monthly', 'index', 0, 'url', ?)
        """,
        ("CZ_HICP_CORE_INDEX", "2026-07-01T10:00:00+00:00"),
    )
    ingest_observations(
        conn,
        "CZ_HICP_CORE_INDEX",
        [Observation("2026-06-01", 100.0)],
        vintage_date="2026-07-01",
        collected_at="2026-07-01T10:00:00+00:00",
    )
    ingest_observations(
        conn,
        "CZ_HICP_CORE_INDEX",
        [Observation("2026-06-01", 101.0)],
        vintage_date="2026-07-18",
        collected_at="2026-07-18T10:00:00+00:00",
    )
    return conn


def test_snapshot_export_respects_as_of_date(tmp_path):
    rows = snapshot_rows(_database(), as_of="2026-07-10")
    output = write_snapshot(rows, tmp_path / "snapshot.csv")

    with output.open(encoding="utf-8", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert exported[0]["value"] == "100.0"
    assert exported[0]["vintage_date"] == "2026-07-01"


def test_wide_snapshot_has_series_columns(tmp_path):
    rows = snapshot_rows(_database())
    output = write_snapshot(rows, tmp_path / "wide.csv", layout="wide")

    with output.open(encoding="utf-8", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert list(exported[0]) == ["reference_date", "CZ_HICP_CORE_INDEX"]
    assert exported[0]["CZ_HICP_CORE_INDEX"] == "101.0"
