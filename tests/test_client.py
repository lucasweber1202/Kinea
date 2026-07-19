import io
import urllib.error

import pytest

from kinea.client import FetchError, LiveClient
from kinea.config import load_config


class Response:
    status = 200

    def __init__(self, body=b"KEY,TIME_PERIOD,OBS_VALUE\nX,2026-01,1.0\n"):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


def test_live_client_uses_explicit_timeout(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = load_config()
    client = LiveClient(config, timeout=7.5)
    client.fetch(config.series[0])
    assert seen["timeout"] == 7.5


def test_transient_network_failure_is_retried(monkeypatch):
    calls = {"count": 0}

    def fake_urlopen(request, timeout):
        del request, timeout
        calls["count"] += 1
        if calls["count"] < 3:
            raise urllib.error.URLError("temporary")
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = load_config()
    result = LiveClient(config, attempts=3, backoff_seconds=0).fetch(config.series[0])
    assert result.http_status == 200
    assert calls["count"] == 3


def test_fatal_http_status_is_not_retried(monkeypatch):
    calls = {"count": 0}

    def fake_urlopen(request, timeout):
        del timeout
        calls["count"] += 1
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, io.BytesIO())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = load_config()
    with pytest.raises(FetchError):
        LiveClient(config, attempts=3, backoff_seconds=0).fetch(config.series[0])
    assert calls["count"] == 1


def test_exhausted_network_retries_propagate(monkeypatch):
    def fake_urlopen(request, timeout):
        del request, timeout
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = load_config()
    with pytest.raises(FetchError) as error:
        LiveClient(config, attempts=2, backoff_seconds=0).fetch(config.series[0])
    assert isinstance(error.value.__cause__, urllib.error.URLError)


def test_rate_limit_honors_retry_after(monkeypatch):
    calls = {"count": 0}
    delays = []

    def fake_urlopen(request, timeout):
        del timeout
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limited",
                {"Retry-After": "2"},
                io.BytesIO(),
            )
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", delays.append)
    config = load_config()
    result = LiveClient(config, attempts=2, backoff_seconds=0).fetch(config.series[0])

    assert result.attempt_count == 2
    assert delays == [2.0]
