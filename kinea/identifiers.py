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
KNOWN_TOKENS = set(COUNTRY_NAMES) | set(TOKEN_LABELS)


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
    tokens = series_id.split("_")
    unknown = [token for token in tokens if token not in KNOWN_TOKENS]
    if unknown:
        raise ValueError(f"unknown series_id token(s): {', '.join(unknown)}")
    country, family, *qualifiers = tokens
    if family == "FX":
        valid_shape = qualifiers == ["EURCZK"]
    elif family == "HICP":
        valid_shape = (
            len(qualifiers) == 2
            and qualifiers[0] in {"CORE", "ENERGY", "FOOD", "SERVICES"}
            and qualifiers[1] == "INDEX"
        )
    else:
        valid_shape = False
    if not valid_shape:
        raise ValueError(f"invalid structured series_id grammar: {series_id}")
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
