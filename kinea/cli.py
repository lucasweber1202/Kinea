"""Command-line interface for collection, inspection, vintages and as-of queries."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from .collector import build_client, collect
from .config import load_config
from .db import AS_OF_QUERY, connect, table_counts
from .panels import as_of_panel, knowledge_date_grid, write_panel


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


def _cmd_panel(args: argparse.Namespace) -> int:
    try:
        if args.as_of:
            if args.start or args.end:
                raise ValueError("use either --as-of or --start/--end, not both")
            dates = tuple(item for group in args.as_of for item in group.split(",") if item)
        else:
            if not args.start or not args.end:
                raise ValueError("panel export requires --as-of or both --start and --end")
            dates = knowledge_date_grid(args.start, args.end, args.frequency)
        conn = connect(args.db)
        try:
            rows = as_of_panel(conn, dates, series_ids=args.series)
        finally:
            conn.close()
        destination = write_panel(rows, args.output, args.format)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        f"Point-in-time panel: {len(rows)} rows; {len(set(row.knowledge_date for row in rows))} "
        f"knowledge dates; output={destination}"
    )
    return 0


def _cmd_quality(args: argparse.Namespace) -> int:
    from .quality import evaluate_database, format_quality_report

    config = load_config(args.config)
    conn = connect(args.db)
    as_of = args.as_of or date.today().isoformat()
    reports = evaluate_database(conn, config, as_of=as_of)
    conn.close()
    print(format_quality_report(reports, as_of=as_of))
    return 0 if all(report.passed for report in reports) else 1


def _cmd_revisions(args: argparse.Namespace) -> int:
    from .analytics import revision_events, revision_summary

    conn = connect(args.db)
    events = revision_events(conn, args.series)
    if not events:
        print("No multi-vintage observations found.")
        conn.close()
        return 0
    print("series_id | reference_date | vintages | first | latest | change | pct | lag_days")
    for event in events:
        pct = "n/a" if event.pct_change is None else f"{event.pct_change:+.2f}%"
        print(
            f"{event.series_id} | {event.reference_date} | {event.n_vintages} | "
            f"{event.first_value} | {event.latest_value} | {event.change:+.4g} | "
            f"{pct} | {event.lag_days}"
        )
    print("\nsummary:")
    for row in revision_summary(conn, args.series):
        print(
            f"  {row.series_id}: revised={row.n_revised} mean|change|={row.mean_abs_revision:.4g} "
            f"max|change|={row.max_abs_revision:.4g} mean_lag={row.mean_lag_days:.1f}d"
        )
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

    panel_parser = sub.add_parser("panel", help="Export a look-ahead-free point-in-time panel")
    panel_parser.add_argument("--db", required=True)
    panel_parser.add_argument(
        "--as-of",
        action="append",
        help="Knowledge date; repeat or pass comma-separated dates",
    )
    panel_parser.add_argument("--start", help="Inclusive grid start date")
    panel_parser.add_argument("--end", help="Inclusive grid end date")
    panel_parser.add_argument(
        "--frequency", choices=("daily", "weekly", "monthly"), default="monthly"
    )
    panel_parser.add_argument("--series", action="append", help="Optional series filter")
    panel_parser.add_argument("--output", required=True)
    panel_parser.add_argument("--format", choices=("csv", "parquet", "feather"), default="csv")
    panel_parser.set_defaults(func=_cmd_panel)

    quality_parser = sub.add_parser("quality", help="Run the semantic data-quality gate")
    quality_parser.add_argument("--db", required=True)
    quality_parser.add_argument("--as-of", help="Reference date for freshness (default: today)")
    quality_parser.set_defaults(func=_cmd_quality)

    revisions_parser = sub.add_parser("revisions", help="Revision analytics from vintage history")
    revisions_parser.add_argument("--db", required=True)
    revisions_parser.add_argument("--series", help="Optional series filter")
    revisions_parser.set_defaults(func=_cmd_revisions)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
