#!/usr/bin/env python3
"""Minimal, executable proof of the assignment's vintage and as-of rules."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kinea.db import AS_OF_QUERY, CURRENT_QUERY, connect  # noqa: E402
from kinea.models import Observation  # noqa: E402
from kinea.vintages import ingest_observations  # noqa: E402


def main() -> None:
    conn = connect(":memory:")
    series_id = "CZ_HICP_CORE_INDEX"
    conn.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            observation_count, source_url, collected_at
        ) VALUES (?, 'Czechia - HICP Core Index', 'Derived demo', 'CZ',
                  'monthly', 'index', 0, 'https://data.ecb.europa.eu', ?)
        """,
        (series_id, "2026-07-01T10:00:00+00:00"),
    )
    observation_date = "2026-06-01"
    ingest_observations(
        conn, series_id, [Observation(observation_date, 100.0)],
        vintage_date="2026-07-01", collected_at="2026-07-01T10:00:00+00:00",
    )
    ingest_observations(
        conn, series_id, [Observation(observation_date, 101.0)],
        vintage_date="2026-07-18", collected_at="2026-07-18T10:00:00+00:00",
    )
    print("Coexisting vintages:")
    for row in conn.execute("SELECT * FROM time_series ORDER BY vintage_date"):
        print(dict(row))
    old = conn.execute(AS_OF_QUERY, {"as_of": "2026-07-10"}).fetchone()
    new = conn.execute(CURRENT_QUERY).fetchone()
    print("\nAs-of 2026-07-10:", dict(old))
    print("Current:", dict(new))
    assert old["value"] == 100.0 and new["value"] == 101.0
    print("\nPASS")


if __name__ == "__main__":
    main()
