from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

import pytest

from kinea.config import load_config
from kinea.parser import parse_sdmx_csv

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "fixtures" / "contracts"
REQUIRED_COLUMNS = {"KEY", "TIME_PERIOD", "OBS_VALUE"}


@pytest.mark.parametrize("spec", load_config().series, ids=lambda spec: spec.series_id)
def test_recorded_real_ecb_response_matches_parser_contract(spec):
    payload = (CONTRACTS / f"{spec.external_id}.csv").read_text(encoding="utf-8")
    columns = set(csv.DictReader(StringIO(payload)).fieldnames or ())

    assert REQUIRED_COLUMNS <= columns
    result = parse_sdmx_csv(payload, expected_external_id=spec.external_id)
    assert len(result.observations) == 3
    assert not result.warnings
    assert all(observation.value > 0 for observation in result.observations)
