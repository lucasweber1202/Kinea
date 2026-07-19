from __future__ import annotations

import gzip
import json

from kinea.archive import archive_response, verify_archive
from kinea.client import FetchResult
from kinea.config import load_config


def test_raw_payload_archive_is_hashed_compressed_and_verifiable(tmp_path):
    spec = load_config().series[0]
    body = f"KEY,TIME_PERIOD,OBS_VALUE\n{spec.external_id},2026-07-17,24.2\n"
    manifest = archive_response(
        tmp_path,
        spec,
        FetchResult(body, "https://example.test", 200, "2026-07-19T10:00:00+00:00"),
        run_id="run-123",
    )

    manifest_path = tmp_path / "run-123-EXR.D.CZK.EUR.SP00.A.json"
    stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    with gzip.open(tmp_path / stored["payload_path"], "rt", encoding="utf-8") as handle:
        assert handle.read() == body
    assert manifest.series_id == "CZ_FX_EURCZK"
    assert verify_archive(manifest_path)


def test_archive_verification_detects_tampering(tmp_path):
    spec = load_config().series[0]
    result = FetchResult("original", "url", 200, "2026-07-19T10:00:00+00:00")
    manifest = archive_response(tmp_path, spec, result, run_id="run")
    with gzip.open(tmp_path / manifest.payload_path, "wt", encoding="utf-8") as handle:
        handle.write("tampered")

    assert not verify_archive(tmp_path / "run-EXR.D.CZK.EUR.SP00.A.json")
