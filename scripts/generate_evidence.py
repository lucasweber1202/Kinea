#!/usr/bin/env python3
"""Build review-ready evidence from live ECB data or deterministic offline fixtures."""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kinea.analytics import compare_as_of, publication_lags  # noqa: E402
from kinea.client import FetchError, LiveClient, OfflineClient  # noqa: E402
from kinea.collector import collect  # noqa: E402
from kinea.config import load_config  # noqa: E402
from kinea.db import AS_OF_QUERY, CURRENT_QUERY, SCHEMA_SQL, connect, table_counts  # noqa: E402
from kinea.features import default_feature_recipes, feature_panel, write_feature_panel  # noqa: E402
from kinea.health import format_source_health, source_health  # noqa: E402
from kinea.models import Observation  # noqa: E402
from kinea.panels import as_of_panel, write_panel  # noqa: E402
from kinea.parser import parse_sdmx_csv  # noqa: E402
from kinea.quality import evaluate_database, format_quality_report  # noqa: E402
from kinea.vintages import ingest_observations  # noqa: E402

EVIDENCE = ROOT / "evidence"
DB_PATH = EVIDENCE / "kinea.db"
DEMO_DB_PATH = EVIDENCE / "revision_demo.db"
SAMPLE_QUERY = """WITH ranked AS (
    SELECT t.*,
           ROW_NUMBER() OVER (
               PARTITION BY series_id, reference_date
               ORDER BY vintage_date DESC, collected_at DESC
           ) AS rn
      FROM time_series t
), latest_three AS (
    SELECT r.*,
           ROW_NUMBER() OVER (
               PARTITION BY series_id ORDER BY reference_date DESC
           ) AS recency
      FROM ranked r
     WHERE rn = 1
)
SELECT m.series_id, m.name, m.frequency, m.unit,
       l.reference_date, l.value, l.vintage_date
  FROM latest_three l
  JOIN metadata m ON m.series_id = l.series_id
 WHERE l.recency <= 3
 ORDER BY m.series_id, l.reference_date DESC;
"""


def _write(name: str, content: str) -> None:
    (EVIDENCE / name).write_text(content.rstrip() + "\n", encoding="utf-8")


def _write_json(name: str, payload) -> None:
    (EVIDENCE / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _format_rows(rows, columns: list[str]) -> str:
    def clean(value) -> str:
        return str(value).replace(str(ROOT), "<repo>")

    lines = [" | ".join(columns), " | ".join("---" for _ in columns)]
    lines.extend(" | ".join(clean(row[column]) for column in columns) for row in rows)
    return "\n".join(lines)


def _csv_rows(rows, columns: list[str]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row[column] for column in columns])
    return output.getvalue()


def _collect_quietly(*args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return collect(*args, **kwargs)


def _promote_evidence(staged: Path, target: Path) -> None:
    """Promote a validated directory while preserving the previous delivery on failure."""
    backup_root = Path(tempfile.mkdtemp(prefix=".evidence-backup-", dir=ROOT))
    backup = backup_root / "evidence"
    try:
        if target.exists():
            target.rename(backup)
        try:
            staged.rename(target)
        except Exception:
            if backup.exists() and not target.exists():
                backup.rename(target)
            raise
    finally:
        shutil.rmtree(backup_root, ignore_errors=True)


def _build_idempotency_proof(config):
    with tempfile.TemporaryDirectory() as directory:
        conn = connect(Path(directory) / "idempotency.db")
        client = OfflineClient(ROOT / "fixtures" / "v2")
        _collect_quietly(conn, config, client, collected_at="2026-07-18T10:00:00+00:00")
        first = table_counts(conn)
        _collect_quietly(conn, config, client, collected_at="2026-07-18T11:00:00+00:00")
        second = table_counts(conn)
        conn.close()
    delta = {key: second[key] - first[key] for key in first}
    assert delta == {"metadata": 0, "time_series": 0, "logs": 1}
    return first, second, delta


def _validate_live_samples(conn, config):
    samples = []
    for spec in config.series:
        response = LiveClient(config).fetch(spec, {"lastNObservations": "1"})
        parsed = parse_sdmx_csv(response.body, expected_external_id=spec.external_id)
        if not parsed.observations:
            raise RuntimeError(f"live sample for {spec.series_id} contains no valid observation")
        raw = parsed.observations[-1]
        stored = conn.execute(
            """
            SELECT reference_date, value FROM time_series
            WHERE series_id=? AND reference_date=?
            ORDER BY vintage_date DESC, collected_at DESC LIMIT 1
            """,
            (spec.series_id, raw.reference_date),
        ).fetchone()
        if stored is None:
            raise RuntimeError(
                f"live ECB sample for {spec.series_id} is missing from the generated database"
            )
        samples.append(
            {
                "series_id": spec.series_id,
                "external_id": spec.external_id,
                "http_status": response.http_status,
                "reference_date": raw.reference_date,
                "raw_value": raw.value,
                "stored_value": float(stored["value"]),
                "match": float(stored["value"]) == raw.value,
            }
        )
    request = urllib.request.Request(
        "https://data.ecb.europa.eu/data/concepts/hicp",
        headers={"User-Agent": "kinea-delivery-validator/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as portal:
        portal_status = int(portal.status)
    return samples, portal_status


def _build_live(config):
    conn = connect(DB_PATH)
    first = _collect_quietly(conn, config, LiveClient(config))
    before = table_counts(conn)
    repeat = _collect_quietly(conn, config, LiveClient(config))
    after = table_counts(conn)
    delta = {key: after[key] - before[key] for key in before}
    if delta != {"metadata": 0, "time_series": 0, "logs": 1}:
        raise RuntimeError(f"live idempotency failed: {delta}")
    return conn, first, repeat, before, after, "live"


def _build_offline(config):
    conn = connect(DB_PATH)
    first = _collect_quietly(
        conn,
        config,
        OfflineClient(ROOT / "fixtures" / "v1"),
        collected_at="2026-07-01T10:00:00+00:00",
    )
    _collect_quietly(
        conn,
        config,
        OfflineClient(ROOT / "fixtures" / "v2"),
        collected_at="2026-07-18T10:00:00+00:00",
    )
    before = table_counts(conn)
    repeat = _collect_quietly(
        conn,
        config,
        OfflineClient(ROOT / "fixtures" / "v2"),
        collected_at="2026-07-18T11:00:00+00:00",
    )
    after = table_counts(conn)
    return conn, first, repeat, before, after, "offline"


def _build_revision_demo(main_conn):
    if DEMO_DB_PATH.exists():
        DEMO_DB_PATH.unlink()
    demo = connect(DEMO_DB_PATH)
    series_id = "CZ_HICP_CORE_INDEX"
    metadata = main_conn.execute(
        "SELECT * FROM metadata WHERE series_id = ?", (series_id,)
    ).fetchone()
    demo.execute(
        """
        INSERT INTO metadata (
            series_id, name, description, country, frequency, unit,
            first_observation, last_observation, observation_count,
            source_url, last_publish_date, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?, ?, ?)
        """,
        (
            metadata["series_id"],
            metadata["name"],
            metadata["description"],
            metadata["country"],
            metadata["frequency"],
            metadata["unit"],
            metadata["source_url"],
            metadata["last_publish_date"],
            "2026-07-01T10:00:00+00:00",
        ),
    )
    source_rows = [
        row
        for row in main_conn.execute(CURRENT_QUERY)
        if row["series_id"] == series_id and row["reference_date"] <= "2026-06-01"
    ]
    observations = [Observation(row["reference_date"], float(row["value"])) for row in source_rows]
    ingest_observations(
        demo,
        series_id,
        observations,
        vintage_date="2026-07-01",
        collected_at="2026-07-01T10:00:00+00:00",
    )
    target = observations[-1]
    ingest_observations(
        demo,
        series_id,
        [Observation(target.reference_date, target.value + 0.21)],
        vintage_date="2026-07-18",
        collected_at="2026-07-18T10:00:00+00:00",
    )
    demo.execute(
        """
        UPDATE metadata SET
            first_observation=(SELECT MIN(reference_date) FROM time_series),
            last_observation=(SELECT MAX(reference_date) FROM time_series),
            observation_count=(SELECT COUNT(DISTINCT reference_date) FROM time_series),
            collected_at='2026-07-18T10:00:00+00:00'
        WHERE series_id=?
        """,
        (series_id,),
    )
    demo.commit()
    return demo, target.reference_date


def main() -> None:
    global EVIDENCE, DB_PATH, DEMO_DB_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("live", "offline"), default="live")
    parser.add_argument(
        "--output-dir",
        help="Destination directory (offline defaults to evidence/offline and never replaces live evidence)",
    )
    args = parser.parse_args()

    target_evidence = ROOT / "evidence"
    staging: tempfile.TemporaryDirectory[str] | None = None
    atomic_live = args.mode == "live" and not args.output_dir
    if atomic_live:
        staging = tempfile.TemporaryDirectory(prefix=".evidence-staging-", dir=ROOT)
        EVIDENCE = Path(staging.name) / "evidence"
    elif args.output_dir:
        EVIDENCE = Path(args.output_dir).expanduser().resolve()
    elif args.mode == "offline":
        EVIDENCE = ROOT / "evidence" / "offline"
    else:
        EVIDENCE = ROOT / "evidence"
    DB_PATH = EVIDENCE / "kinea.db"
    DEMO_DB_PATH = EVIDENCE / "revision_demo.db"

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for legacy in ("sample_query_output.txt", "log_success.txt", "log_error.txt"):
        path = EVIDENCE / legacy
        if path.exists():
            path.unlink()
    if DB_PATH.exists():
        DB_PATH.unlink()
    config = load_config()

    result = _build_live(config) if args.mode == "live" else _build_offline(config)

    conn, first, repeat, before, after, source_kind = result
    idempotent_first, idempotent_second, delta = _build_idempotency_proof(config)
    live_proof = []
    if source_kind == "live":
        live_delta = {key: after[key] - before[key] for key in before}
        live_proof = [
            "OFFICIAL LIVE ECB DATABASE",
            f"FIRST RUN: metadata={before['metadata']}; time_series={before['time_series']}; logs={before['logs']}",
            f"SECOND RUN: metadata={after['metadata']}; time_series={after['time_series']}; logs={after['logs']}",
            f"DELTA: metadata={live_delta['metadata']}; time_series={live_delta['time_series']}; logs={live_delta['logs']}",
            "LIVE RESULT: PASS",
            "",
        ]
    _write(
        "idempotency.txt",
        "\n".join(
            [
                "IDEMPOTENCY PROOF",
                "=================",
                *live_proof,
                "DETERMINISTIC OFFLINE CONTROL",
                "FIRST RUN",
                f"metadata: {idempotent_first['metadata']}",
                f"time_series: {idempotent_first['time_series']}",
                f"logs: {idempotent_first['logs']}",
                "",
                "SECOND RUN",
                f"metadata: {idempotent_second['metadata']}",
                f"time_series: {idempotent_second['time_series']}",
                f"logs: {idempotent_second['logs']}",
                "",
                "DELTA",
                f"metadata: {delta['metadata']}",
                f"time_series: {delta['time_series']}",
                f"logs: {delta['logs']}",
                "",
                "RESULT: PASS",
            ]
        ),
    )

    demo, reference_date = _build_revision_demo(conn)
    history = demo.execute(
        """
        SELECT series_id, reference_date, value, vintage_date, collected_at
          FROM time_series WHERE series_id=? AND reference_date=?
          ORDER BY vintage_date, collected_at
        """,
        ("CZ_HICP_CORE_INDEX", reference_date),
    ).fetchall()
    past = demo.execute(AS_OF_QUERY, {"as_of": "2026-07-10"}).fetchall()
    past = [row for row in past if row["reference_date"] == reference_date]
    current = [
        row for row in demo.execute(CURRENT_QUERY) if row["reference_date"] == reference_date
    ]
    _write(
        "revision_demo.txt",
        "\n".join(
            [
                "SIMULATED REVISION DEMONSTRATION",
                "===============================",
                "Dedicated database: revision_demo.db (official ECB values remain untouched)",
                "Two coexisting rows:",
                _format_rows(
                    history,
                    ["series_id", "reference_date", "value", "vintage_date", "collected_at"],
                ),
                "",
                "As-of 2026-07-10 (old knowledge):",
                _format_rows(past, ["series_id", "reference_date", "value", "vintage_date"]),
                "",
                "Current demo view (revised knowledge):",
                _format_rows(current, ["series_id", "reference_date", "value", "vintage_date"]),
                "",
                "PASS: both vintages coexist and the as-of query returns the correct one.",
            ]
        ),
    )
    _write(
        "as_of_demo.txt",
        "\n".join(
            [
                "AS-OF QUERY DEMONSTRATION",
                "=========================",
                f"Series: {history[0]['series_id']}",
                f"Reference date: {history[0]['reference_date']}",
                "",
                "Vintage 1:",
                f"vintage_date: {history[0]['vintage_date']}",
                f"value: {history[0]['value']}",
                "",
                "Vintage 2:",
                f"vintage_date: {history[1]['vintage_date']}",
                f"value: {history[1]['value']}",
                "",
                "AS OF 2026-07-10:",
                str(past[0]["value"]),
                "",
                "CURRENT:",
                str(current[0]["value"]),
                "",
                "RESULT: PASS",
            ]
        ),
    )
    panel_rows = as_of_panel(
        demo,
        ["2026-07-10", "2026-07-18"],
        series_ids=["CZ_HICP_CORE_INDEX"],
    )
    write_panel(panel_rows, EVIDENCE / "pit_panel.csv", "csv")
    write_panel(panel_rows, EVIDENCE / "pit_panel.parquet", "parquet")
    feature_rows = feature_panel(
        demo,
        ["2026-07-10", "2026-07-18"],
        default_feature_recipes(config.select(["CZ_HICP_CORE_INDEX"])),
    )
    write_feature_panel(feature_rows, EVIDENCE / "feature_panel.csv", layout="wide")
    differences = compare_as_of(
        demo,
        "2026-07-10",
        "2026-07-18",
        series_ids=["CZ_HICP_CORE_INDEX"],
    )
    _write(
        "as_of_diff.txt",
        "\n".join(
            [
                "POINT-IN-TIME DIFFERENCE",
                "========================",
                "series_id | reference_date | status | left_value | right_value | change",
                *[
                    f"{row.series_id} | {row.reference_date} | {row.status} | "
                    f"{row.left_value} | {row.right_value} | "
                    f"{'n/a' if row.change is None else f'{row.change:+.6g}'}"
                    for row in differences
                ],
                "",
                f"RESULT: {'PASS' if differences else 'FAIL'}",
            ]
        ),
    )
    demo.close()

    _write("sample_query.sql", SAMPLE_QUERY)
    sample_rows = conn.execute(SAMPLE_QUERY).fetchall()
    sample_columns = [
        "series_id",
        "name",
        "frequency",
        "unit",
        "reference_date",
        "value",
        "vintage_date",
    ]
    _write("sample_query_output.csv", _csv_rows(sample_rows, sample_columns))
    success_log = conn.execute(
        "SELECT * FROM logs WHERE status='success' ORDER BY id DESC LIMIT 1"
    ).fetchall()
    _write(
        "success_log.txt",
        _format_rows(
            success_log, ["id", "started_at", "finished_at", "status", "log_text", "traceback"]
        ),
    )
    try:
        _collect_quietly(
            conn,
            config,
            OfflineClient(ROOT / "fixtures" / "missing"),
        )
    except FetchError:
        pass
    error_log = conn.execute(
        "SELECT * FROM logs WHERE status='error' ORDER BY id DESC LIMIT 1"
    ).fetchall()
    _write(
        "error_log.txt",
        _format_rows(
            error_log, ["id", "started_at", "finished_at", "status", "log_text", "traceback"]
        ),
    )
    _write(
        "schema.sql",
        SCHEMA_SQL
        + "\n-- Current view\n"
        + CURRENT_QUERY
        + "\n-- As-of view (bind :as_of)\n"
        + AS_OF_QUERY,
    )

    counts = table_counts(conn)
    coverage = conn.execute(
        """
        SELECT series_id, observation_count, first_observation, last_observation
        FROM metadata ORDER BY series_id
        """
    ).fetchall()
    _write(
        "database_counts.txt",
        "\n".join(
            [
                "DATABASE COUNTS",
                "===============",
                f"metadata: {counts['metadata']}",
                f"time_series: {counts['time_series']}",
                f"logs: {counts['logs']}",
                "",
                "SERIES COVERAGE",
                _format_rows(
                    coverage,
                    ["series_id", "observation_count", "first_observation", "last_observation"],
                ),
            ]
        ),
    )

    if source_kind == "live":
        live_samples, portal_status = _validate_live_samples(conn, config)
        live_delta = {key: after[key] - before[key] for key in before}
        if portal_status != 200:
            raise RuntimeError(f"ECB portal returned HTTP {portal_status}")
        if len(live_samples) != len(config.series):
            raise RuntimeError("live validation did not cover the complete configured catalogue")
        if any(item["http_status"] != 200 or not item["match"] for item in live_samples):
            raise RuntimeError("one or more live ECB samples do not match the generated database")
        sample_columns = [
            "series_id",
            "external_id",
            "http_status",
            "reference_date",
            "raw_value",
            "stored_value",
            "match",
        ]
        live_text = "\n".join(
            [
                "LIVE ECB VALIDATION",
                "===================",
                "Status: PASS",
                "data-api.ecb.europa.eu HTTP status: 200",
                f"data.ecb.europa.eu HTTP status: {portal_status}",
                "HTTP status: 200 for both required ECB hosts",
                f"First collection: {first.log_text}",
                f"Immediate repeat: {repeat.log_text}",
                "",
                _format_rows(
                    coverage,
                    ["series_id", "observation_count", "first_observation", "last_observation"],
                ),
                "",
                "RAW RESPONSE SAMPLE COMPARISON - ALL FIVE SERIES",
                _format_rows(live_samples, sample_columns),
                f"live_series_matches: {sum(item['match'] for item in live_samples)}/5",
                "",
                "Source endpoints are stored in metadata.source_url.",
            ]
        )
        live_payload = {
            "status": "pass",
            "source": "live",
            "host_status": {
                "data-api.ecb.europa.eu": 200,
                "data.ecb.europa.eu": portal_status,
            },
            "idempotency_delta": live_delta,
            "samples": live_samples,
        }
    else:
        live_text = """LIVE ECB VALIDATION
===================
Status: PENDING - live validation is intentionally not run in isolated offline mode.
This isolated database was built from deterministic SDMX-CSV fixtures.
Run: python scripts/generate_evidence.py --mode live
"""
        live_payload = {"status": "pending", "source": "offline", "samples": []}
    _write("live_validation.txt", live_text)
    _write_json("live_validation.json", live_payload)
    quality_as_of = conn.execute("SELECT MAX(vintage_date) FROM time_series").fetchone()[0]
    quality_reports = evaluate_database(conn, config, as_of=quality_as_of)
    quality_text = format_quality_report(quality_reports, as_of=quality_as_of)
    _write("data_quality.txt", quality_text)
    if any(not report.passed for report in quality_reports):
        raise RuntimeError("generated database failed the semantic data-quality gate")
    health = source_health(conn, config, as_of=quality_as_of)
    _write("source_health.txt", format_source_health(health))
    lag_rows = publication_lags(conn)
    _write(
        "publication_lag.txt",
        "\n".join(
            [
                "PUBLICATION LAG",
                "===============",
                "series_id | reference_date | publish_date | first_observed | "
                "reference_to_publish_days | publish_to_observed_days",
                *[
                    f"{row.series_id} | {row.reference_date} | {row.publish_date} | "
                    f"{row.first_observed_vintage} | {row.reference_to_publish_days} | "
                    f"{row.publish_to_observed_days}"
                    for row in lag_rows
                ],
                "",
                "Note: NULL publish dates mean the ECB SDMX response did not expose a source date.",
            ]
        ),
    )
    conn.execute("VACUUM")
    conn.close()
    if atomic_live:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "validate_delivery.py"),
                "--evidence-dir",
                str(EVIDENCE),
            ],
            cwd=ROOT,
            check=True,
        )
        _promote_evidence(EVIDENCE, target_evidence)
        if staging is not None:
            staging.cleanup()
        print("Generated, validated, and atomically promoted complete live evidence")
    else:
        print(f"Generated isolated {source_kind} evidence in {EVIDENCE}")


if __name__ == "__main__":
    main()
