#!/usr/bin/env python3
"""Network contract check for the five configured ECB SDMX series."""

from __future__ import annotations

import csv
import io
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kinea.client import LiveClient  # noqa: E402
from kinea.config import load_config  # noqa: E402
from kinea.parser import parse_sdmx_csv  # noqa: E402
from kinea.quality import evaluate_observations  # noqa: E402

REQUIRED_COLUMNS = {"KEY", "TIME_PERIOD", "OBS_VALUE"}


def main() -> int:
    config = load_config()
    client = LiveClient(config)
    failures: list[str] = []
    for spec in config.series:
        try:
            result = client.fetch(spec, {"lastNObservations": "3"})
            columns = set(csv.DictReader(io.StringIO(result.body)).fieldnames or ())
            missing = REQUIRED_COLUMNS - columns
            if missing:
                raise ValueError(f"missing columns: {sorted(missing)}")
            parsed = parse_sdmx_csv(result.body, expected_external_id=spec.external_id)
            if not parsed.observations:
                raise ValueError("no valid observations")
            quality = evaluate_observations(
                spec,
                parsed.observations,
                as_of=date.today().isoformat(),
            )
            if not quality.passed:
                details = "; ".join(f"{item.code}: {item.message}" for item in quality.issues)
                raise ValueError(f"quality contract failed: {details}")
            print(
                f"PASS {spec.series_id}: HTTP {result.http_status}; "
                f"rows={len(parsed.observations)}; last={parsed.observations[-1].reference_date}"
            )
        except Exception as exc:
            failures.append(f"{spec.series_id}: {exc}")
            print(f"FAIL {spec.series_id}: {exc}", file=sys.stderr)

    if failures:
        print(f"SOURCE CONTRACT: FAIL ({len(failures)}/5 series)", file=sys.stderr)
        return 1
    print("SOURCE CONTRACT: PASS (5/5 series)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
