from __future__ import annotations

import pytest

from kinea.client import FetchResult
from kinea.collector import collect
from kinea.config import SeriesSpec, load_config
from kinea.db import connect, table_counts
from kinea.models import Observation
from kinea.quality import DataQualityError, evaluate_observations, format_quality_report


def _monthly_spec() -> SeriesSpec:
    return SeriesSpec(
        series_id="CZ_HICP_CORE_INDEX",
        external_id="HICP.M.CZ.N.XEF000.4D0.INX",
        dataflow="HICP",
        sdmx_key="M.CZ.N.XEF000.4D0.INX",
        frequency="monthly",
        unit="index",
        min_value=20.0,
        max_value=300.0,
        max_change_pct=45.0,
        stale_after_days=75,
    )


def _daily_spec() -> SeriesSpec:
    return SeriesSpec(
        series_id="CZ_FX_EURCZK",
        external_id="EXR.D.CZK.EUR.SP00.A",
        dataflow="EXR",
        sdmx_key="D.CZK.EUR.SP00.A",
        frequency="daily",
        unit="currency",
        min_value=10.0,
        max_value=50.0,
        max_change_pct=15.0,
        max_gap_days=10,
        stale_after_days=7,
    )


def test_clean_monthly_series_passes_all_quality_checks():
    report = evaluate_observations(
        _monthly_spec(),
        [
            Observation("2026-04-01", 100.0),
            Observation("2026-05-01", 101.0),
            Observation("2026-06-01", 102.0),
        ],
        as_of="2026-07-19",
    )

    assert report.status == "pass"
    assert report.issues == ()


def test_quality_gate_detects_gap_range_jump_and_staleness():
    report = evaluate_observations(
        _monthly_spec(),
        [Observation("2025-01-01", 100.0), Observation("2025-03-01", 301.0)],
        as_of="2026-07-19",
    )

    assert {issue.code for issue in report.issues} == {
        "above_maximum",
        "missing_months",
        "implausible_change",
        "stale_series",
    }
    assert report.status == "error"
    assert "RESULT: FAIL" in format_quality_report([report], as_of="2026-07-19")


def test_semantic_quality_failure_rolls_back_and_is_logged():
    class OutOfRangeClient:
        mode = "live"

        def fetch(self, spec, params=None):
            del params
            reference = "2026-07-18" if spec.frequency == "daily" else "2026-06"
            return FetchResult(
                body=f"KEY,TIME_PERIOD,OBS_VALUE\n{spec.external_id},{reference},9999\n",
                source_url="https://example.test/sdmx",
                http_status=200,
                fetched_at="2026-07-19T10:00:00+00:00",
            )

    conn = connect(":memory:")
    with pytest.raises(DataQualityError, match="semantic quality gate failed"):
        collect(
            conn,
            load_config(),
            OutOfRangeClient(),
            collected_at="2026-07-19T10:00:00+00:00",
        )

    assert table_counts(conn) == {"metadata": 0, "time_series": 0, "logs": 1}
    log = conn.execute("SELECT status, log_text, traceback FROM logs").fetchone()
    assert log["status"] == "error"
    assert "quality=error" in log["log_text"]
    assert "above_maximum" in log["traceback"]


def test_flat_series_flags_a_stuck_or_cached_feed():
    # Real case this guards against: evidence/kinea.db's CZ_FX_EURCZK has a genuine 39-day run
    # of 27.021 (2017-02-03..2017-03-29, the CNB's EUR/CZK floor-defense regime), which every
    # other check treats as perfectly healthy -- in range, no gap, 0% change never trips
    # max_change_pct. This is that scenario in miniature: five identical daily fixings in a row.
    report = evaluate_observations(
        _daily_spec(),
        [Observation(f"2026-06-{day:02d}", 25.5) for day in range(1, 6)],
        as_of="2026-06-05",
    )

    assert {issue.code for issue in report.issues} == {"flat_series"}
    flat = next(issue for issue in report.issues if issue.code == "flat_series")
    assert flat.severity == "warning"
    assert "5 consecutive observations" in flat.message
    assert report.status == "warning"  # non-blocking by default, as the assignment requires


def test_flat_series_ignores_short_runs_below_the_frequency_threshold():
    # Daily threshold is 5; four identical days in a row is still ordinary noise-free data.
    report = evaluate_observations(
        _daily_spec(),
        [Observation(f"2026-06-{day:02d}", 25.5) for day in range(1, 5)],
        as_of="2026-06-04",
    )
    assert report.issues == ()


def test_flat_series_uses_a_tighter_threshold_for_monthly_series():
    report = evaluate_observations(
        _monthly_spec(),
        [Observation(m, 100.0) for m in ("2026-01-01", "2026-02-01", "2026-03-01")],
        as_of="2026-07-19",
    )
    assert {issue.code for issue in report.issues} == {"flat_series", "stale_series"}


def test_malformed_month_is_warned_and_does_not_abort_default_collection():
    class OneMalformedMonthClient:
        mode = "live"

        def fetch(self, spec, params=None):
            del params
            if spec.frequency == "daily":
                rows = [("2026-03-01", 24.0), ("bad-date", 24.1), ("2026-03-03", 24.2)]
            else:
                rows = [("2026-01", 100.0), ("bad-date", 101.0), ("2026-03", 102.0)]
            body = "KEY,TIME_PERIOD,OBS_VALUE\n" + "".join(
                f"{spec.external_id},{period},{value}\n" for period, value in rows
            )
            return FetchResult(body, "https://example.test", 200, "2026-03-31T10:00:00+00:00")

    conn = connect(":memory:")
    with pytest.warns(RuntimeWarning):
        report = collect(
            conn,
            load_config(),
            OneMalformedMonthClient(),
            collected_at="2026-03-31T10:00:00+00:00",
        )

    assert report.status == "success"
    assert any("missing_months" in warning for warning in report.warnings)
    assert table_counts(conn)["metadata"] == 5
