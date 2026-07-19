from __future__ import annotations

import csv

import pytest

from kinea.cli import main
from kinea.db import connect
from kinea.models import Observation
from kinea.panels import as_of_panel, knowledge_date_grid, write_panel
from kinea.vintages import ingest_observations

SERIES = "CZ_HICP_CORE_INDEX"


def _revision_db():
    conn = connect(":memory:")
    conn.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            observation_count, source_url, collected_at
        ) VALUES (?, 'name', 'description', 'CZ', 'monthly', 'index', 0, 'url', ?)
        """,
        (SERIES, "2026-07-01T10:00:00+00:00"),
    )
    ingest_observations(
        conn,
        SERIES,
        [Observation("2026-06-01", 102.79)],
        vintage_date="2026-07-01",
        collected_at="2026-07-01T10:00:00+00:00",
    )
    ingest_observations(
        conn,
        SERIES,
        [Observation("2026-06-01", 103.0)],
        vintage_date="2026-07-18",
        collected_at="2026-07-18T10:00:00+00:00",
    )
    conn.commit()
    return conn


def test_as_of_panel_never_leaks_a_later_vintage():
    rows = as_of_panel(_revision_db(), ["2026-07-10", "2026-07-18"])

    assert [(row.knowledge_date, row.value) for row in rows] == [
        ("2026-07-10", 102.79),
        ("2026-07-18", 103.0),
    ]
    assert all(row.vintage_date <= row.knowledge_date for row in rows)


def test_monthly_knowledge_grid_preserves_month_end_intent():
    assert knowledge_date_grid("2026-01-31", "2026-04-30") == (
        "2026-01-31",
        "2026-02-28",
        "2026-03-31",
        "2026-04-30",
    )


def test_panel_csv_has_stable_modeling_schema(tmp_path):
    rows = as_of_panel(_revision_db(), ["2026-07-10", "2026-07-18"])
    output = write_panel(rows, tmp_path / "panel.csv", "csv")

    with output.open(encoding="utf-8", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert list(exported[0]) == [
        "knowledge_date",
        "series_id",
        "reference_date",
        "value",
        "vintage_date",
        "collected_at",
    ]
    assert [row["value"] for row in exported] == ["102.79", "103.0"]


def test_panel_parquet_round_trip(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    rows = as_of_panel(_revision_db(), ["2026-07-10", "2026-07-18"])

    output = write_panel(rows, tmp_path / "panel.parquet", "parquet")
    frame = pd.read_parquet(output)

    assert frame[["knowledge_date", "value"]].to_dict("records") == [
        {"knowledge_date": "2026-07-10", "value": 102.79},
        {"knowledge_date": "2026-07-18", "value": 103.0},
    ]


def test_panel_cli_exports_selected_series(tmp_path):
    database = tmp_path / "revision.db"
    source = _revision_db()
    target = connect(database)
    source.backup(target)
    source.close()
    target.close()
    output = tmp_path / "pit.csv"

    exit_code = main(
        [
            "panel",
            "--db",
            str(database),
            "--as-of",
            "2026-07-10,2026-07-18",
            "--series",
            SERIES,
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.exists()
