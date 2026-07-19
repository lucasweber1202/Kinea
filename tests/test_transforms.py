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


def test_change_windows_must_be_positive():
    series = _series([100.0, 101.0])

    with pytest.raises(ValueError, match="periods must be positive"):
        year_over_year(series, periods=0)
    with pytest.raises(ValueError, match="window and periods_per_year must be positive"):
        annualized(series, window=0)
    with pytest.raises(ValueError, match="window and periods_per_year must be positive"):
        annualized(series, periods_per_year=0)
