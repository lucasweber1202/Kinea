"""Vintage-safe mixed-frequency feature matrices for forecasting workflows."""

from __future__ import annotations

import calendar
import csv
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from statistics import fmean
from typing import Iterable, Sequence

from .config import Config
from .panels import PanelRow, as_of_panel


@dataclass(frozen=True)
class FeatureRecipe:
    name: str
    series_id: str
    transform: str = "level"
    periods: int = 1
    aggregation: str = "latest"
    period_unit: str = "observations"

    def __post_init__(self) -> None:
        if self.transform not in {"level", "pct_change", "difference"}:
            raise ValueError("feature transform must be level, pct_change, or difference")
        if self.aggregation not in {"latest", "mean_month"}:
            raise ValueError("feature aggregation must be latest or mean_month")
        if self.period_unit not in {"observations", "calendar_months"}:
            raise ValueError("feature period unit must be observations or calendar_months")
        if self.periods < 1:
            raise ValueError("feature periods must be positive")


@dataclass(frozen=True)
class FeatureValue:
    knowledge_date: str
    feature: str
    value: float
    source_reference_date: str
    source_vintage_date: str


FEATURE_COLUMNS = tuple(FeatureValue.__dataclass_fields__)


def default_feature_recipes(config: Config) -> tuple[FeatureRecipe, ...]:
    """Forecasting defaults: calendar-aligned HICP YoY and EUR/CZK month-to-date mean."""
    recipes = []
    for spec in config.series:
        if spec.frequency == "monthly":
            recipes.append(
                FeatureRecipe(
                    name=f"{spec.series_id}_YOY",
                    series_id=spec.series_id,
                    transform="pct_change",
                    periods=12,
                    period_unit="calendar_months",
                )
            )
        else:
            recipes.append(
                FeatureRecipe(
                    name=f"{spec.series_id}_MONTH_TO_DATE_MEAN",
                    series_id=spec.series_id,
                    aggregation="mean_month",
                )
            )
    return tuple(recipes)


def _aggregate(rows: Sequence[PanelRow], method: str) -> list[tuple[str, float, str]]:
    ordered = sorted(rows, key=lambda item: item.reference_date)
    if method == "latest":
        return [(row.reference_date, row.value, row.vintage_date) for row in ordered]
    months: dict[str, list[PanelRow]] = {}
    for row in ordered:
        months.setdefault(row.reference_date[:7], []).append(row)
    return [
        (
            max(item.reference_date for item in month_rows),
            fmean(item.value for item in month_rows),
            max(item.vintage_date for item in month_rows),
        )
        for _, month_rows in sorted(months.items())
    ]


def _shift_months(reference: str, months: int) -> str:
    current = date.fromisoformat(reference)
    absolute_month = current.year * 12 + current.month - 1 + months
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def _comparison_point(
    points: list[tuple[str, float, str]], recipe: FeatureRecipe
) -> tuple[str, float, str] | None:
    if recipe.period_unit == "observations":
        if len(points) <= recipe.periods:
            return None
        return points[-1 - recipe.periods]
    target_reference = _shift_months(points[-1][0], -recipe.periods)
    return next((point for point in reversed(points) if point[0] == target_reference), None)


def _calculate(points: list[tuple[str, float, str]], recipe: FeatureRecipe) -> FeatureValue | None:
    if not points:
        return None
    reference, latest, vintage = points[-1]
    if recipe.transform == "level":
        value = latest
    else:
        previous_point = _comparison_point(points, recipe)
        if previous_point is None:
            return None
        previous = previous_point[1]
        if recipe.transform == "pct_change":
            if previous == 0:
                return None
            value = (latest / previous - 1.0) * 100.0
        else:
            value = latest - previous
    return FeatureValue("", recipe.name, value, reference, vintage)


def feature_panel(
    conn: sqlite3.Connection,
    knowledge_dates: Iterable[str],
    recipes: Sequence[FeatureRecipe],
) -> tuple[FeatureValue, ...]:
    """Build features independently inside each as-of snapshot, preventing revision leakage."""
    recipe_by_series: dict[str, list[FeatureRecipe]] = {}
    for recipe in recipes:
        recipe_by_series.setdefault(recipe.series_id, []).append(recipe)
    panel = as_of_panel(conn, knowledge_dates, series_ids=tuple(recipe_by_series))
    grouped: dict[tuple[str, str], list[PanelRow]] = {}
    for row in panel:
        grouped.setdefault((row.knowledge_date, row.series_id), []).append(row)
    values = []
    for (knowledge_date, series_id), rows in sorted(grouped.items()):
        for recipe in recipe_by_series[series_id]:
            calculated = _calculate(_aggregate(rows, recipe.aggregation), recipe)
            if calculated is not None:
                values.append(
                    FeatureValue(
                        knowledge_date=knowledge_date,
                        feature=calculated.feature,
                        value=calculated.value,
                        source_reference_date=calculated.source_reference_date,
                        source_vintage_date=calculated.source_vintage_date,
                    )
                )
    return tuple(sorted(values, key=lambda item: (item.knowledge_date, item.feature)))


def write_feature_panel(
    rows: Sequence[FeatureValue],
    output: str | Path,
    *,
    file_format: str = "csv",
    layout: str = "wide",
) -> Path:
    """Write feature values in modeling-friendly wide or audit-friendly long form."""
    if layout not in {"long", "wide"}:
        raise ValueError("feature layout must be long or wide")
    if file_format not in {"csv", "parquet", "feather"}:
        raise ValueError("feature format must be csv, parquet, or feather")
    if layout == "long":
        columns = list(FEATURE_COLUMNS)
        records = [asdict(row) for row in rows]
    else:
        feature_names = sorted({row.feature for row in rows})
        columns = ["knowledge_date", *feature_names]
        indexed: dict[str, dict[str, object]] = {}
        for row in rows:
            indexed.setdefault(row.knowledge_date, {"knowledge_date": row.knowledge_date})[
                row.feature
            ] = row.value
        records = [indexed[key] for key in sorted(indexed)]
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if file_format == "csv":
        with destination.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(records)
        return destination
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Parquet/Feather export requires: python -m pip install -e '.[modeling]'"
        ) from exc
    frame = pd.DataFrame.from_records(records, columns=columns)
    if file_format == "parquet":
        frame.to_parquet(destination, index=False)
    else:
        frame.to_feather(destination)
    return destination
