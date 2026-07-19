"""Optional raw-response archive with deterministic gzip files and SHA-256 manifests."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .client import FetchResult
from .config import SeriesSpec

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class PayloadManifest:
    run_id: str
    series_id: str
    external_id: str
    source_url: str
    fetched_at: str
    http_status: int
    response_bytes: int
    sha256: str
    payload_path: str


def archive_response(
    root: str | Path,
    spec: SeriesSpec,
    result: FetchResult,
    *,
    run_id: str,
) -> PayloadManifest:
    """Archive the exact response bytes and a traceable JSON manifest.

    Files live outside the relational store so the assignment's exact three-table schema remains
    unchanged.  The hash is over the uncompressed source payload decoded for the parser.
    """
    destination = Path(root).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    safe_key = _SAFE.sub("_", spec.external_id)
    stem = f"{run_id}-{safe_key}"
    payload = result.raw_body if result.raw_body is not None else result.body.encode("utf-8")
    payload_path = destination / f"{stem}.csv.gz"
    with payload_path.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as zipped:
            zipped.write(payload)
    manifest = PayloadManifest(
        run_id=run_id,
        series_id=spec.series_id,
        external_id=spec.external_id,
        source_url=result.source_url,
        fetched_at=result.fetched_at,
        http_status=result.http_status,
        response_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        payload_path=payload_path.name,
    )
    manifest_path = destination / f"{stem}.json"
    manifest_path.write_text(
        json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def verify_archive(manifest_path: str | Path) -> bool:
    """Verify a manifest against its compressed payload."""
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    with gzip.open(path.parent / manifest["payload_path"], "rb") as handle:
        payload = handle.read()
    return (
        len(payload) == int(manifest["response_bytes"])
        and hashlib.sha256(payload).hexdigest() == manifest["sha256"]
    )
