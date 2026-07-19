from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

streamlit = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from dashboard.app import add_freshness  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_dashboard_contract_and_default_revision_story():
    app = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=30).run()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == [
        "Overview",
        "HICP components",
        "EUR/CZK",
        "Vintages",
        "As-of",
        "Audit",
    ]
    assert len(app.get("download_button")) == 7
    assert any("1 observation(s) changed since 2026-07-01" in item.value for item in app.markdown)


def test_all_derived_hicp_views_render_without_exceptions():
    app = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=30).run()

    for view in (
        "Year-over-year %",
        "Month-over-month %",
        "3m annualized %",
        "Rebased to 100",
    ):
        app.selectbox[0].set_value(view)
        app.run()
        assert not app.exception
        assert len(app.get("vega_lite_chart")) >= 6


def test_dashboard_uses_current_streamlit_width_api():
    source = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")

    assert "use_container_width" not in source
    assert "first published" not in source
    assert "_csv_bytes(shown)" in source
    assert 'scheme="yelloworangered"' in source
    assert "not seasonally adjusted" in source
    assert "hero_yoy = add_yoy(hero_full, 12)" in source
    assert 'latest = shown.sort_values("reference_date")' in source
    assert "Source publish date" in source


def test_freshness_thresholds_respect_native_frequency():
    metadata = pd.DataFrame(
        {
            "frequency": ["daily", "monthly"],
            "last_observation": ["2026-07-10", "2026-05-01"],
        }
    )

    result = add_freshness(metadata, today=pd.Timestamp("2026-07-19").date())

    assert result["freshness"].tolist() == ["Review", "Review"]
    assert result["lag_days"].tolist() == [9, 79]
