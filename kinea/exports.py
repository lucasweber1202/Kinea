"""Current and point-in-time snapshot exports in long or wide layouts."""

from __future__ import annotations

import csv
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable

from .db import AS_OF_QUERY, CURRENT_QUERY

LONG_COLUMNS = (
    "series_id",
    "reference_date",
    "value",
    "vintage_date",
    "collected_at",
)


def snapshot_rows(
    conn: sqlite3.Connection,
    *,
    as_of: str | None = None,
    series_ids: Iterable[str] | None = None,
) -> list[dict[str, object]]:
    """Return a current or historical snapshot as plain serializable records."""
    if as_of is not None:
        date.fromisoformat(as_of)
        rows = conn.execute(AS_OF_QUERY, {"as_of": as_of}).fetchall()
    else:
        rows = conn.execute(CURRENT_QUERY).fetchall()
    selected = set(series_ids or ())
    return [
        {column: row[column] for column in LONG_COLUMNS}
        for row in rows
        if not selected or row["series_id"] in selected
    ]


def _wide_records(rows: list[dict[str, object]]) -> tuple[list[str], list[dict[str, object]]]:
    series_ids = sorted({str(row["series_id"]) for row in rows})
    by_date: dict[str, dict[str, object]] = {}
    for row in rows:
        record = by_date.setdefault(
            str(row["reference_date"]), {"reference_date": row["reference_date"]}
        )
        record[str(row["series_id"])] = row["value"]
    return ["reference_date", *series_ids], [by_date[key] for key in sorted(by_date)]


def write_snapshot(
    rows: list[dict[str, object]],
    output: str | Path,
    *,
    file_format: str = "csv",
    layout: str = "long",
) -> Path:
    """Write snapshot rows as CSV, Parquet or Feather."""
    if layout not in {"long", "wide"}:
        raise ValueError("snapshot layout must be long or wide")
    normalized_format = file_format.lower()
    if normalized_format not in {"csv", "parquet", "feather"}:
        raise ValueError("snapshot format must be csv, parquet, or feather")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    columns, records = (list(LONG_COLUMNS), rows) if layout == "long" else _wide_records(rows)
    if normalized_format == "csv":
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
    if normalized_format == "parquet":
        frame.to_parquet(destination, index=False)
    else:
        frame.to_feather(destination)
    return destination
