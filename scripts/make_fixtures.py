#!/usr/bin/env python3
"""Generate reproducible SDMX-CSV fixtures for offline collection runs.

IMPORTANT — these fixtures are SYNTHETIC. This environment cannot reach the live ECB
API, so we generate plausible values in the *exact* ECB `format=csvdata` shape. Their
only job is to exercise the real pipeline end-to-end and to produce evidence of
idempotency and revision handling. Run the collector with `--mode live` for real data.

Two vintages are produced:
  fixtures/v1 : an initial "release".
  fixtures/v2 : a later "release" that (a) adds one new month / a few new FX days, and
                (b) REVISES two provisional monthly values. Collecting v1 then v2 is what
                creates real vintage rows in the store; collecting v2 twice proves
                idempotency.

The generator is deterministic (fixed base values, no randomness), so re-running it
reproduces byte-identical fixtures.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"

HICP_HEADER = ("KEY,FREQ,REF_AREA,ADJUSTMENT,HICP_ITEM,STS_INSTIT,HICP_SUFFIX,"
               "TIME_PERIOD,OBS_VALUE,OBS_STATUS,UNIT,TITLE")
EXR_HEADER = ("KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,"
              "TIME_PERIOD,OBS_VALUE,OBS_STATUS,UNIT,TITLE")

# --- Monthly HICP series ---------------------------------------------------------------
# (item_code, base_2024_jan, monthly_drift, seasonal_amp, label)
HICP_SERIES = {
    "XEF000": (96.4, 0.28, 0.15, "HICP - Czechia - core (excl. energy, food, alcohol & tobacco), 2025=100"),
    "NRGY00": (101.5, 0.10, 1.40, "HICP - Czechia - energy, 2025=100"),
    "FOOD00": (95.8, 0.34, 0.35, "HICP - Czechia - food incl. alcohol & tobacco, 2025=100"),
    "SERV00": (95.1, 0.40, 0.20, "HICP - Czechia - services, 2025=100"),
}


def month_iter(start: date, n: int):
    y, m = start.year, start.month
    for _ in range(n):
        yield date(y, m, 1)
        m += 1
        if m > 12:
            m = 1
            y += 1


def hicp_value(base: float, drift: float, amp: float, i: int) -> float:
    # smooth upward trend + mild seasonality; deterministic
    seasonal = amp * math.sin((i % 12) / 12.0 * 2 * math.pi)
    return round(base + drift * i + seasonal, 2)


def build_hicp(item: str, months: list[date], vintage: str) -> str:
    base, drift, amp, title = HICP_SERIES[item]
    key = f"HICP.M.CZ.N.{item}.4D0.INX"
    lines = [HICP_HEADER]
    n = len(months)
    for i, mdate in enumerate(months):
        val = hicp_value(base, drift, amp, i)
        # last month is provisional in v1; in v2 it becomes final and a revision is applied.
        is_last = i == n - 1
        status = "P" if is_last else "A"
        if vintage == "v2":
            # v2 revises the month that was last & provisional in v1 (index n-2 here since
            # v2 has one extra month appended): finalize it, and nudge the level.
            if i == n - 2 and item in ("XEF000", "NRGY00"):
                status = "A"
                val = round(val + (0.21 if item == "XEF000" else 0.95), 2)
        period = f"{mdate.year}-{mdate.month:02d}"
        lines.append(
            f"{key},M,CZ,N,{item},4D0,INX,{period},{val},{status},"
            f"Index (2025=100),{title}"
        )
    return "\n".join(lines) + "\n"


def business_days(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            yield d
        d += timedelta(days=1)


def fx_value(i: int) -> float:
    # EUR/CZK gently oscillating in a realistic 24.6–25.2 band; deterministic
    return round(24.90 + 0.25 * math.sin(i / 22.0) + 0.05 * math.sin(i / 6.0), 4)


def build_fx(days: list[date]) -> str:
    key = "EXR.D.CZK.EUR.SP00.A"
    title = "Czech koruna/Euro - ECB reference exchange rate"
    lines = [EXR_HEADER]
    for i, d in enumerate(days):
        val = fx_value(i)
        period = d.isoformat()
        lines.append(
            f"{key},D,CZK,EUR,SP00,A,{period},{val},A,CZK,{title}"
        )
    return "\n".join(lines) + "\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}  ({text.count(chr(10))} lines)")


def main() -> None:
    # v1: monthly 2024-01..2026-06 (30 months); FX 2025-01-02..2026-06-30
    months_v1 = list(month_iter(date(2024, 1, 1), 30))
    months_v2 = list(month_iter(date(2024, 1, 1), 31))  # + 2026-07
    fx_v1 = list(business_days(date(2025, 1, 2), date(2026, 6, 30)))
    fx_v2 = list(business_days(date(2025, 1, 2), date(2026, 7, 15)))  # + 11 new days

    print("Generating v1 fixtures ...")
    for item in HICP_SERIES:
        write(FIX / "v1" / f"HICP.M.CZ.N.{item}.4D0.INX.csv", build_hicp(item, months_v1, "v1"))
    write(FIX / "v1" / "EXR.D.CZK.EUR.SP00.A.csv", build_fx(fx_v1))

    print("Generating v2 fixtures (adds a month / days, revises 2 provisional values) ...")
    for item in HICP_SERIES:
        write(FIX / "v2" / f"HICP.M.CZ.N.{item}.4D0.INX.csv", build_hicp(item, months_v2, "v2"))
    write(FIX / "v2" / "EXR.D.CZK.EUR.SP00.A.csv", build_fx(fx_v2))

    print("Done.")


if __name__ == "__main__":
    main()
