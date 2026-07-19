import pytest

from kinea.identifiers import derive_description, derive_name, parse_series_id


def test_parse_series_id_components():
    parts = parse_series_id("CZ_HICP_CORE_INDEX")
    assert parts.country == "CZ"
    assert parts.family == "HICP"
    assert parts.qualifiers == ("CORE", "INDEX")


def test_rejects_lowercase_identifier():
    with pytest.raises(ValueError):
        parse_series_id("CZ_HICP_core_INDEX")


def test_rejects_empty_identifier():
    with pytest.raises(ValueError):
        parse_series_id("")


def test_rejects_unknown_token():
    with pytest.raises(ValueError, match="unknown"):
        parse_series_id("CZ_HICP_UNKNOWN_INDEX")


@pytest.mark.parametrize(
    "series_id",
    ["CZ_HICP_CORE", "CZ_HICP_CORE_FOOD", "CZ_FX_CORE", "CZ_FX_EURCZK_INDEX"],
)
def test_rejects_known_tokens_in_invalid_grammar(series_id):
    with pytest.raises(ValueError, match="invalid structured series_id grammar"):
        parse_series_id(series_id)


def test_name_is_derived_from_tokens():
    assert derive_name("CZ_FX_EURCZK") == "Czechia - FX EUR/CZK"


def test_description_preserves_identifier_tokens():
    description = derive_description("CZ_HICP_SERVICES_INDEX")
    assert "HICP / Services / Index" in description
    assert "CZ_HICP_SERVICES_INDEX" in description


def test_eur_czk_acronym_is_preserved():
    assert "EUR/CZK" in derive_name("CZ_FX_EURCZK")
    assert "EUR/CZK" in derive_description("CZ_FX_EURCZK")
