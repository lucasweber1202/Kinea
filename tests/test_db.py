from kinea.db import AS_OF_QUERY, connect, get_as_of_view, get_current_view


def _metadata(conn, series_id="CZ_HICP_CORE_INDEX"):
    conn.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            observation_count, source_url, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            series_id,
            "name",
            "description",
            "CZ",
            "monthly",
            "index",
            0,
            "https://example.test",
            "2026-01-01T00:00:00+00:00",
        ),
    )


def test_schema_contains_only_required_tables():
    conn = connect(":memory:")
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert names == {"metadata", "time_series", "logs"}


def test_time_series_has_exact_assignment_columns():
    conn = connect(":memory:")
    columns = [row[1] for row in conn.execute("PRAGMA table_info(time_series)")]
    assert columns == ["series_id", "reference_date", "vintage_date", "value", "collected_at"]


def test_metadata_has_exact_assignment_columns():
    conn = connect(":memory:")
    columns = [row[1] for row in conn.execute("PRAGMA table_info(metadata)")]
    assert columns == [
        "series_id",
        "name",
        "description",
        "country",
        "frequency",
        "unit",
        "first_observation",
        "last_observation",
        "observation_count",
        "source_url",
        "last_publish_date",
        "collected_at",
    ]


def test_logs_has_exact_assignment_columns():
    conn = connect(":memory:")
    columns = [row[1] for row in conn.execute("PRAGMA table_info(logs)")]
    assert columns == ["id", "started_at", "finished_at", "status", "log_text", "traceback"]


def test_composite_primary_key_is_exact():
    conn = connect(":memory:")
    pk = {row[1]: row[5] for row in conn.execute("PRAGMA table_info(time_series)") if row[5]}
    assert pk == {"series_id": 1, "reference_date": 2, "vintage_date": 3}


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


def test_explicit_view_functions_select_old_and_new_values():
    conn = connect(":memory:")
    _metadata(conn)
    conn.executemany(
        "INSERT INTO time_series VALUES (?, ?, ?, ?, ?)",
        [
            ("CZ_HICP_CORE_INDEX", "2026-01-01", "2026-02-01", 100.0, "2026-02-01T10:00:00+00:00"),
            ("CZ_HICP_CORE_INDEX", "2026-01-01", "2026-03-01", 101.0, "2026-03-01T10:00:00+00:00"),
        ],
    )
    assert get_as_of_view(conn, "2026-02-28")[0]["value"] == 100.0
    assert get_as_of_view(conn, "2026-03-01")[0]["value"] == 101.0
    assert get_current_view(conn)[0]["value"] == 101.0


def test_unrevised_series_is_identical_current_and_as_of():
    conn = connect(":memory:")
    _metadata(conn)
    conn.execute(
        "INSERT INTO time_series VALUES (?, ?, ?, ?, ?)",
        ("CZ_HICP_CORE_INDEX", "2026-01-01", "2026-02-01", 100.0, "2026-02-01T10:00:00+00:00"),
    )
    assert get_current_view(conn)[0]["value"] == get_as_of_view(conn, "2026-12-31")[0]["value"]
