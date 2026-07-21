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


@given(st.lists(st.tuples(st.booleans(), FINITE_VALUES), min_size=1, max_size=15))
@settings(max_examples=50, deadline=1_000)
def test_random_same_day_and_cross_day_sequences_match_reference_replay(steps):
    """Fuzzes rule 4 (same-day update-in-place) interleaved with rules 1-3, which the other
    property test never exercises: it always advances vintage_date by one day per call, so no
    two of its calls ever share a vintage_date. Each step is (repeat_today, value); True re-uses
    the previous call's vintage_date (the same-day path), False advances to the next calendar
    day (the cross-day/revision path). A plain Python replay of the four literal rules is
    compared row-for-row against what ingest_observations actually wrote, including that an
    earlier day's row is left untouched by a later same-day update.
    """
    conn = _conn()
    day = date(2026, 1, 1)
    reference: list[list] = []  # [vintage_date, value], one entry per distinct vintage_date

    for index, (repeat_today, value) in enumerate(steps):
        if index > 0 and not repeat_today:
            day += timedelta(days=1)
        vintage = day.isoformat()
        # Strictly increasing regardless of how many same-day repeats occur, so "latest
        # collection of the day wins" is never ambiguous.
        collected_at = f"2026-01-01T00:{index:02d}:00+00:00"

        ingest_observations(
            conn,
            SERIES,
            [Observation("2025-12-01", value)],
            vintage_date=vintage,
            collected_at=collected_at,
        )

        if reference and reference[-1][0] == vintage:
            if not values_equal(reference[-1][1], value):
                reference[-1][1] = value  # rule 4: same-day update in place
        else:
            latest_value = reference[-1][1] if reference else None
            if latest_value is None or not values_equal(latest_value, value):
                reference.append([vintage, value])  # rules 1/3: first seen, or a real revision
            # else: unchanged on a new day -> rule 2, no-op, nothing appended

    stored = conn.execute(
        "SELECT vintage_date, value FROM time_series WHERE series_id = ? ORDER BY vintage_date",
        (SERIES,),
    ).fetchall()
    assert [row["vintage_date"] for row in stored] == [item[0] for item in reference]
    assert all(
        values_equal(row["value"], item[1]) for row, item in zip(stored, reference, strict=True)
    )


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
