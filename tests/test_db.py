from kinea.db import AS_OF_QUERY, connect


def _metadata(conn, series_id="CZ_HICP_CORE_INDEX"):
    conn.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            observation_count, source_url, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (series_id, "name", "description", "CZ", "monthly", "index", 0,
         "https://example.test", "2026-01-01T00:00:00+00:00"),
    )


def test_schema_contains_only_required_tables():
    conn = connect(":memory:")
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    assert names == {"metadata", "time_series", "logs"}


def test_time_series_has_exact_assignment_columns():
    conn = connect(":memory:")
    columns = [row[1] for row in conn.execute("PRAGMA table_info(time_series)")]
    assert columns == ["series_id", "reference_date", "vintage_date", "value", "collected_at"]


def test_current_query_selects_latest_vintage():
    conn = connect(":memory:")
    _metadata(conn)
    conn.executemany(
        "INSERT INTO time_series VALUES (?, ?, ?, ?, ?)",
        [
            ("CZ_HICP_CORE_INDEX", "2026-01-01", "2026-02-01", 100.0, "2026-02-01T10:00:00+00:00"),
            ("CZ_HICP_CORE_INDEX", "2026-01-01", "2026-03-01", 101.0, "2026-03-01T10:00:00+00:00"),
        ],
    )
    from kinea.db import CURRENT_QUERY
    row = conn.execute(CURRENT_QUERY).fetchone()
    assert (row["value"], row["vintage_date"]) == (101.0, "2026-03-01")


def test_as_of_query_selects_historical_vintage():
    conn = connect(":memory:")
    _metadata(conn)
    conn.executemany(
        "INSERT INTO time_series VALUES (?, ?, ?, ?, ?)",
        [
            ("CZ_HICP_CORE_INDEX", "2026-01-01", "2026-02-01", 100.0, "2026-02-01T10:00:00+00:00"),
            ("CZ_HICP_CORE_INDEX", "2026-01-01", "2026-03-01", 101.0, "2026-03-01T10:00:00+00:00"),
        ],
    )
    row = conn.execute(AS_OF_QUERY, {"as_of": "2026-02-28"}).fetchone()
    assert (row["value"], row["vintage_date"]) == (100.0, "2026-02-01")
