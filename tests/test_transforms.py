"""Tests for the derived-transformation helpers (never persisted, only computed)."""

from __future__ import annotations

import math

import pandas as pd
import pytest
from pytest import approx

from kinea.transforms import annualized, month_over_month, rebase, year_over_year


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype="float64")


def test_year_over_year_percentage():
    s = _series([100.0] * 12 + [110.0])
    yoy = year_over_year(s, periods=12)
    assert math.isnan(yoy.iloc[0])
    assert yoy.iloc[12] == approx(10.0)  # 110 vs 100 a year earlier


def test_month_over_month_is_single_period():
    s = _series([100.0, 101.0, 101.0])
    mom = month_over_month(s)
    assert math.isnan(mom.iloc[0])
    assert mom.iloc[1] == approx(1.0)
    assert mom.iloc[2] == approx(0.0, abs=1e-9)


def test_annualized_compounds_window_change():
    # +2% over 3 months annualizes to (1.02**4 - 1) * 100
    s = _series([100.0, 100.0, 100.0, 102.0])
    ann = annualized(s, window=3, periods_per_year=12)
    assert math.isnan(ann.iloc[2])
    assert ann.iloc[3] == (1.02**4 - 1.0) * 100.0


def test_annualized_ignores_non_positive_ratio():
    s = _series([100.0, 0.0, 0.0, -5.0])
    ann = annualized(s, window=3)
    assert math.isnan(ann.iloc[3])  # ratio <= 0 -> NaN, never a complex/garbage number


def test_rebase_starts_at_base():
    s = _series([50.0, 55.0, 60.0])
    rebased = rebase(s, base=100.0)
    assert rebased.iloc[0] == 100.0
    assert rebased.iloc[2] == 120.0


def test_rebase_handles_zero_anchor():
    s = _series([0.0, 5.0])
    rebased = rebase(s)
    assert rebased.isna().all()


def test_year_over_year_without_reference_dates_trusts_array_position():
    # Historical behavior, preserved for backward compatibility: with no reference_dates given,
    # position is all that counts, whether or not it corresponds to a real 12-month calendar span.
    s = _series([100.0] * 12 + [110.0])
    yoy = year_over_year(s, periods=12)
    assert math.isnan(yoy.iloc[0])
    assert yoy.iloc[12] == approx(10.0)


def test_year_over_year_with_reference_dates_catches_missing_month():
    # Same values as above, but now the underlying series actually skipped 2026-04-01 -- the
    # calendar-aware guard must NaN out the mislabeled 11-months-apart "annual" comparison.
    dates = pd.to_datetime(
        [
            "2025-05-01",
            "2025-06-01",
            "2025-07-01",
            "2025-08-01",
            "2025-09-01",
            "2025-10-01",
            "2025-11-01",
            "2025-12-01",
            "2026-01-01",
            "2026-02-01",
            "2026-03-01",
            "2026-05-01",  # 2026-04-01 is missing
        ]
    )
    s = _series([100.0] * 11 + [110.0])
    yoy = year_over_year(s, periods=12, reference_dates=dates)
    assert yoy.isna().all()  # every comparison needs 12 full calendar months; none exist yet


def test_year_over_year_with_reference_dates_accepts_a_genuine_annual_span():
    dates = pd.to_datetime([f"2025-{m:02d}-01" for m in range(1, 13)] + ["2026-01-01"])
    s = _series([100.0] * 12 + [110.0])
    yoy = year_over_year(s, periods=12, reference_dates=dates)
    assert yoy.iloc[12] == approx(10.0)  # 2026-01-01 vs 2025-01-01: a real 12-month span


def test_annualized_with_reference_dates_catches_missing_month():
    dates = pd.to_datetime(["2026-01-01", "2026-02-01", "2026-04-01", "2026-05-01"])  # skips -03
    s = _series([100.0, 100.0, 100.0, 102.0])
    ann = annualized(s, window=3, reference_dates=dates)
    assert math.isnan(ann.iloc[3])  # would otherwise report a spurious annualized rate


def test_change_windows_must_be_positive():
    series = _series([100.0, 101.0])

    with pytest.raises(ValueError, match="periods must be positive"):
        year_over_year(series, periods=0)
    with pytest.raises(ValueError, match="window and periods_per_year must be positive"):
        annualized(series, window=0)
    with pytest.raises(ValueError, match="window and periods_per_year must be positive"):
        annualized(series, periods_per_year=0)
