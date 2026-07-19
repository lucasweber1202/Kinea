from __future__ import annotations

import csv
from datetime import date

import pytest

from kinea.db import connect
from kinea.features import FeatureRecipe, feature_panel, write_feature_panel
from kinea.models import Observation
from kinea.vintages import ingest_observations

SERIES = "CZ_HICP_CORE_INDEX"


def _month(year: int, month: int) -> str:
    return date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1).isoformat()


def _feature_database():
    conn = connect(":memory:")
    conn.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            observation_count, source_url, collected_at
        ) VALUES (?, 'name', 'description', 'CZ', 'monthly', 'index', 0, 'url', ?)
        """,
        (SERIES, "2026-01-15T10:00:00+00:00"),
    )
    observations = [Observation(_month(2025, index + 1), 100.0 + index) for index in range(13)]
    ingest_observations(
        conn,
        SERIES,
        observations,
        vintage_date="2026-01-15",
        collected_at="2026-01-15T10:00:00+00:00",
    )
    ingest_observations(
        conn,
        SERIES,
        [Observation("2026-01-01", 120.0)],
        vintage_date="2026-02-15",
        collected_at="2026-02-15T10:00:00+00:00",
    )
    return conn


def test_feature_panel_is_vintage_safe_across_revision():
    recipe = FeatureRecipe("core_yoy", SERIES, "pct_change", 12)
    rows = feature_panel(_feature_database(), ["2026-01-20", "2026-02-20"], [recipe])

    assert [row.knowledge_date for row in rows] == ["2026-01-20", "2026-02-20"]
    assert [row.value for row in rows] == pytest.approx([12.0, 20.0])


def test_feature_panel_wide_export(tmp_path):
    recipe = FeatureRecipe("core_yoy", SERIES, "pct_change", 12)
    rows = feature_panel(_feature_database(), ["2026-01-20", "2026-02-20"], [recipe])
    output = write_feature_panel(rows, tmp_path / "features.csv")

    with output.open(encoding="utf-8", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert list(exported[0]) == ["knowledge_date", "core_yoy"]
    assert len(exported) == 2


def test_monthly_mean_aggregation_for_daily_feature():
    conn = connect(":memory:")
    conn.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            observation_count, source_url, collected_at
        ) VALUES (?, 'name', 'description', 'CZ', 'daily', 'currency', 0, 'url', ?)
        """,
        ("CZ_FX_EURCZK", "2026-02-01T10:00:00+00:00"),
    )
    ingest_observations(
        conn,
        "CZ_FX_EURCZK",
        [Observation("2026-01-02", 24.0), Observation("2026-01-03", 26.0)],
        vintage_date="2026-02-01",
        collected_at="2026-02-01T10:00:00+00:00",
    )
    rows = feature_panel(
        conn,
        ["2026-02-01"],
        [FeatureRecipe("fx_mean", "CZ_FX_EURCZK", aggregation="mean_month")],
    )
    assert rows[0].value == 25.0
