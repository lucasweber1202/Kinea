"""Tests for cross-series forecasting analytics (adds no table to the schema)."""

from __future__ import annotations

import pytest

from kinea.db import connect
from kinea.econometrics import base_effect_decomposition, diffusion_index, fx_passthrough
from kinea.models import Observation
from kinea.vintages import ingest_observations


def _seed(conn, series_id, frequency="monthly"):
    conn.execute(
        """
        INSERT INTO metadata (series_id, name, description, country, frequency, unit,
                              observation_count, source_url, collected_at)
        VALUES (?, 'n', 'd', 'CZ', ?, 'index', 0, 'https://x', ?)
        """,
        (series_id, frequency, "2026-07-01T10:00:00+00:00"),
    )
    return series_id


def _month(year: int, month_index_from_jan: int) -> str:
    """1-based month offset from January of ``year`` -> a first-of-month ISO date string."""
    absolute = year * 12 + (month_index_from_jan - 1)
    y, m = divmod(absolute, 12)
    return f"{y:04d}-{m + 1:02d}-01"


def _ingest_monthly(conn, series_id, values: dict[int, float]) -> None:
    """``values`` keyed by a 1-based running month index starting at January 2023."""
    observations = [
        Observation(_month(2023, index), value) for index, value in sorted(values.items())
    ]
    ingest_observations(
        conn,
        series_id,
        observations,
        vintage_date="2026-07-19",
        collected_at="2026-07-19T10:00:00+00:00",
    )


def test_fx_passthrough_recovers_a_known_lag_and_elasticity():
    conn = connect(":memory:")
    fx_sid, hicp_sid = "CZ_FX_EURCZK", "CZ_HICP_ENERGY_INDEX"
    _seed(conn, fx_sid, "daily")
    _seed(conn, hicp_sid, "monthly")

    # FX: month-over-month percent moves for months 2..8 (one observation per month stands in
    # for a daily series -- _monthly_aggregate_last just takes the single value as month-end).
    fx_mom_pct = {2: 1.0, 3: -1.0, 4: 2.0, 5: -2.0, 6: 0.5, 7: -0.5, 8: 1.5}
    fx_values = {1: 25.0}
    for month in range(2, 9):
        fx_values[month] = fx_values[month - 1] * (1 + fx_mom_pct[month] / 100.0)
    _ingest_monthly(conn, fx_sid, fx_values)

    # HICP energy: MoM(t) = elasticity * FX_MoM(t - lag), elasticity=2.0, lag=2 months.
    lag_months, elasticity = 2, 2.0
    hicp_values = {1: 100.0}
    for month in range(2, 9):
        driving_month = month - lag_months
        mom = elasticity * fx_mom_pct[driving_month] if driving_month in fx_mom_pct else 0.0
        hicp_values[month] = hicp_values[month - 1] * (1 + mom / 100.0)
    _ingest_monthly(conn, hicp_sid, hicp_values)

    result = fx_passthrough(conn, hicp_sid, fx_series_id=fx_sid, max_lag_months=4)

    assert result.hicp_series_id == hicp_sid
    assert result.best_lag_months == lag_months
    assert result.best_correlation == pytest.approx(1.0, abs=1e-6)
    assert result.elasticity == pytest.approx(elasticity, rel=1e-6)
    assert len(result.by_lag) == 5  # lags 0..4
    lag_2 = next(entry for entry in result.by_lag if entry.lag_months == 2)
    assert lag_2.n_pairs == 5  # months 4..8, the only ones with FX data 2 months back


def test_fx_passthrough_degrades_gracefully_with_no_data():
    conn = connect(":memory:")
    _seed(conn, "CZ_FX_EURCZK", "daily")
    _seed(conn, "CZ_HICP_ENERGY_INDEX", "monthly")

    result = fx_passthrough(conn, "CZ_HICP_ENERGY_INDEX", max_lag_months=3)

    assert result.best_correlation is None
    assert result.elasticity is None
    assert all(entry.n_pairs == 0 for entry in result.by_lag)


def test_diffusion_index_reports_breadth_of_acceleration():
    conn = connect(":memory:")
    a, b = "CZ_HICP_CORE_INDEX", "CZ_HICP_ENERGY_INDEX"
    _seed(conn, a)
    _seed(conn, b)

    # A accelerates into March (1% -> 2%) then decelerates into April (2% -> 1%).
    _ingest_monthly(conn, a, {1: 100.0, 2: 101.0, 3: 103.02, 4: 104.0502})
    # B decelerates into March (0.5% -> -0.4975%) then accelerates sharply into April.
    _ingest_monthly(conn, b, {1: 100.0, 2: 100.5, 3: 100.0009975, 4: 101.2010})

    readings = diffusion_index(conn, [a, b])
    by_month = {r.reference_date: r for r in readings}

    march = by_month[_month(2023, 3)]
    assert march.n_accelerating == 1  # A
    assert march.n_decelerating == 1  # B
    assert march.diffusion_index == pytest.approx(0.5)
    assert march.dominant_component == a  # |2.0%| > |~0.5%|

    april = by_month[_month(2023, 4)]
    assert april.n_accelerating == 1  # B
    assert april.n_decelerating == 1  # A
    assert april.dominant_component == b  # |1.2%| > |1.0%|

    february = by_month[_month(2023, 2)]
    assert february.n_components == 0  # no prior month's MoM exists yet to compare against
    assert february.diffusion_index is None


def test_diffusion_index_defaults_to_every_hicp_series_in_metadata():
    conn = connect(":memory:")
    _seed(conn, "CZ_HICP_CORE_INDEX")
    _seed(conn, "CZ_FX_EURCZK", "daily")  # not a HICP series -- must be excluded by default
    _ingest_monthly(conn, "CZ_HICP_CORE_INDEX", {1: 100.0, 2: 101.0, 3: 102.0})

    readings = diffusion_index(conn)

    assert all(r.n_components <= 1 for r in readings)  # only one HICP series was seeded


def test_base_effect_decomposition_on_steady_growth():
    # Constant 1%-per-month growth for 20 months: YoY is mechanically identical every eligible
    # month (12 months of the same 1% compounded), so yoy_change_pct is ~0 throughout, and the
    # base-effect/fresh-momentum split should cleanly cancel (1.0% momentum, -1.0% base effect).
    conn = connect(":memory:")
    sid = "CZ_HICP_CORE_INDEX"
    _seed(conn, sid)
    values = {1: 100.0}
    for month in range(2, 21):
        values[month] = values[month - 1] * 1.01
    _ingest_monthly(conn, sid, values)

    readings = base_effect_decomposition(conn, sid)

    # First eligible YoY row is month 13 (needs month 1 as its base); base_effect additionally
    # needs the base month's own MoM, which requires a 14th month of lookback -> None at month 13.
    assert readings[0].reference_date == _month(2023, 13)
    assert readings[0].base_effect_pct is None
    assert readings[0].yoy_change_pct is None  # nothing to compare the very first reading to
    assert readings[0].fresh_momentum_pct == pytest.approx(1.0)
    expected_yoy = (1.01**12 - 1.0) * 100.0
    assert readings[0].yoy_pct == pytest.approx(expected_yoy)

    for reading in readings[1:]:
        assert reading.yoy_pct == pytest.approx(expected_yoy)
        assert reading.fresh_momentum_pct == pytest.approx(1.0)
        assert reading.base_effect_pct == pytest.approx(-1.0)
        assert reading.yoy_change_pct == pytest.approx(0.0, abs=1e-9)
        # The decomposition should (approximately) reconstruct the actual swing.
        assert reading.fresh_momentum_pct + reading.base_effect_pct == pytest.approx(
            reading.yoy_change_pct, abs=1e-9
        )


def test_base_effect_decomposition_with_insufficient_history_is_empty():
    conn = connect(":memory:")
    sid = "CZ_HICP_CORE_INDEX"
    _seed(conn, sid)
    _ingest_monthly(conn, sid, {1: 100.0, 2: 101.0, 3: 102.0})

    assert base_effect_decomposition(conn, sid) == []
