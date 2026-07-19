"""Command-line interface for collection, inspection, vintages and as-of queries."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from .collector import build_client, collect
from .config import load_config
from .db import AS_OF_QUERY, connect, table_counts


def _start_period(months: int | None) -> str | None:
    if months is None:
        return None
    if months < 1:
        raise ValueError("--months must be positive")
    today = date.today()
    absolute = today.year * 12 + today.month - 1 - months
    return f"{absolute // 12:04d}-{absolute % 12 + 1:02d}-01"


def _cmd_collect(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    conn = connect(args.db)
    client = build_client(config, args.mode, args.fixtures)
    start = _start_period(args.months)
    params = {"startPeriod": start} if start and args.mode == "live" else None
    try:
        report = collect(conn, config, client, params=params)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        conn.close()
        return 1
    print(report.log_text)
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    conn.close()
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    counts = table_counts(conn)
    print("Tables:", ", ".join(f"{key}={value}" for key, value in counts.items()))
    for row in conn.execute(
        """
        SELECT series_id, name, frequency, unit, first_observation,
               last_observation, observation_count
          FROM metadata ORDER BY series_id
        """
    ):
        print(
            f"{row['series_id']}: {row['observation_count']} observations "
            f"[{row['first_observation']} .. {row['last_observation']}] "
            f"({row['frequency']}, {row['unit']})"
        )
    conn.close()
    return 0


def _print_rows(rows) -> None:
    print("series_id | reference_date | value | vintage_date")
    for row in rows:
        print(
            f"{row['series_id']} | {row['reference_date']} | {row['value']} | {row['vintage_date']}"
        )


def _cmd_as_of(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    rows = conn.execute(AS_OF_QUERY, {"as_of": args.date}).fetchall()
    if args.series:
        rows = [row for row in rows if row["series_id"] == args.series]
    _print_rows(rows)
    conn.close()
    return 0


def _cmd_vintages(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    rows = conn.execute(
        """
        SELECT series_id, reference_date, value, vintage_date, collected_at
          FROM time_series
         WHERE series_id = ? AND reference_date = ?
         ORDER BY vintage_date, collected_at
        """,
        (args.series, args.reference_date),
    ).fetchall()
    _print_rows(rows)
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kinea")
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--db", required=True)
    collect_parser.add_argument("--mode", choices=("live", "offline"), default="live")
    collect_parser.add_argument("--fixtures")
    collect_parser.add_argument("--months", type=int)
    collect_parser.set_defaults(func=_cmd_collect)

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--db", required=True)
    status_parser.set_defaults(func=_cmd_status)

    as_of_parser = sub.add_parser("as-of")
    as_of_parser.add_argument("--db", required=True)
    as_of_parser.add_argument("--date", required=True)
    as_of_parser.add_argument("--series")
    as_of_parser.set_defaults(func=_cmd_as_of)

    vintage_parser = sub.add_parser("vintages")
    vintage_parser.add_argument("--db", required=True)
    vintage_parser.add_argument("--series", required=True)
    vintage_parser.add_argument("--reference-date", required=True)
    vintage_parser.set_defaults(func=_cmd_vintages)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
