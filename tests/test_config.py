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
