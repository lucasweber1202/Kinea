"""Structured, human-readable identifiers for collected series."""

from __future__ import annotations

import re
from dataclasses import dataclass


_VALID_ID = re.compile(r"^[A-Z0-9]+(?:_[A-Z0-9]+)+$")

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
    """Validate and decode an upper-case, underscore-separated series identifier."""

    if not isinstance(series_id, str) or not _VALID_ID.fullmatch(series_id):
        raise ValueError(
            "series_id must be upper-case alphanumeric tokens separated by underscores"
        )
    country, family, *qualifiers = series_id.split("_")
    return SeriesIdParts(country=country, family=family, qualifiers=tuple(qualifiers))


def _label(token: str) -> str:
    return TOKEN_LABELS.get(token, token.title())


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
