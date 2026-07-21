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

import calendar
from datetime import date

import pandas as pd


def _shift_months(reference: str, months: int) -> str:
    """The same calendar-month arithmetic kinea.features uses for vintage-safe lookbacks."""
    current = date.fromisoformat(reference)
    absolute_month = current.year * 12 + current.month - 1 + months
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def _calendar_aligned_mask(reference_dates: pd.Series, periods: int) -> pd.Series:
    """True where the observation ``periods`` positions back is exactly ``periods`` calendar
    months earlier, so a gap in the underlying series cannot silently mislabel an 11- or
    13-month span as an annual comparison."""
    as_iso = pd.Series(pd.to_datetime(reference_dates)).dt.strftime("%Y-%m-%d")
    expected = as_iso.apply(lambda ref: _shift_months(ref, -periods))
    return expected.reset_index(drop=True) == as_iso.shift(periods).reset_index(drop=True)


def year_over_year(
    series: pd.Series, periods: int = 12, *, reference_dates: pd.Series | None = None
) -> pd.Series:
    """Percentage change versus ``periods`` observations earlier (12 = annual for monthly).

    ``reference_dates``, when given, must be calendar dates aligned one-to-one with ``series``
    (same order). Any comparison whose two endpoints are not exactly ``periods`` calendar months
    apart — because a month is missing from the underlying series — becomes NaN instead of a
    silently mislabeled rate computed from whatever happens to sit ``periods`` rows back.
    """
    if periods < 1:
        raise ValueError("periods must be positive")
    result = series.pct_change(periods=periods, fill_method=None) * 100.0
    if reference_dates is not None:
        mask = _calendar_aligned_mask(reference_dates, periods)
        result = result.where(mask.set_axis(result.index))
    return result


def month_over_month(series: pd.Series, *, reference_dates: pd.Series | None = None) -> pd.Series:
    """Percentage change versus the previous observation."""
    return year_over_year(series, periods=1, reference_dates=reference_dates)


def annualized(
    series: pd.Series,
    window: int = 3,
    periods_per_year: int = 12,
    *,
    reference_dates: pd.Series | None = None,
) -> pd.Series:
    """Annualized percentage change over a rolling ``window`` (e.g. 3-month annualized).

    Compounds the window change to a yearly rate: ``(value_t / value_{t-window}) ** (12/window) - 1``.
    Non-positive ratios (which cannot be raised to a fractional power meaningfully) become NaN.
    ``reference_dates`` applies the same calendar-gap guard as ``year_over_year``.
    """
    if window < 1 or periods_per_year < 1:
        raise ValueError("window and periods_per_year must be positive")
    ratio = series / series.shift(window)
    ratio = ratio.where(ratio > 0)
    if reference_dates is not None:
        mask = _calendar_aligned_mask(reference_dates, window)
        ratio = ratio.where(mask.set_axis(ratio.index))
    return (ratio ** (periods_per_year / window) - 1.0) * 100.0


def rebase(series: pd.Series, base: float = 100.0) -> pd.Series:
    """Rescale so the first observation equals ``base`` (index comparison from a common start)."""
    if series.empty:
        return series
    anchor = series.iloc[0]
    if pd.isna(anchor) or anchor == 0:
        return pd.Series([float("nan")] * len(series), index=series.index)
    return series / anchor * base
