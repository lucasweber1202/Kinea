"""Cross-series, forecasting-oriented analytics for the ECB Czech-CPI predictor set.

Beyond per-series transforms (kinea.transforms) and revision analytics (kinea.analytics), a
forecasting desk reasons across series: how much of a CZK move passes through into HICP
components, how broad-based this month's inflation momentum is, and how much of a component's
year-over-year print is a mechanical base effect versus fresh in-year momentum. All of it is
computed on demand from the current view of ``time_series`` -- no table is added, and this
module (like the rest of ``kinea``) has no third-party runtime dependency.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date


def _months_before(reference: str, months: int) -> str:
    """The first-of-month ISO date ``months`` calendar months before ``reference``."""
    current = date.fromisoformat(reference)
    absolute_month = current.year * 12 + current.month - 1 - months
    year, zero_based_month = divmod(absolute_month, 12)
    return date(year, zero_based_month + 1, 1).isoformat()


def _current_series(conn: sqlite3.Connection, series_id: str) -> list[tuple[str, float]]:
    """The latest known vintage of every observation for one series, oldest first."""
    rows = conn.execute(
        """
        SELECT reference_date, value
        FROM (
            SELECT t.*, ROW_NUMBER() OVER (
                PARTITION BY reference_date ORDER BY vintage_date DESC, collected_at DESC
            ) AS rn
            FROM time_series t
            WHERE series_id = ?
        ) ranked
        WHERE rn = 1
        ORDER BY reference_date
        """,
        (series_id,),
    ).fetchall()
    return [(row["reference_date"], float(row["value"])) for row in rows]


def _monthly_aggregate_last(rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Reduce a (typically daily) series to one value per calendar month: that month's last
    observation (a month-end fixing), which is how FX pass-through is conventionally read."""
    by_month: dict[str, float] = {}
    for reference, value in rows:  # rows are ascending, so the last write per month wins
        by_month[f"{reference[:7]}-01"] = value
    return sorted(by_month.items())


def _monthly_mom(rows: list[tuple[str, float]]) -> dict[str, float]:
    """Percent change versus the calendar-adjacent prior month, keyed by that month's date.

    Only emits an entry when the immediately preceding calendar month is actually present in
    ``rows`` -- a gap silently produces no entry rather than a mislabeled multi-month change.
    """
    by_date = dict(rows)
    result: dict[str, float] = {}
    for reference, value in rows:
        previous = by_date.get(_months_before(reference, 1))
        if previous:
            result[reference] = (value / previous - 1.0) * 100.0
    return result


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def _ols_slope(xs: list[float], ys: list[float]) -> float | None:
    """Closed-form single-variable OLS slope (beta of y on x), no external dependency."""
    if len(xs) < 3:
        return None
    try:
        variance = statistics.variance(xs)
        if not variance:
            return None
        return statistics.covariance(xs, ys) / variance
    except statistics.StatisticsError:
        return None


@dataclass(frozen=True)
class PassThroughLag:
    lag_months: int
    correlation: float | None
    n_pairs: int


@dataclass(frozen=True)
class FxPassThrough:
    """How much of a EUR/CZK move shows up in one HICP component, and after how long.

    ``elasticity`` is the OLS slope of the component's month-over-month percent change on
    EUR/CZK's month-over-month percent change at ``best_lag_months`` -- roughly, "a 1% CZK
    depreciation is followed by an ``elasticity``% move in this component ``best_lag_months``
    months later." ``by_lag`` carries every tested lag so the caller can judge how sharp (or
    flat and inconclusive) the peak actually is, not just trust the single best number.
    """

    hicp_series_id: str
    fx_series_id: str
    best_lag_months: int
    best_correlation: float | None
    elasticity: float | None
    by_lag: tuple[PassThroughLag, ...]


def fx_passthrough(
    conn: sqlite3.Connection,
    hicp_series_id: str,
    *,
    fx_series_id: str = "CZ_FX_EURCZK",
    max_lag_months: int = 6,
) -> FxPassThrough:
    """Cross-correlate a HICP component's MoM% against EUR/CZK's MoM% at lags 0..max_lag_months.

    EUR/CZK is aggregated from daily to a month-end monthly series first, so both sides compare
    on the same calendar-month cadence. The lag with the largest absolute correlation is
    reported as ``best_lag_months``; ties prefer the shorter (more immediately actionable) lag.
    """
    if max_lag_months < 0:
        raise ValueError("max_lag_months must not be negative")
    fx_mom = _monthly_mom(_monthly_aggregate_last(_current_series(conn, fx_series_id)))
    hicp_mom = _monthly_mom(_current_series(conn, hicp_series_id))
    hicp_months = sorted(hicp_mom)

    def _paired(lag: int) -> tuple[list[float], list[float]]:
        xs: list[float] = []
        ys: list[float] = []
        for month in hicp_months:
            fx_value = fx_mom.get(_months_before(month, lag))
            if fx_value is not None:
                xs.append(fx_value)
                ys.append(hicp_mom[month])
        return xs, ys

    by_lag = []
    for lag in range(max_lag_months + 1):
        xs, ys = _paired(lag)
        by_lag.append(
            PassThroughLag(lag_months=lag, correlation=_correlation(xs, ys), n_pairs=len(xs))
        )

    scored = [(entry.correlation, entry) for entry in by_lag if entry.correlation is not None]
    best_pair = max(scored, key=lambda item: (abs(item[0]), -item[1].lag_months), default=None)
    best = best_pair[1] if best_pair is not None else None
    best_lag = best.lag_months if best is not None else 0
    best_xs, best_ys = _paired(best_lag)

    return FxPassThrough(
        hicp_series_id=hicp_series_id,
        fx_series_id=fx_series_id,
        best_lag_months=best_lag,
        best_correlation=best.correlation if best is not None else None,
        elasticity=_ols_slope(best_xs, best_ys),
        by_lag=tuple(by_lag),
    )


@dataclass(frozen=True)
class DiffusionReading:
    """Breadth of inflation momentum across the tracked HICP components for one month.

    ``diffusion_index`` is the fraction of components whose month-over-month move ACCELERATED
    versus the prior month (not merely positive) -- broad-based acceleration across components
    is a materially different signal for a forecaster than one component (typically energy)
    swinging while the others hold steady. ``dominant_component`` is whichever component moved
    most in absolute MoM terms that month; it is an equal-weighted magnitude proxy, NOT the
    official HICP component weighting, which this source does not publish.
    """

    reference_date: str
    n_accelerating: int
    n_decelerating: int
    n_flat: int
    n_components: int
    diffusion_index: float | None
    dominant_component: str | None
    dominant_component_mom_pct: float | None


def diffusion_index(
    conn: sqlite3.Connection, hicp_series_ids: list[str] | tuple[str, ...] | None = None
) -> list[DiffusionReading]:
    """Month-by-month breadth of MoM acceleration across the given (default: all HICP_) series."""
    if hicp_series_ids is None:
        rows = conn.execute(
            "SELECT DISTINCT series_id FROM metadata WHERE series_id LIKE '%\\_HICP\\_%' ESCAPE '\\'"
        ).fetchall()
        hicp_series_ids = sorted(row["series_id"] for row in rows)

    mom_by_series = {sid: _monthly_mom(_current_series(conn, sid)) for sid in hicp_series_ids}
    all_months = sorted(set().union(*mom_by_series.values())) if mom_by_series else []

    readings = []
    for month in all_months:
        prev_month = _months_before(month, 1)
        accelerating = decelerating = flat = 0
        dominant_sid: str | None = None
        dominant_abs = -1.0
        for sid in hicp_series_ids:
            series_mom = mom_by_series[sid]
            mom_now = series_mom.get(month)
            if mom_now is None:
                continue
            if abs(mom_now) > dominant_abs:
                dominant_abs = abs(mom_now)
                dominant_sid = sid
            mom_prev = series_mom.get(prev_month)
            if mom_prev is None:
                continue
            if mom_now > mom_prev:
                accelerating += 1
            elif mom_now < mom_prev:
                decelerating += 1
            else:
                flat += 1
        n_components = accelerating + decelerating + flat
        readings.append(
            DiffusionReading(
                reference_date=month,
                n_accelerating=accelerating,
                n_decelerating=decelerating,
                n_flat=flat,
                n_components=n_components,
                diffusion_index=(accelerating / n_components) if n_components else None,
                dominant_component=dominant_sid,
                dominant_component_mom_pct=dominant_abs if dominant_sid is not None else None,
            )
        )
    return readings


@dataclass(frozen=True)
class BaseEffectReading:
    """Decomposes the month-to-month SWING in a series' own year-over-year print.

    A classic source of forecaster confusion: YoY inflation can jump or drop mechanically
    because of what happened 12 months ago (the "base"), independent of anything new. This
    reports, for each month with a computable YoY-versus-last-month comparison:
    ``fresh_momentum_pct`` (this month's own month-over-month move -- genuinely new information)
    and ``base_effect_pct`` (the negative of the month-over-month move from 12 months ago, which
    is rolling out of the 12-month comparison window this month -- mechanical, not new). Their
    sum approximates ``yoy_change_pct`` (exact for log returns; a close approximation here since
    these HICP components typically move a few percent a month at most).
    """

    series_id: str
    reference_date: str
    yoy_pct: float
    yoy_change_pct: float | None
    fresh_momentum_pct: float
    base_effect_pct: float | None


def base_effect_decomposition(conn: sqlite3.Connection, series_id: str) -> list[BaseEffectReading]:
    """Base-effect/fresh-momentum decomposition for every month with a computable YoY figure."""
    rows = _current_series(conn, series_id)
    by_date = dict(rows)
    mom = _monthly_mom(rows)

    readings: list[BaseEffectReading] = []
    previous_yoy: float | None = None
    for reference, value in rows:
        base = by_date.get(_months_before(reference, 12))
        if not base:
            previous_yoy = None
            continue
        yoy = (value / base - 1.0) * 100.0
        fresh_momentum = mom.get(reference, 0.0)
        base_effect_mom = mom.get(_months_before(reference, 12))
        base_effect = -base_effect_mom if base_effect_mom is not None else None
        yoy_change = None if previous_yoy is None else yoy - previous_yoy
        readings.append(
            BaseEffectReading(
                series_id=series_id,
                reference_date=reference,
                yoy_pct=yoy,
                yoy_change_pct=yoy_change,
                fresh_momentum_pct=fresh_momentum,
                base_effect_pct=base_effect,
            )
        )
        previous_yoy = yoy
    return readings
