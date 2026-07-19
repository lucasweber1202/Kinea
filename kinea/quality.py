"""Semantic data-quality checks applied before ingest and to delivered databases."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from .config import Config, SeriesSpec
from .models import Observation


class DataQualityError(ValueError):
    """Raised when a series violates a configured semantic invariant."""


QUALITY_POLICIES = {"warn", "strict"}


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class QualityReport:
    series_id: str
    observation_count: int
    first_observation: str | None
    last_observation: str | None
    issues: tuple[QualityIssue, ...]

    @property
    def status(self) -> str:
        if any(issue.severity == "error" for issue in self.issues):
            return "error"
        if self.issues:
            return "warning"
        return "pass"

    @property
    def passed(self) -> bool:
        return self.status != "error"


def _month_number(value: date) -> int:
    return value.year * 12 + value.month


def evaluate_observations(
    spec: SeriesSpec,
    observations: Iterable[Observation],
    *,
    as_of: str,
    previous: Observation | None = None,
) -> QualityReport:
    """Evaluate range, jumps, cadence, future dates, and frequency-aware staleness."""
    as_of_date = date.fromisoformat(as_of)
    ordered = sorted(observations, key=lambda item: item.reference_date)
    issues: list[QualityIssue] = []
    if not ordered:
        issues.append(QualityIssue("empty_series", "response contained zero valid observations"))
        return QualityReport(spec.series_id, 0, None, None, tuple(issues))

    for observation in ordered:
        if spec.min_value is not None and observation.value < spec.min_value:
            issues.append(
                QualityIssue(
                    "below_minimum",
                    f"{observation.reference_date}: {observation.value} < {spec.min_value}",
                )
            )
        if spec.max_value is not None and observation.value > spec.max_value:
            issues.append(
                QualityIssue(
                    "above_maximum",
                    f"{observation.reference_date}: {observation.value} > {spec.max_value}",
                )
            )
        if date.fromisoformat(observation.reference_date) > as_of_date:
            issues.append(
                QualityIssue(
                    "future_observation",
                    f"{observation.reference_date} is after collection date {as_of}",
                )
            )

    comparisons = ([previous] if previous is not None else []) + ordered
    for earlier, later in zip(comparisons, comparisons[1:], strict=False):
        earlier_date = date.fromisoformat(earlier.reference_date)
        later_date = date.fromisoformat(later.reference_date)
        if spec.frequency == "monthly":
            gap = _month_number(later_date) - _month_number(earlier_date)
            if gap > 1:
                issues.append(
                    QualityIssue(
                        "missing_months",
                        f"gap of {gap - 1} month(s) before {later.reference_date}",
                        severity="warning",
                    )
                )
        elif spec.max_gap_days is not None:
            gap_days = (later_date - earlier_date).days
            if gap_days > spec.max_gap_days:
                issues.append(
                    QualityIssue(
                        "cadence_gap",
                        f"gap of {gap_days} days before {later.reference_date}",
                        severity="warning",
                    )
                )

        if spec.max_change_pct is not None and earlier.value != 0:
            change_pct = abs(later.value / earlier.value - 1.0) * 100.0
            if change_pct > spec.max_change_pct:
                issues.append(
                    QualityIssue(
                        "implausible_change",
                        f"{later.reference_date}: {change_pct:.2f}% change exceeds "
                        f"{spec.max_change_pct:.2f}%",
                        severity="warning",
                    )
                )

    last_date = date.fromisoformat(ordered[-1].reference_date)
    if spec.stale_after_days is not None:
        lag_days = (as_of_date - last_date).days
        if lag_days > spec.stale_after_days:
            issues.append(
                QualityIssue(
                    "stale_series",
                    f"latest observation is {lag_days} days old (limit {spec.stale_after_days})",
                    severity="warning",
                )
            )

    return QualityReport(
        series_id=spec.series_id,
        observation_count=len(ordered),
        first_observation=ordered[0].reference_date,
        last_observation=ordered[-1].reference_date,
        issues=tuple(issues),
    )


def blocking_issues(report: QualityReport, *, policy: str = "warn") -> tuple[QualityIssue, ...]:
    """Return issues that should stop ingest under the selected policy.

    The assignment explicitly asks malformed individual records to be warned and skipped.  Gaps,
    cadence anomalies, suspicious jumps and staleness are therefore non-blocking by default.  A
    production scheduler can opt into ``strict`` to fail on every semantic issue.
    """
    if policy not in QUALITY_POLICIES:
        raise ValueError(f"quality policy must be one of: {', '.join(sorted(QUALITY_POLICIES))}")
    if policy == "strict":
        return report.issues
    return tuple(issue for issue in report.issues if issue.severity == "error")


def evaluate_database(
    conn: sqlite3.Connection, config: Config, *, as_of: str
) -> tuple[QualityReport, ...]:
    """Run the same semantic checks against each current series in a database."""
    reports = []
    for spec in config.series:
        rows = conn.execute(
            """
            SELECT reference_date, value
            FROM (
                SELECT t.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY series_id, reference_date
                           ORDER BY vintage_date DESC, collected_at DESC
                       ) AS rn
                FROM time_series t
                WHERE series_id = ?
            ) ranked
            WHERE rn = 1
            ORDER BY reference_date
            """,
            (spec.series_id,),
        ).fetchall()
        observations = [Observation(row["reference_date"], float(row["value"])) for row in rows]
        reports.append(evaluate_observations(spec, observations, as_of=as_of))
    return tuple(reports)


def format_quality_report(reports: Iterable[QualityReport], *, as_of: str) -> str:
    """Render a deterministic text artifact suitable for review and alerting."""
    materialized = tuple(reports)
    overall = "PASS" if all(report.passed for report in materialized) else "FAIL"
    lines = [
        "DATA QUALITY REPORT",
        "===================",
        f"As of: {as_of}",
        f"Status: {overall}",
        "",
    ]
    for report in materialized:
        lines.append(
            f"{report.series_id}: {report.status.upper()} | observations={report.observation_count} | "
            f"first={report.first_observation} | last={report.last_observation}"
        )
        lines.extend(
            f"  - {issue.severity.upper()} {issue.code}: {issue.message}" for issue in report.issues
        )
    lines.append("")
    lines.append(f"RESULT: {overall}")
    return "\n".join(lines)
