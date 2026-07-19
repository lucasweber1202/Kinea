"""Derived series transformations for analysis and presentation.

These are the transformations the store deliberately does NOT persist (the database keeps
only raw published levels, per the assignment). They live here as small, tested functions
so the dashboard, CLI, and any downstream analysis derive year-over-year, month-over-month,
annualized, and rebased views from one canonical implementation instead of ad-hoc snippets.

They operate on a ``pandas.Series`` indexed in reference-date order and return a Series of
the same shape (with NaN where a value cannot be computed), so callers can align results
back to their frames.
"""

from __future__ import annotations

import pandas as pd


def year_over_year(series: pd.Series, periods: int = 12) -> pd.Series:
    """Percentage change versus ``periods`` observations earlier (12 = annual for monthly)."""
    if periods < 1:
        raise ValueError("periods must be positive")
    return series.pct_change(periods=periods, fill_method=None) * 100.0


def month_over_month(series: pd.Series) -> pd.Series:
    """Percentage change versus the previous observation."""
    return year_over_year(series, periods=1)


def annualized(series: pd.Series, window: int = 3, periods_per_year: int = 12) -> pd.Series:
    """Annualized percentage change over a rolling ``window`` (e.g. 3-month annualized).

    Compounds the window change to a yearly rate: ``(value_t / value_{t-window}) ** (12/window) - 1``.
    Non-positive ratios (which cannot be raised to a fractional power meaningfully) become NaN.
    """
    if window < 1 or periods_per_year < 1:
        raise ValueError("window and periods_per_year must be positive")
    ratio = series / series.shift(window)
    ratio = ratio.where(ratio > 0)
    return (ratio ** (periods_per_year / window) - 1.0) * 100.0


def rebase(series: pd.Series, base: float = 100.0) -> pd.Series:
    """Rescale so the first observation equals ``base`` (index comparison from a common start)."""
    if series.empty:
        return series
    anchor = series.iloc[0]
    if pd.isna(anchor) or anchor == 0:
        return pd.Series([float("nan")] * len(series), index=series.index)
    return series / anchor * base
