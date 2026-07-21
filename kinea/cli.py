"""Command-line interface for collection, inspection, vintages and as-of queries."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from .collector import build_client, collect
from .config import load_config
from .db import AS_OF_QUERY, connect, table_counts
from .locking import RunLockError, execution_lock
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
    try:
        config = load_config(args.config).select(args.series)
        if args.months is not None and (args.start or args.end):
            raise ValueError("use either --months or --start/--end")
        start = _start_period(args.months) or args.start
        if start:
            date.fromisoformat(start)
        if args.end:
            date.fromisoformat(args.end)
        params = None
        if args.mode == "live":
            params = {
                key: value
                for key, value in {"startPeriod": start, "endPeriod": args.end}.items()
                if value
            } or None
        with execution_lock(args.db, timeout=args.lock_timeout):
            conn = connect(args.db)
            try:
                client = build_client(config, args.mode, args.fixtures)
                report = collect(
                    conn,
                    config,
                    client,
                    params=params,
                    quality_policy=args.quality_policy,
                    dry_run=args.dry_run,
                    archive_dir=args.archive_dir,
                )
            finally:
                conn.close()
    except (Exception, RunLockError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(report.log_text)
    for warning in report.warnings:
        print(f"WARNING: {warning}")
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
    passed = (
        all(not report.issues for report in reports)
        if args.strict
        else all(report.passed for report in reports)
    )
    return 0 if passed else 1


def _cmd_revisions(args: argparse.Namespace) -> int:
    from .analytics import revision_events, revision_reliability, revision_summary

    conn = connect(args.db)
    events = revision_events(conn, args.series)
    if events:
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
            pct = "n/a" if row.mean_pct_revision is None else f"{row.mean_pct_revision:+.2f}%"
            print(
                f"  {row.series_id}: revised={row.n_revised} mean|change|={row.mean_abs_revision:.4g} "
                f"max|change|={row.max_abs_revision:.4g} mean_lag={row.mean_lag_days:.1f}d "
                f"mean_change={row.mean_revision:+.4g} ({row.n_upward}up/{row.n_downward}down) "
                f"mean_pct_change={pct}"
            )
    else:
        print("No multi-vintage observations found.")
    print("\nreliability (trust in the latest print, vs. this series' own typical move):")
    for reliability in revision_reliability(conn, args.series):
        nts = "n/a" if reliability.noise_to_signal is None else f"{reliability.noise_to_signal:.2f}"
        print(
            f"  {reliability.series_id}: revised={reliability.n_revised}/{reliability.n_observations} "
            f"noise_to_signal={nts} bias={reliability.bias_direction}"
        )
    conn.close()
    return 0


def _cmd_passthrough(args: argparse.Namespace) -> int:
    from .econometrics import fx_passthrough

    conn = connect(args.db)
    result = fx_passthrough(
        conn, args.series, fx_series_id=args.fx_series, max_lag_months=args.max_lag
    )
    conn.close()
    corr = "n/a" if result.best_correlation is None else f"{result.best_correlation:+.3f}"
    elasticity = "n/a" if result.elasticity is None else f"{result.elasticity:+.3f}"
    print(f"{result.hicp_series_id} vs {result.fx_series_id} MoM%, lags 0..{args.max_lag}:")
    print(
        f"  best lag: {result.best_lag_months}mo | correlation: {corr} | elasticity: {elasticity}"
    )
    print("  lag_months | correlation | n_pairs")
    for entry in result.by_lag:
        entry_corr = "n/a" if entry.correlation is None else f"{entry.correlation:+.3f}"
        print(f"  {entry.lag_months:>10} | {entry_corr:>11} | {entry.n_pairs}")
    return 0


def _cmd_diffusion(args: argparse.Namespace) -> int:
    from .econometrics import diffusion_index

    conn = connect(args.db)
    readings = diffusion_index(conn, args.series)
    conn.close()
    if not readings:
        print("No months with a computable diffusion reading.")
        return 0
    print("reference_date | accel | decel | flat | diffusion | dominant_component")
    for row in readings:
        index = "n/a" if row.diffusion_index is None else f"{row.diffusion_index:.2f}"
        dominant = row.dominant_component or "n/a"
        print(
            f"{row.reference_date} | {row.n_accelerating} | {row.n_decelerating} | "
            f"{row.n_flat} | {index} | {dominant}"
        )
    return 0


def _cmd_base_effects(args: argparse.Namespace) -> int:
    from .econometrics import base_effect_decomposition

    conn = connect(args.db)
    readings = base_effect_decomposition(conn, args.series)
    conn.close()
    if not readings:
        print("Not enough history for a base-effect decomposition (needs 13+ monthly points).")
        return 0
    print("reference_date | yoy% | yoy_change_pp | fresh_momentum% | base_effect%")
    for row in readings:
        change = "n/a" if row.yoy_change_pct is None else f"{row.yoy_change_pct:+.2f}"
        base = "n/a" if row.base_effect_pct is None else f"{row.base_effect_pct:+.2f}"
        print(
            f"{row.reference_date} | {row.yoy_pct:+.2f} | {change} | "
            f"{row.fresh_momentum_pct:+.2f} | {base}"
        )
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    from .analytics import compare_as_of

    conn = connect(args.db)
    try:
        rows = compare_as_of(
            conn,
            args.left,
            args.right,
            series_ids=args.series,
            include_unchanged=args.include_unchanged,
        )
    finally:
        conn.close()
    print(
        "series_id | reference_date | status | left | right | change | left_vintage | right_vintage"
    )
    for row in rows:
        left = "n/a" if row.left_value is None else f"{row.left_value:.10g}"
        right = "n/a" if row.right_value is None else f"{row.right_value:.10g}"
        change = "n/a" if row.change is None else f"{row.change:+.6g}"
        print(
            f"{row.series_id} | {row.reference_date} | {row.status} | {left} | "
            f"{right} | {change} | {row.left_vintage} | {row.right_vintage}"
        )
    print(f"Differences: {len(rows)}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from .exports import snapshot_rows, write_snapshot

    try:
        conn = connect(args.db)
        try:
            rows = snapshot_rows(conn, as_of=args.as_of, series_ids=args.series)
        finally:
            conn.close()
        destination = write_snapshot(
            rows,
            args.output,
            file_format=args.format,
            layout=args.layout,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Snapshot export: rows={len(rows)}; layout={args.layout}; output={destination}")
    return 0


def _knowledge_dates(args: argparse.Namespace) -> tuple[str, ...]:
    if args.as_of:
        if args.start or args.end:
            raise ValueError("use either --as-of or --start/--end")
        return tuple(item for group in args.as_of for item in group.split(",") if item)
    if not args.start or not args.end:
        raise ValueError("provide --as-of or both --start and --end")
    return knowledge_date_grid(args.start, args.end, args.frequency)


def _cmd_features(args: argparse.Namespace) -> int:
    from .features import default_feature_recipes, feature_panel, write_feature_panel

    try:
        dates = _knowledge_dates(args)
        config = load_config(args.config).select(args.series)
        conn = connect(args.db)
        try:
            rows = feature_panel(conn, dates, default_feature_recipes(config))
        finally:
            conn.close()
        destination = write_feature_panel(
            rows,
            args.output,
            file_format=args.format,
            layout=args.layout,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        f"Feature panel: rows={len(rows)}; knowledge_dates={len(set(row.knowledge_date for row in rows))}; "
        f"output={destination}"
    )
    return 0


def _cmd_source_health(args: argparse.Namespace) -> int:
    from .client import LiveClient
    from .health import format_source_health, source_health

    config = load_config(args.config).select(args.series)
    conn = connect(args.db)
    try:
        report = source_health(
            conn,
            config,
            as_of=args.as_of,
            live_client=LiveClient(config) if args.live else None,
        )
    finally:
        conn.close()
    print(format_source_health(report))
    return 0 if report.status == "pass" else 1


def _cmd_publication_lag(args: argparse.Namespace) -> int:
    from .analytics import publication_lags

    conn = connect(args.db)
    try:
        rows = publication_lags(conn)
    finally:
        conn.close()
    print(
        "series_id | reference_date | publish_date | first_observed | "
        "reference_to_publish_days | publish_to_observed_days"
    )
    for row in rows:
        print(
            f"{row.series_id} | {row.reference_date} | {row.publish_date} | "
            f"{row.first_observed_vintage} | {row.reference_to_publish_days} | "
            f"{row.publish_to_observed_days}"
        )
    return 0


def _cmd_verify_archive(args: argparse.Namespace) -> int:
    from .archive import verify_archive

    valid = verify_archive(args.manifest)
    print(f"Archive: {'PASS' if valid else 'FAIL'}")
    return 0 if valid else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kinea")
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--db", required=True)
    collect_parser.add_argument("--mode", choices=("live", "offline"), default="live")
    collect_parser.add_argument("--fixtures")
    collect_parser.add_argument("--months", type=int)
    collect_parser.add_argument("--start", help="Inclusive source start period")
    collect_parser.add_argument("--end", help="Inclusive source end period")
    collect_parser.add_argument("--series", action="append", help="Collect only this series ID")
    collect_parser.add_argument("--dry-run", action="store_true", help="Plan changes and roll back")
    collect_parser.add_argument("--quality-policy", choices=("warn", "strict"), default="warn")
    collect_parser.add_argument("--archive-dir", help="Optional raw payload archive directory")
    collect_parser.add_argument("--lock-timeout", type=float, default=0.0)
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
    quality_parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    quality_parser.set_defaults(func=_cmd_quality)

    revisions_parser = sub.add_parser("revisions", help="Revision analytics from vintage history")
    revisions_parser.add_argument("--db", required=True)
    revisions_parser.add_argument("--series", help="Optional series filter")
    revisions_parser.set_defaults(func=_cmd_revisions)

    passthrough_parser = sub.add_parser(
        "passthrough", help="EUR/CZK to HICP-component pass-through by lag"
    )
    passthrough_parser.add_argument("--db", required=True)
    passthrough_parser.add_argument("--series", required=True, help="HICP component series_id")
    passthrough_parser.add_argument("--fx-series", default="CZ_FX_EURCZK")
    passthrough_parser.add_argument("--max-lag", type=int, default=6, help="Months to test")
    passthrough_parser.set_defaults(func=_cmd_passthrough)

    diffusion_parser = sub.add_parser(
        "diffusion", help="Month-by-month breadth of HICP component acceleration"
    )
    diffusion_parser.add_argument("--db", required=True)
    diffusion_parser.add_argument(
        "--series", action="append", help="Optional HICP series filter (default: all HICP_*)"
    )
    diffusion_parser.set_defaults(func=_cmd_diffusion)

    base_effects_parser = sub.add_parser(
        "base-effects", help="Decompose YoY swings into base effect vs. fresh momentum"
    )
    base_effects_parser.add_argument("--db", required=True)
    base_effects_parser.add_argument("--series", required=True)
    base_effects_parser.set_defaults(func=_cmd_base_effects)

    diff_parser = sub.add_parser("diff", help="Compare two point-in-time snapshots")
    diff_parser.add_argument("--db", required=True)
    diff_parser.add_argument("--from", dest="left", required=True)
    diff_parser.add_argument("--to", dest="right", required=True)
    diff_parser.add_argument("--series", action="append")
    diff_parser.add_argument("--include-unchanged", action="store_true")
    diff_parser.set_defaults(func=_cmd_diff)

    export_parser = sub.add_parser("export", help="Export current or as-of data")
    export_parser.add_argument("--db", required=True)
    export_parser.add_argument("--as-of")
    export_parser.add_argument("--series", action="append")
    export_parser.add_argument("--layout", choices=("long", "wide"), default="long")
    export_parser.add_argument("--format", choices=("csv", "parquet", "feather"), default="csv")
    export_parser.add_argument("--output", required=True)
    export_parser.set_defaults(func=_cmd_export)

    features_parser = sub.add_parser("features", help="Export vintage-safe modeling features")
    features_parser.add_argument("--db", required=True)
    features_parser.add_argument("--as-of", action="append")
    features_parser.add_argument("--start")
    features_parser.add_argument("--end")
    features_parser.add_argument(
        "--frequency", choices=("daily", "weekly", "monthly"), default="monthly"
    )
    features_parser.add_argument("--series", action="append")
    features_parser.add_argument("--layout", choices=("long", "wide"), default="wide")
    features_parser.add_argument("--format", choices=("csv", "parquet", "feather"), default="csv")
    features_parser.add_argument("--output", required=True)
    features_parser.set_defaults(func=_cmd_features)

    health_parser = sub.add_parser("source-health", help="Operational source and database health")
    health_parser.add_argument("--db", required=True)
    health_parser.add_argument("--as-of")
    health_parser.add_argument("--series", action="append")
    health_parser.add_argument("--live", action="store_true", help="Probe the live ECB API")
    health_parser.set_defaults(func=_cmd_source_health)

    lag_parser = sub.add_parser("publication-lag", help="Publication and observation lag report")
    lag_parser.add_argument("--db", required=True)
    lag_parser.set_defaults(func=_cmd_publication_lag)

    archive_parser = sub.add_parser("verify-archive", help="Verify a raw payload manifest")
    archive_parser.add_argument("--manifest", required=True)
    archive_parser.set_defaults(func=_cmd_verify_archive)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
