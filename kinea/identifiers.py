"""Structured, human-readable identifiers for collected series."""

from __future__ import annotations

import re
from dataclasses import dataclass

_VALID_ID = re.compile(r"^[A-Z0-9]+(?:_[A-Z0-9]+)+$")

# Assignment scope is Czechia only (section 3: "Escopo nacional"), so the country token is a
# closed set on purpose. Family/qualifier tokens are intentionally NOT a closed set: earlier this
# module hard-coded the exact shape of every family (e.g. HICP requires exactly two qualifiers
# ending in INDEX), which meant adding a series to config/series.json without also editing this
# file made kinea.config.load_config() raise at import time. Structure (upper-case tokens,
# underscore-separated, country first) is still enforced; the vocabulary is open, and unrecognised
# tokens degrade gracefully to a title-cased label instead of failing.
COUNTRY_NAMES = {"CZ": "Czechia"}
TOKEN_LABELS = {
    "FX": "FX",
    "HICP": "HICP",
    "CORE": "Core",
    "ENERGY": "Energy",
    "FOOD": "Food",
    "SERVICES": "Services",
    "INDEX": "Index",
    "EURCZK": "EUR/CZK",
}


@dataclass(frozen=True)
class SeriesIdParts:
    """Decoded components of a structured ``series_id``."""

    country: str
    family: str
    qualifiers: tuple[str, ...]

    @property
    def tokens(self) -> tuple[str, ...]:
        return (self.country, self.family, *self.qualifiers)


def parse_series_id(series_id: str) -> SeriesIdParts:
    """Validate and decode an upper-case, underscore-separated series identifier.

    Only the structure is enforced here (country, family, and zero or more qualifiers, all
    upper-case alphanumeric tokens) — not a fixed enum of family/qualifier values. The country
    is validated against the assignment's fixed national scope; family and qualifier vocabulary
    are free so the catalogue in ``config/series.json`` stays the single source of truth for
    which series exist. A two-token id (country + family, e.g. the assignment's own ``CZ_M2``
    example) is valid: qualifiers are optional, not required.
    """

    if not isinstance(series_id, str) or not _VALID_ID.fullmatch(series_id):
        raise ValueError(
            "series_id must be upper-case alphanumeric tokens separated by underscores"
        )
    country, family, *qualifiers = series_id.split("_")
    if country not in COUNTRY_NAMES:
        raise ValueError(f"series_id country must be one of {sorted(COUNTRY_NAMES)}: {series_id}")
    return SeriesIdParts(country=country, family=family, qualifiers=tuple(qualifiers))


def _label(token: str) -> str:
    # series_id tokens are already validated as a single all-upper-case alphanumeric run (no
    # internal spaces), so .title() has nothing useful to split on -- it only ever lower-cases
    # everything after the first letter, which corrupts acronym-style tokens that are the norm
    # in this domain (GDP -> "Gdp", CPI -> "Cpi", PPI -> "Ppi"). Fall back to the token verbatim
    # (already upper-case) instead, so a future acronym-heavy series renders correctly by
    # default rather than only the ones someone remembered to add to TOKEN_LABELS.
    return TOKEN_LABELS.get(token, token)


def derive_name(series_id: str) -> str:
    """Build a display name exclusively from the identifier's decoded tokens."""

    parts = parse_series_id(series_id)
    country = COUNTRY_NAMES.get(parts.country, parts.country)
    subject = " ".join(_label(token) for token in (parts.family, *parts.qualifiers))
    return f"{country} - {subject}"


def derive_description(series_id: str) -> str:
    """Build an auditable description exclusively from structured id components."""

    parts = parse_series_id(series_id)
    country = COUNTRY_NAMES.get(parts.country, parts.country)
    details = " / ".join(_label(token) for token in (parts.family, *parts.qualifiers))
    return f"{details} for {country}; metadata derived from structured id {series_id}."
