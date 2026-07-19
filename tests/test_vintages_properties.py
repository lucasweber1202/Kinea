from __future__ import annotations

from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from kinea.db import connect
from kinea.models import Observation
from kinea.panels import as_of_panel
from kinea.vintages import ingest_observations, values_equal

SERIES = "CZ_HICP_CORE_INDEX"
FINITE_VALUES = st.floats(
    min_value=50.0,
    max_value=150.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)


def _conn():
    conn = connect(":memory:")
    conn.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            observation_count, source_url, collected_at
        ) VALUES (?, 'name', 'description', 'CZ', 'monthly', 'index', 0, 'url', ?)
        """,
        (SERIES, "2026-01-01T00:00:00+00:00"),
    )
    return conn


@given(st.lists(FINITE_VALUES, min_size=1, max_size=10))
@settings(max_examples=25, deadline=1_000)
def test_random_revision_sequences_preserve_history_without_lookahead(values):
    conn = _conn()
    start = date(2026, 1, 1)
    retained: list[tuple[str, float]] = []
    current_value: float | None = None

    for offset, value in enumerate(values):
        vintage = (start + timedelta(days=offset)).isoformat()
        ingest_observations(
            conn,
            SERIES,
            [Observation("2025-12-01", value)],
            vintage_date=vintage,
            collected_at=f"{vintage}T10:00:00+00:00",
        )
        if current_value is None or not values_equal(current_value, value):
            retained.append((vintage, value))
            current_value = value

    stored = conn.execute(
        "SELECT vintage_date, value FROM time_series ORDER BY vintage_date"
    ).fetchall()
    assert len(stored) == len(retained)
    assert [row["vintage_date"] for row in stored] == [item[0] for item in retained]

    panel = as_of_panel(conn, [item[0] for item in retained])
    assert all(row.vintage_date <= row.knowledge_date for row in panel)
    assert [row.vintage_date for row in panel] == [item[0] for item in retained]


@given(st.lists(FINITE_VALUES, min_size=1, max_size=10))
@settings(max_examples=20, deadline=1_000)
def test_random_unchanged_reruns_are_idempotent(values):
    conn = _conn()
    observations = [
        Observation(f"2025-{index + 1:02d}-01", value) for index, value in enumerate(values)
    ]
    ingest_observations(
        conn,
        SERIES,
        observations,
        vintage_date="2026-01-01",
        collected_at="2026-01-01T10:00:00+00:00",
    )
    before = conn.execute("SELECT COUNT(*) FROM time_series").fetchone()[0]

    counts = ingest_observations(
        conn,
        SERIES,
        observations,
        vintage_date="2026-02-01",
        collected_at="2026-02-01T10:00:00+00:00",
    )

    assert conn.execute("SELECT COUNT(*) FROM time_series").fetchone()[0] == before
    assert counts.unchanged == len(observations)
