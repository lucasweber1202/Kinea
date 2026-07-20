from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from kinea.client import FetchError, OfflineClient
from kinea.collector import collect
from kinea.config import load_config
from kinea.db import connect, table_counts
from kinea.features import FeatureRecipe, default_feature_recipes, feature_panel
from kinea.health import format_source_health, source_health
from kinea.locking import _try_lock_windows
from kinea.models import Observation
from kinea.vintages import ingest_observations

ROOT = Path(__file__).resolve().parent.parent
SERIES = "CZ_HICP_CORE_INDEX"


def _metadata_database():
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
    return conn


def test_windows_lock_always_targets_existing_byte_zero(tmp_path):
    class FakeMsvcrt:
        LK_NBLCK = 1

        def __init__(self):
            self.calls = []

        def locking(self, fileno, mode, byte_count):
            self.calls.append((os.lseek(fileno, 0, os.SEEK_CUR), mode, byte_count))

    lock_path = tmp_path / "collector.db.lock"
    fake = FakeMsvcrt()
    with lock_path.open("a+b") as handle:
        assert _try_lock_windows(handle, fake)

    assert fake.calls == [(0, fake.LK_NBLCK, 1)]
    assert lock_path.stat().st_size >= 1


def test_calendar_aligned_yoy_refuses_a_missing_t_minus_12_month():
    conn = _metadata_database()
    references = ["2024-12-01"]
    references.extend(
        date(2025 + month // 12, month % 12 + 1, 1).isoformat() for month in range(1, 13)
    )
    observations = [
        Observation(reference, 100.0 + index) for index, reference in enumerate(references)
    ]
    ingest_observations(
        conn,
        SERIES,
        observations,
        vintage_date="2026-02-15",
        collected_at="2026-02-15T10:00:00+00:00",
    )

    recipe = FeatureRecipe(
        "core_yoy",
        SERIES,
        transform="pct_change",
        periods=12,
        period_unit="calendar_months",
    )
    assert feature_panel(conn, ["2026-02-15"], [recipe]) == ()

    default = next(
        item for item in default_feature_recipes(load_config()) if item.series_id == SERIES
    )
    assert default.period_unit == "calendar_months"


def test_dry_run_with_archive_directory_has_no_persistent_side_effects(tmp_path):
    conn = connect(":memory:")
    archive_dir = tmp_path / "raw"
    report = collect(
        conn,
        load_config(),
        OfflineClient(ROOT / "fixtures" / "v1"),
        collected_at="2026-07-18T10:00:00+00:00",
        dry_run=True,
        archive_dir=archive_dir,
    )

    assert report.dry_run
    assert table_counts(conn) == {"metadata": 0, "time_series": 0, "logs": 0}
    assert not archive_dir.exists()


def test_source_health_reports_one_live_failure_and_continues_other_series():
    conn = connect(":memory:")
    config = load_config()
    delegate = OfflineClient(ROOT / "fixtures" / "v2")
    collect(
        conn,
        config,
        delegate,
        collected_at="2026-07-18T10:00:00+00:00",
    )

    class PartiallyFailingClient:
        def fetch(self, spec, params=None):
            if spec.series_id == SERIES:
                raise FetchError("simulated endpoint outage")
            return delegate.fetch(spec, params)

    report = source_health(
        conn,
        config,
        as_of="2026-07-18",
        live_client=PartiallyFailingClient(),
    )

    assert report.status == "fail"
    assert len(report.series) == 5
    failed = next(item for item in report.series if item.series_id == SERIES)
    assert failed.live_error == "FetchError: simulated endpoint outage"
    assert sum(item.live_rows is not None for item in report.series) == 4
    assert "simulated endpoint outage" in format_source_health(report)


def test_rejects_retroactive_vintage_even_for_a_new_reference_date():
    conn = _metadata_database()
    ingest_observations(
        conn,
        SERIES,
        [Observation("2026-01-01", 100.0)],
        vintage_date="2026-03-10",
        collected_at="2026-03-10T10:00:00+00:00",
    )

    with pytest.raises(ValueError, match="non-monotonic"):
        ingest_observations(
            conn,
            SERIES,
            [Observation("2025-12-01", 99.0)],
            vintage_date="2026-02-10",
            collected_at="2026-02-10T10:00:00+00:00",
        )

    assert conn.execute("SELECT COUNT(*) FROM time_series").fetchone()[0] == 1
