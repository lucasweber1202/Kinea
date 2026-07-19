from __future__ import annotations

import pytest

from kinea.config import load_config


def test_base_url_can_be_overridden_from_environment(monkeypatch):
    monkeypatch.setenv("KINEA_ECB_BASE_URL", "https://example.test/sdmx/")

    config = load_config()

    assert config.base_url == "https://example.test/sdmx/"


def test_base_url_override_must_be_absolute(monkeypatch):
    monkeypatch.setenv("KINEA_ECB_BASE_URL", "not-a-url")

    with pytest.raises(ValueError, match="absolute HTTP"):
        load_config()


def test_config_can_select_a_validated_series_subset():
    subset = load_config().select(["CZ_FX_EURCZK"])

    assert [item.series_id for item in subset.series] == ["CZ_FX_EURCZK"]
    with pytest.raises(ValueError, match="unknown series_id"):
        load_config().select(["CZ_UNKNOWN"])
