#!/usr/bin/env python3
"""Capture real screenshots of the live Streamlit dashboard into docs/.

Unlike scripts/generate_dashboard_previews.py (a fast, dependency-light matplotlib
approximation), this drives the actual running app with a headless browser, so the captures
show every real control, tooltip, and chart exactly as a reviewer would see them. This is the
genuine reproduction path for docs/dashboard-*.png.

Requires the optional "screenshots" extra (Playwright) and a one-time browser download:

    python -m pip install -e ".[dashboard,screenshots]"
    python -m playwright install --with-deps chromium

Usage:
    python -m streamlit run dashboard/app.py -- --db evidence/kinea.db \\
        --server.headless true --server.port 8501 &
    python scripts/capture_dashboard_screenshots.py
"""

from __future__ import annotations

import argparse
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TABS = [
    ("Overview", "dashboard-overview.png"),
    ("HICP components", "dashboard-hicp.png"),
    ("EUR/CZK", "dashboard-fx.png"),
    ("Vintages", "dashboard-vintages.png"),
    ("As-of", "dashboard-as-of.png"),
    ("Audit", "dashboard-audit.png"),
]

BASE_WIDTH = 1680
BASE_HEIGHT = 1200


def _wait_for_server(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    raise RuntimeError(f"Streamlit did not respond at {url} within {timeout:.0f}s")


def _content_height(page) -> int:
    # stAppViewContainer is clamped to the viewport height by Streamlit's layout; the actual
    # scrollable content lives in stMainBlockContainer, which grows with the real page content.
    return int(
        page.evaluate(
            "document.querySelector('[data-testid=\"stMainBlockContainer\"]').scrollHeight"
        )
    )


def _trim_trailing_whitespace(path: Path, min_height: int) -> None:
    from PIL import Image, ImageChops

    img = Image.open(path).convert("RGB")
    white = Image.new("RGB", img.size, (255, 255, 255))
    bbox = ImageChops.difference(img, white).getbbox()
    if not bbox:
        return
    bottom = max(bbox[3] + 24, min_height)
    if bottom < img.height:
        img.crop((0, 0, img.width, bottom)).save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--output-dir", default=str(ROOT / "docs"))
    parser.add_argument("--startup-timeout", type=float, default=40.0)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    _wait_for_server(args.url, args.startup_timeout)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": BASE_WIDTH, "height": BASE_HEIGHT})
        console_errors: list[str] = []
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )

        page.goto(args.url, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("text=Czech inflation predictors", timeout=30000)
        time.sleep(2)

        for label, filename in TABS:
            # Reset to the base viewport before every tab so one tab's measured height can
            # never leak into and inflate the next tab's measurement.
            page.set_viewport_size({"width": BASE_WIDTH, "height": BASE_HEIGHT})
            page.get_by_role("tab", name=label, exact=True).click()
            time.sleep(3.5)  # let Altair/Vega charts finish rendering

            height = _content_height(page)
            page.set_viewport_size({"width": BASE_WIDTH, "height": height + 80})
            time.sleep(1.2)

            path = output_dir / filename
            page.screenshot(path=str(path))
            _trim_trailing_whitespace(path, min_height=BASE_HEIGHT)
            print(f"captured {label} -> {path}")

        if console_errors:
            raise RuntimeError(f"Browser console errors during capture: {console_errors}")
        browser.close()

    print(f"Captured six dashboard screenshots into {output_dir}")


if __name__ == "__main__":
    main()
