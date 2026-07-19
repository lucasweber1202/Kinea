"""Robust SDMX-CSV parsing with record-level warnings."""

from __future__ import annotations

import csv
import io
import math
import re
import warnings
from dataclasses import dataclass
from datetime import date

from .models import Observation

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$")


@dataclass(frozen=True)
class ParseResult:
    observations: tuple[Observation, ...]
    warnings: tuple[str, ...]
    last_publish_date: str | None = None


def normalize_reference_date(raw: str) -> str:
    value = raw.strip()
    month = _MONTH.fullmatch(value)
    if month:
        return date(int(month.group(1)), int(month.group(2)), 1).isoformat()
    quarter = _QUARTER.fullmatch(value)
    if quarter:
        return date(int(quarter.group(1)), (int(quarter.group(2)) - 1) * 3 + 1, 1).isoformat()
    return date.fromisoformat(value[:10]).isoformat()


def parse_sdmx_csv(text: str, *, expected_external_id: str | None = None) -> ParseResult:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    required = {"TIME_PERIOD", "OBS_VALUE"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError(f"SDMX CSV missing required columns: {sorted(required)}")

    parsed: dict[str, Observation] = {}
    messages: list[str] = []
    publish_dates: list[str] = []

    for line_number, row in enumerate(reader, start=2):
        try:
            key = (row.get("KEY") or "").strip()
            if expected_external_id and key and key != expected_external_id:
                raise ValueError(f"unexpected KEY {key!r}")
            reference_date = normalize_reference_date(row.get("TIME_PERIOD") or "")
            value = float(row.get("OBS_VALUE") or "")
            if not math.isfinite(value):
                raise ValueError("non-finite OBS_VALUE")
            if reference_date in parsed:
                messages.append(
                    f"line {line_number}: duplicate period {reference_date}; last row wins"
                )
            parsed[reference_date] = Observation(reference_date, value)
            for column in ("LAST_UPDATE", "LAST_PUBLISHED", "PUBLICATION_DATE"):
                raw_date = (row.get(column) or "").strip()
                if raw_date:
                    publish_dates.append(date.fromisoformat(raw_date[:10]).isoformat())
                    break
        except (TypeError, ValueError) as exc:
            message = f"line {line_number}: skipped malformed record ({exc})"
            messages.append(message)
            warnings.warn(message, RuntimeWarning, stacklevel=2)

    observations = tuple(parsed[key] for key in sorted(parsed))
    return ParseResult(
        observations=observations,
        warnings=tuple(messages),
        last_publish_date=max(publish_dates) if publish_dates else None,
    )
