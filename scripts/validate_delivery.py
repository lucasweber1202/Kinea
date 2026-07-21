#!/usr/bin/env python3
"""Fail-closed validator for the complete Kinea assignment delivery."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kinea.analytics import compare_as_of, revision_events  # noqa: E402
from kinea.client import OfflineClient  # noqa: E402
from kinea.collector import collect  # noqa: E402
from kinea.config import load_config  # noqa: E402
from kinea.db import connect, get_as_of_view, get_current_view, table_counts  # noqa: E402
from kinea.identifiers import derive_description, derive_name, parse_series_id  # noqa: E402
from kinea.panels import as_of_panel  # noqa: E402

REQUIRED_COLUMNS = {
    "metadata": [
        "series_id",
        "name",
        "description",
        "country",
        "frequency",
        "unit",
        "first_observation",
        "last_observation",
        "observation_count",
        "source_url",
        "last_publish_date",
        "collected_at",
    ],
    "time_series": ["series_id", "reference_date", "vintage_date", "value", "collected_at"],
    "logs": ["id", "started_at", "finished_at", "status", "log_text", "traceback"],
}
EXPECTED_SERIES = {
    "CZ_HICP_CORE_INDEX",
    "CZ_HICP_ENERGY_INDEX",
    "CZ_HICP_FOOD_INDEX",
    "CZ_HICP_SERVICES_INDEX",
    "CZ_FX_EURCZK",
}
REQUIRED_FILES = [
    "README.md",
    "DELIVERY.md",
    "Makefile",
    "pyproject.toml",
    "requirements.txt",
    ".pre-commit-config.yaml",
    ".env.example",
    ".streamlit/config.toml",
    "config/series.json",
    "dashboard/app.py",
    ".github/workflows/validate.yml",
    ".github/workflows/source-contract.yml",
    "fixtures/contracts/EXR.D.CZK.EUR.SP00.A.csv",
    "fixtures/contracts/HICP.M.CZ.N.XEF000.4D0.INX.csv",
    "fixtures/contracts/HICP.M.CZ.N.NRGY00.4D0.INX.csv",
    "fixtures/contracts/HICP.M.CZ.N.FOOD00.4D0.INX.csv",
    "fixtures/contracts/HICP.M.CZ.N.SERV00.4D0.INX.csv",
    "tests/test_contracts.py",
    "tests/test_analysis_cli.py",
    "tests/test_archive.py",
    "tests/test_analytics.py",
    "tests/test_panels.py",
    "tests/test_quality.py",
    "tests/test_vintages_properties.py",
    "tests/test_transforms.py",
    "tests/test_scripts.py",
    "tests/test_dashboard.py",
    "tests/test_exports.py",
    "tests/test_features.py",
    "tests/test_health.py",
    "tests/test_locking.py",
    "tests/test_config.py",
    "scripts/generate_evidence.py",
    "scripts/check_source_contract.py",
    "scripts/validate_delivery.py",
    "kinea/db.py",
    "kinea/archive.py",
    "kinea/vintages.py",
    "kinea/analytics.py",
    "kinea/exports.py",
    "kinea/features.py",
    "kinea/health.py",
    "kinea/locking.py",
    "kinea/panels.py",
    "kinea/quality.py",
    "kinea/transforms.py",
    "kinea/collector.py",
    "kinea/client.py",
    "kinea/parser.py",
    "kinea/identifiers.py",
    "evidence/kinea.db",
    "evidence/database_counts.txt",
    "evidence/data_quality.txt",
    "evidence/idempotency.txt",
    "evidence/revision_demo.txt",
    "evidence/as_of_demo.txt",
    "evidence/pit_panel.csv",
    "evidence/pit_panel.parquet",
    "evidence/feature_panel.csv",
    "evidence/as_of_diff.txt",
    "evidence/sample_query.sql",
    "evidence/sample_query_output.csv",
    "evidence/success_log.txt",
    "evidence/error_log.txt",
    "evidence/live_validation.txt",
    "evidence/live_validation.json",
    "evidence/source_health.txt",
    "evidence/publication_lag.txt",
    "evidence/revision_demo.db",
    "docs/dashboard-overview.png",
    "docs/dashboard-hicp.png",
    "docs/dashboard-fx.png",
    "docs/dashboard-vintages.png",
    "docs/dashboard-as-of.png",
    "docs/dashboard-audit.png",
]


class Validator:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures = 0

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        status = "PASS" if condition else "FAIL"
        suffix = f" - {detail}" if detail else ""
        self.lines.append(f"[{status}] {label}{suffix}")
        if not condition:
            self.failures += 1


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _required_path(relative_path: str, evidence_dir: Path) -> Path:
    prefix = "evidence/"
    if relative_path.startswith(prefix):
        return evidence_dir / relative_path[len(prefix) :]
    return ROOT / relative_path


def _run_validation(evidence_dir: Path) -> Validator:
    result = Validator()
    result.check(
        "Python version supported",
        sys.version_info >= (3, 11),
        f"{sys.version_info.major}.{sys.version_info.minor}",
    )
    missing = [path for path in REQUIRED_FILES if not _required_path(path, evidence_dir).exists()]
    result.check(
        "Required files exist",
        not missing,
        ", ".join(missing) if missing else f"{len(REQUIRED_FILES)} files",
    )
    screenshots = [path for path in REQUIRED_FILES if path.startswith("docs/")]
    result.check(
        "Dashboard screenshots are non-empty",
        all(
            (ROOT / path).exists() and (ROOT / path).stat().st_size > 10_000 for path in screenshots
        ),
    )

    db_path = evidence_dir / "kinea.db"
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        result.check("Database opens successfully", integrity == "ok", str(integrity))
    except sqlite3.Error as exc:
        result.check("Database opens successfully", False, str(exc))

    if conn is not None:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        for table in ("metadata", "time_series", "logs"):
            result.check(f"{table} table exists", table in tables)
        result.check(
            "Database contains exactly the mandatory tables",
            tables == {"metadata", "time_series", "logs"},
        )
        columns_ok = all(
            _columns(conn, table) == columns for table, columns in REQUIRED_COLUMNS.items()
        )
        result.check("Required columns exist", columns_ok)
        metadata_pk = {
            row[1]: row[5] for row in conn.execute("PRAGMA table_info(metadata)") if row[5]
        }
        time_series_pk = {
            row[1]: row[5] for row in conn.execute("PRAGMA table_info(time_series)") if row[5]
        }
        logs_pk = {row[1]: row[5] for row in conn.execute("PRAGMA table_info(logs)") if row[5]}
        result.check(
            "Primary keys are correct",
            metadata_pk == {"series_id": 1}
            and time_series_pk == {"series_id": 1, "reference_date": 2, "vintage_date": 3}
            and logs_pk == {"id": 1},
        )
        series = {row[0] for row in conn.execute("SELECT series_id FROM metadata")}
        result.check(
            "Five series exist in metadata", series == EXPECTED_SERIES, str(sorted(series))
        )
        stored_series = {
            row[0] for row in conn.execute("SELECT DISTINCT series_id FROM time_series")
        }
        result.check("Five series exist in time_series", stored_series == EXPECTED_SERIES)
        result.check("Every series_id is parseable", all(parse_series_id(item) for item in series))
        metadata_semantics = True
        for row in conn.execute("SELECT * FROM metadata"):
            expected_frequency = "daily" if "_FX_" in row["series_id"] else "monthly"
            expected_unit = "currency" if "_FX_" in row["series_id"] else "index"
            metadata_semantics &= (
                row["name"] == derive_name(row["series_id"])
                and row["description"] == derive_description(row["series_id"])
                and row["country"] == "CZ"
                and row["frequency"] == expected_frequency
                and row["unit"] == expected_unit
            )
        result.check("Metadata semantics are derived and consistent", metadata_semantics)
        orphan_count = conn.execute(
            """
            SELECT COUNT(*) FROM time_series t LEFT JOIN metadata m ON m.series_id=t.series_id
            WHERE m.series_id IS NULL
            """
        ).fetchone()[0]
        result.check("Every time_series series_id exists in metadata", orphan_count == 0)
        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        result.check("Foreign-key integrity passes", not foreign_key_errors)
        metadata_ok = True
        for row in conn.execute("SELECT * FROM metadata"):
            actual = conn.execute(
                """
                SELECT MIN(reference_date), MAX(reference_date), COUNT(DISTINCT reference_date)
                FROM time_series WHERE series_id=?
                """,
                (row["series_id"],),
            ).fetchone()
            metadata_ok &= (
                row["first_observation"],
                row["last_observation"],
                row["observation_count"],
            ) == tuple(actual)
        result.check("Metadata counts match time_series", metadata_ok)
        duplicate_count = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT series_id, reference_date, vintage_date, COUNT(*) n
                FROM time_series GROUP BY series_id, reference_date, vintage_date HAVING n > 1
            )
            """
        ).fetchone()[0]
        result.check("No duplicate primary keys", duplicate_count == 0)
        success = conn.execute(
            "SELECT COUNT(*) FROM logs WHERE status='success' AND traceback IS NULL"
        ).fetchone()[0]
        error = conn.execute(
            "SELECT COUNT(*) FROM logs WHERE status='error' AND traceback IS NOT NULL AND traceback<>''"
        ).fetchone()[0]
        result.check("Success log exists", success > 0)
        result.check("Error log with traceback exists", error > 0)
        conn.close()

    with tempfile.TemporaryDirectory() as directory:
        offline = connect(Path(directory) / "idempotency.db")
        config = load_config()
        client = OfflineClient(ROOT / "fixtures" / "v2")
        collect(offline, config, client, collected_at="2026-07-18T10:00:00+00:00")
        before = table_counts(offline)
        collect(offline, config, client, collected_at="2026-07-18T11:00:00+00:00")
        after = table_counts(offline)
        result.check(
            "Second run creates zero metadata rows", after["metadata"] - before["metadata"] == 0
        )
        result.check(
            "Second run creates zero vintage rows",
            after["time_series"] - before["time_series"] == 0,
        )
        result.check("Second run creates exactly one log row", after["logs"] - before["logs"] == 1)
        offline.close()

    demo = connect(evidence_dir / "revision_demo.db")
    revised = demo.execute(
        """
        SELECT series_id, reference_date, COUNT(*) n FROM time_series
        GROUP BY series_id, reference_date HAVING n=2 ORDER BY reference_date DESC LIMIT 1
        """
    ).fetchone()
    result.check("Revision demonstration contains two vintages", revised is not None)
    if revised:
        history = demo.execute(
            """
            SELECT value FROM time_series WHERE series_id=? AND reference_date=?
            ORDER BY vintage_date, collected_at
            """,
            (revised["series_id"], revised["reference_date"]),
        ).fetchall()
        old = next(
            row
            for row in get_as_of_view(demo, "2026-07-10")
            if row["series_id"] == revised["series_id"]
            and row["reference_date"] == revised["reference_date"]
        )
        current = next(
            row
            for row in get_current_view(demo)
            if row["series_id"] == revised["series_id"]
            and row["reference_date"] == revised["reference_date"]
        )
        result.check("Historical as-of returns old value", old["value"] == history[0]["value"])
        result.check("Current view returns revised value", current["value"] == history[1]["value"])
        panel = as_of_panel(
            demo,
            ["2026-07-10", "2026-07-18"],
            series_ids=[revised["series_id"]],
        )
        panel_values = [
            row.value for row in panel if row.reference_date == revised["reference_date"]
        ]
        result.check(
            "Point-in-time panel contains no look-ahead",
            panel_values == [history[0]["value"], history[1]["value"]]
            and all(row.vintage_date <= row.knowledge_date for row in panel),
        )
        revision = revision_events(demo, revised["series_id"])
        result.check(
            "Revision analytics report signed change, magnitude, and observed lag",
            len(revision) == 1
            and revision[0].change == history[1]["value"] - history[0]["value"]
            and revision[0].abs_change == abs(revision[0].change)
            and revision[0].lag_days == 17,
        )
        differences = compare_as_of(
            demo,
            "2026-07-10",
            "2026-07-18",
            series_ids=[revised["series_id"]],
        )
        result.check(
            "Point-in-time diff identifies the revision",
            len(differences) == 1
            and differences[0].status == "revised"
            and differences[0].change == history[1]["value"] - history[0]["value"],
        )
    demo.close()

    pit_csv = (evidence_dir / "pit_panel.csv").read_text(encoding="utf-8")
    result.check(
        "Modeling panel artifacts are populated",
        "knowledge_date,series_id,reference_date,value,vintage_date,collected_at" in pit_csv
        and "2026-07-10" in pit_csv
        and "2026-07-18" in pit_csv
        and (evidence_dir / "pit_panel.parquet").stat().st_size > 0,
    )
    feature_csv = (evidence_dir / "feature_panel.csv").read_text(encoding="utf-8")
    result.check(
        "Vintage-safe feature panel is populated",
        "knowledge_date,CZ_HICP_CORE_INDEX_YOY" in feature_csv
        and "2026-07-10" in feature_csv
        and "2026-07-18" in feature_csv,
    )

    dashboard = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    mandatory_reads = all(token in dashboard for token in ("metadata", "time_series", "logs"))
    legacy_absent = all(
        token not in dashboard for token in ("collection_runs", "is_current", "raw_responses")
    )
    result.check("Dashboard reads the mandatory schema", mandatory_reads and legacy_absent)
    sections = ("Overview", "HICP components", "EUR/CZK", "Vintages", "As-of", "Audit")
    result.check(
        "Dashboard exposes all six required sections",
        all(section in dashboard for section in sections),
    )
    result.check(
        "Dashboard semantics and exports are review-ready",
        "Demo revisions" in dashboard
        and "first observed" in dashboard
        and "Download as-of snapshot CSV" in dashboard
        and "Month-over-month %" in dashboard
        and "3m annualized %" in dashboard
        and "Year-over-year heatmap" in dashboard
        and "_csv_bytes(shown)" in dashboard
        and "use_container_width" not in dashboard,
    )
    cli = (ROOT / "kinea" / "cli.py").read_text(encoding="utf-8")
    result.check(
        "CLI exposes quality, revision, and cross-series forecasting analytics",
        all(
            # A regex (not a plain substring) so this survives ruff wrapping a long
            # add_parser(...) call onto multiple lines, which a fixed literal would not.
            re.search(rf'add_parser\(\s*"{re.escape(command)}"', cli)
            for command in (
                "quality",
                "revisions",
                "diff",
                "export",
                "features",
                "source-health",
                "publication-lag",
                "verify-archive",
                "passthrough",
                "diffusion",
                "base-effects",
            )
        ),
    )
    live = (evidence_dir / "live_validation.txt").read_text(encoding="utf-8")
    result.check(
        "Live ECB validation passed",
        "Status: PASS" in live
        and "HTTP status: 200" in live
        and "live_series_matches: 5/5" in live,
    )
    live_json = json.loads((evidence_dir / "live_validation.json").read_text(encoding="utf-8"))
    samples = live_json.get("samples", [])
    sample_matches_database = True
    live_conn = sqlite3.connect(evidence_dir / "kinea.db")
    for sample in samples:
        stored = live_conn.execute(
            """
            SELECT value FROM time_series
            WHERE series_id=? AND reference_date=?
            ORDER BY vintage_date DESC, collected_at DESC LIMIT 1
            """,
            (sample["series_id"], sample["reference_date"]),
        ).fetchone()
        sample_matches_database &= stored is not None and float(stored[0]) == float(
            sample["raw_value"]
        )
    live_conn.close()
    result.check(
        "Structured live proof is fail-closed",
        live_json.get("status") == "pass"
        and live_json.get("source") == "live"
        and live_json.get("host_status")
        == {"data-api.ecb.europa.eu": 200, "data.ecb.europa.eu": 200}
        and live_json.get("idempotency_delta") == {"metadata": 0, "time_series": 0, "logs": 1}
        and len(samples) == 5
        and all(sample.get("http_status") == 200 and sample.get("match") for sample in samples)
        and sample_matches_database,
    )
    result.check(
        "Operational health and publication-lag reports exist",
        "Status: PASS" in (evidence_dir / "source_health.txt").read_text(encoding="utf-8")
        and "PUBLICATION LAG" in (evidence_dir / "publication_lag.txt").read_text(encoding="utf-8"),
    )
    quality = (evidence_dir / "data_quality.txt").read_text(encoding="utf-8")
    result.check(
        "Semantic data-quality gate passed",
        "Status: PASS" in quality and "RESULT: PASS" in quality,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence-dir",
        default=str(ROOT / "evidence"),
        help="Evidence directory to validate (defaults to the committed evidence directory)",
    )
    parser.add_argument(
        "--report", help="Report destination (defaults to <evidence-dir>/validation_report.txt)"
    )
    args = parser.parse_args()
    evidence_dir = Path(args.evidence_dir).expanduser().resolve()
    report_path = (
        Path(args.report).expanduser().resolve()
        if args.report
        else evidence_dir / "validation_report.txt"
    )
    try:
        result = _run_validation(evidence_dir)
    except Exception as exc:
        result = Validator()
        result.check("Validator completed without internal error", False, repr(exc))
    status = "READY" if result.failures == 0 else "NOT READY"
    output = "\n".join([*result.lines, "", f"DELIVERY STATUS: {status}", ""])
    report_path.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if result.failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
