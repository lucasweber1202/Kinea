"""HTTP and fixture clients for the ECB SDMX CSV API."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, SeriesSpec


class FetchError(RuntimeError):
    """A grave extraction failure that must abort and be logged."""


@dataclass(frozen=True)
class FetchResult:
    body: str
    source_url: str
    http_status: int
    fetched_at: str


class LiveClient:
    mode = "live"

    def __init__(
        self,
        config: Config,
        *,
        timeout: float = 30.0,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
    ) -> None:
        self.config = config
        self.timeout = timeout
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds

    def fetch(self, spec: SeriesSpec, params: dict[str, str] | None = None) -> FetchResult:
        url = spec.request_url(self.config.base_url, params)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/csv",
                "User-Agent": "kinea-ecb-collector/2.0 (+technical-assignment)",
            },
        )
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8-sig")
                    status = int(getattr(response, "status", 200))
                return FetchResult(
                    body=body,
                    source_url=url,
                    http_status=status,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                )
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504}:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < self.attempts:
                time.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        raise FetchError(
            f"ECB request failed after {self.attempts} attempts: {url}"
        ) from last_error


class OfflineClient:
    mode = "offline"

    def __init__(self, fixtures_dir: str | Path) -> None:
        self.fixtures_dir = Path(fixtures_dir)

    def fetch(self, spec: SeriesSpec, params: dict[str, str] | None = None) -> FetchResult:
        del params
        path = self.fixtures_dir / f"{spec.external_id}.csv"
        if not path.exists():
            raise FetchError(f"fixture not found: {path}")
        return FetchResult(
            body=path.read_text(encoding="utf-8-sig"),
            source_url=spec.request_url("https://data-api.ecb.europa.eu/service/data"),
            http_status=200,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
