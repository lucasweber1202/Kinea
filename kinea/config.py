"""Validated series configuration loader."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .identifiers import parse_series_id

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "series.json"


@dataclass(frozen=True)
class SeriesSpec:
    series_id: str
    external_id: str
    dataflow: str
    sdmx_key: str
    frequency: str
    unit: str
    min_value: float | None = None
    max_value: float | None = None
    max_change_pct: float | None = None
    max_gap_days: int | None = None
    stale_after_days: int | None = None

    def request_url(self, base_url: str, params: dict[str, str] | None = None) -> str:
        from urllib.parse import urlencode

        query = {"format": "csvdata", "detail": "dataonly"}
        if params:
            query.update(params)
        return f"{base_url.rstrip('/')}/{self.dataflow}/{self.sdmx_key}?{urlencode(query)}"


@dataclass(frozen=True)
class Config:
    source: str
    base_url: str
    series: tuple[SeriesSpec, ...]

    def by_id(self, series_id: str) -> SeriesSpec:
        return next(item for item in self.series if item.series_id == series_id)

    def select(self, series_ids: list[str] | tuple[str, ...] | None) -> Config:
        """Return a validated subset while preserving catalogue order."""
        if not series_ids:
            return self
        requested = set(series_ids)
        known = {item.series_id for item in self.series}
        unknown = requested - known
        if unknown:
            raise ValueError(f"unknown series_id(s): {', '.join(sorted(unknown))}")
        return Config(
            source=self.source,
            base_url=self.base_url,
            series=tuple(item for item in self.series if item.series_id in requested),
        )


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    series = tuple(SeriesSpec(**item) for item in raw["series"])
    ids = [item.series_id for item in series]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate structured series_id in configuration")
    for item in series:
        parse_series_id(item.series_id)
        if not item.external_id.startswith(f"{item.dataflow}."):
            raise ValueError(f"external_id/dataflow mismatch for {item.series_id}")
        if item.frequency not in {"daily", "weekly", "monthly", "quarterly"}:
            raise ValueError(f"unsupported frequency for {item.series_id}: {item.frequency}")
        if item.min_value is not None and item.max_value is not None:
            if item.min_value >= item.max_value:
                raise ValueError(f"invalid value range for {item.series_id}")
        for field_name in ("max_change_pct", "max_gap_days", "stale_after_days"):
            value = getattr(item, field_name)
            if value is not None and value <= 0:
                raise ValueError(f"{field_name} must be positive for {item.series_id}")
    base_url = os.getenv("KINEA_ECB_BASE_URL", raw["base_url"]).strip()
    if not base_url.startswith(("https://", "http://")):
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    return Config(source=raw["source"], base_url=base_url, series=series)
