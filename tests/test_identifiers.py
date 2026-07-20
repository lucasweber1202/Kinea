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


def test_rejects_unknown_country():
    """Scope is Czechia only (assignment section 3); the country token stays a closed set."""
    with pytest.raises(ValueError, match="country"):
        parse_series_id("US_HICP_CORE_INDEX")


@pytest.mark.parametrize(
    "series_id",
    ["CZ", "CZ_", "_CZ_HICP", "CZ__HICP", "CZ-HICP-CORE"],
)
def test_rejects_malformed_structure(series_id):
    with pytest.raises(ValueError):
        parse_series_id(series_id)


def test_two_token_id_without_qualifier_is_valid():
    """The assignment's own section-5.5 example (``CZ_M2``) has no qualifier at all."""
    parts = parse_series_id("CZ_M2")
    assert parts.family == "M2"
    assert parts.qualifiers == ()


def test_unrecognized_family_and_qualifier_still_parse():
    """New series in config/series.json should not require editing this module too.

    Family/qualifier vocabulary is intentionally open (see module docstring): a well-formed but
    previously unseen id — like the assignment's own ``CZ_PPI_INDEX`` example — parses instead of
    raising, and gets a graceful title-cased label rather than a hand-written name.
    """
    parts = parse_series_id("CZ_PPI_INDEX")
    assert parts.family == "PPI"
    assert parts.qualifiers == ("INDEX",)
    assert derive_name("CZ_PPI_INDEX") == "Czechia - Ppi Index"
    assert "CZ_PPI_INDEX" in derive_description("CZ_PPI_INDEX")


def test_name_is_derived_from_tokens():
    assert derive_name("CZ_FX_EURCZK") == "Czechia - FX EUR/CZK"


def test_description_preserves_identifier_tokens():
    description = derive_description("CZ_HICP_SERVICES_INDEX")
    assert "HICP / Services / Index" in description
    assert "CZ_HICP_SERVICES_INDEX" in description


def test_eur_czk_acronym_is_preserved():
    assert "EUR/CZK" in derive_name("CZ_FX_EURCZK")
    assert "EUR/CZK" in derive_description("CZ_FX_EURCZK")
