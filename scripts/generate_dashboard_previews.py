#!/usr/bin/env python3
"""Generate reviewable PNG previews from the same database used by Streamlit."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kinea.db import AS_OF_QUERY, CURRENT_QUERY  # noqa: E402

DB = ROOT / "evidence" / "kinea.db"
DOCS = ROOT / "docs"
BLUE, NAVY, TEAL, ORANGE, RED = "#155EEF", "#102A43", "#0E9384", "#F79009", "#D92D20"
PALETTE = [BLUE, TEAL, ORANGE, RED]


def _data():
    conn = sqlite3.connect(DB)
    metadata = pd.read_sql_query("SELECT * FROM metadata ORDER BY series_id", conn)
    current = pd.read_sql_query(CURRENT_QUERY, conn)
    logs = pd.read_sql_query("SELECT * FROM logs", conn)
    conn.close()
    demo = sqlite3.connect(ROOT / "evidence" / "revision_demo.db")
    demo_current = pd.read_sql_query(CURRENT_QUERY, demo)
    demo_history = pd.read_sql_query("SELECT * FROM time_series", demo)
    old = pd.read_sql_query(AS_OF_QUERY, demo, params={"as_of": "2026-07-10"})
    demo.close()
    for frame in (current, demo_current, demo_history, old):
        frame["reference_date"] = pd.to_datetime(frame["reference_date"])
        frame["vintage_date"] = pd.to_datetime(frame["vintage_date"])
    return metadata, current, logs, demo_current, demo_history, old


def _card(fig, x, y, w, h, label, value, accent=BLUE):
    fig.patches.append(
        FancyBboxPatch(
            (x, y), w, h, transform=fig.transFigure,
            boxstyle="round,pad=0.008,rounding_size=0.012",
            facecolor="white", edgecolor="#DDE5EF", linewidth=1.2,
        )
    )
    fig.text(x + 0.018, y + h - 0.038, label, fontsize=11, color="#667085", weight="medium")
    fig.text(x + 0.018, y + 0.027, value, fontsize=24, color=NAVY, weight="bold")
    fig.patches.append(
        FancyBboxPatch((x, y), 0.006, h, transform=fig.transFigure,
                       boxstyle="round,pad=0,rounding_size=0.004",
                       facecolor=accent, edgecolor=accent)
    )


def overview(metadata, current, logs, demo_current, demo_history):
    labels = dict(zip(metadata.series_id, metadata.name))
    fig = plt.figure(figsize=(16, 9), dpi=120, facecolor="#F6F8FB")
    fig.text(0.045, 0.94, "Czech inflation predictors", fontsize=27, weight="bold", color=NAVY)
    fig.text(0.045, 0.905,
             "ECB HICP components and EUR/CZK · raw published levels · vintage-aware storage",
             fontsize=12, color="#667085")

    metrics = [
        ("SERIES", f"{len(metadata)}", BLUE),
        ("CURRENT OBSERVATIONS", f"{len(current):,}", TEAL),
        ("REVISIONS RETAINED", f"{len(demo_history)-len(demo_current)}", ORANGE),
        ("SUCCESSFUL RUNS", f"{(logs.status == 'success').sum()}", BLUE),
    ]
    for index, (label, value, color) in enumerate(metrics):
        _card(fig, 0.045 + index * 0.235, 0.76, 0.215, 0.115, label, value, color)

    ax = fig.add_axes([0.06, 0.29, 0.56, 0.39], facecolor="white")
    hicp = current[current.series_id.str.contains("_HICP_")]
    for color, (series_id, frame) in zip(PALETTE, hicp.groupby("series_id")):
        frame = frame.sort_values("reference_date")
        ax.plot(frame.reference_date, frame.value, color=color, linewidth=2.4,
                label=labels[series_id].replace("Czechia - ", ""))
    ax.set_title("HICP components · index level (2025 = 100)", loc="left", fontsize=14,
                 weight="bold", color=NAVY, pad=14)
    ax.grid(axis="y", color="#E7ECF2", linewidth=0.8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(colors="#667085")
    ax.set_ylabel("Index", color="#667085")
    ax.xaxis.set_major_locator(mdates.YearLocator(base=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.legend(loc="upper left", frameon=False, ncol=2, fontsize=9)

    ax_table = fig.add_axes([0.66, 0.29, 0.30, 0.39], facecolor="white")
    ax_table.axis("off")
    ax_table.text(0, 1.03, "Dataset coverage", fontsize=14, weight="bold", color=NAVY,
                  transform=ax_table.transAxes)
    rows = []
    for row in metadata.itertuples():
        short = row.name.replace("Czechia - ", "")
        rows.append([short, row.frequency, f"{row.observation_count}", str(row.last_observation)])
    table = ax_table.table(
        cellText=rows,
        colLabels=["Series", "Freq.", "Obs.", "Last"],
        colWidths=[0.45, 0.16, 0.14, 0.25],
        loc="upper left", cellLoc="left", colLoc="left", bbox=[0, 0.12, 1, 0.80],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#E7ECF2")
        cell.set_facecolor("#F8FAFC" if row == 0 else "white")
        if row == 0:
            cell.set_text_props(weight="bold", color="#475467")
    ax_table.text(0, 0.02, "Native frequency · raw level · structured series_id",
                  fontsize=9, color="#667085", transform=ax_table.transAxes)

    fig.text(0.06, 0.20, "Why it matters", fontsize=13, weight="bold", color=NAVY)
    fig.text(0.06, 0.155,
             "HICP components support bottom-up inflation analysis; EUR/CZK captures exchange-rate pass-through.\n"
             "Every revision remains queryable without an is_current flag or destructive overwrite.",
             fontsize=11, color="#475467", linespacing=1.5)
    fig.text(0.94, 0.055, "Generated from evidence/kinea.db", ha="right", fontsize=9, color="#98A2B3")
    fig.savefig(DOCS / "dashboard-overview.png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def as_of_preview(metadata, demo_current, demo_history, old):
    labels = dict(zip(metadata.series_id, metadata.name))
    series_id, reference = "CZ_HICP_CORE_INDEX", pd.Timestamp("2026-06-01")
    detail = demo_history[(demo_history.series_id == series_id) & (demo_history.reference_date == reference)].sort_values("vintage_date")
    current_series = demo_current[demo_current.series_id == series_id].sort_values("reference_date")
    old_series = old[old.series_id == series_id].sort_values("reference_date")

    fig = plt.figure(figsize=(16, 9), dpi=120, facecolor="#F6F8FB")
    fig.text(0.045, 0.94, "Vintages & historical snapshot", fontsize=27, weight="bold", color=NAVY)
    fig.text(0.045, 0.905,
             "Reconstruct exactly what was known on a past date from the same time_series table",
             fontsize=12, color="#667085")

    fig.patches.append(FancyBboxPatch((0.045, 0.76), 0.91, 0.105, transform=fig.transFigure,
        boxstyle="round,pad=0.01,rounding_size=0.012", facecolor="#ECFDF3", edgecolor="#ABEFC6"))
    first, last = detail.iloc[0], detail.iloc[-1]
    fig.text(0.065, 0.825, f"{labels[series_id]} · June 2026", fontsize=13, weight="bold", color="#067647")
    fig.text(0.065, 0.785,
             f"Revision retained: {first.value:.2f}  →  {last.value:.2f}   "
             f"(change {last.value-first.value:+.2f}; vintages {first.vintage_date.date()} and {last.vintage_date.date()})",
             fontsize=12, color="#067647")

    ax_bar = fig.add_axes([0.07, 0.34, 0.33, 0.34], facecolor="white")
    bars = ax_bar.bar(["1 Jul 2026", "18 Jul 2026"], detail.value, color=["#98A2B3", BLUE], width=0.55)
    ax_bar.set_title("Two versions coexist", loc="left", fontsize=14, weight="bold", color=NAVY, pad=14)
    ax_bar.set_ylabel("Index value", color="#667085")
    ax_bar.set_ylim(detail.value.min() - 0.25, detail.value.max() + 0.25)
    ax_bar.grid(axis="y", color="#E7ECF2")
    ax_bar.spines[["top", "right", "left"]].set_visible(False)
    ax_bar.bar_label(bars, fmt="%.2f", padding=5, color=NAVY, weight="bold")
    ax_bar.tick_params(colors="#667085")

    ax_line = fig.add_axes([0.47, 0.34, 0.48, 0.34], facecolor="white")
    ax_line.plot(current_series.reference_date, current_series.value, color=BLUE, linewidth=2.4,
                 label="Current view")
    ax_line.plot(old_series.reference_date, old_series.value, color=ORANGE, linewidth=2,
                 linestyle="--", label="As-of 10 Jul 2026")
    ax_line.scatter([reference], [first.value], color=ORANGE, s=55, zorder=5)
    ax_line.scatter([reference], [last.value], color=BLUE, s=55, zorder=5)
    ax_line.set_title("As-of view selects the correct vintage", loc="left", fontsize=14,
                      weight="bold", color=NAVY, pad=14)
    ax_line.grid(axis="y", color="#E7ECF2")
    ax_line.spines[["top", "right", "left"]].set_visible(False)
    ax_line.tick_params(colors="#667085")
    ax_line.set_ylabel("Index", color="#667085")
    ax_line.xaxis.set_major_locator(mdates.YearLocator(base=3))
    ax_line.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax_line.legend(frameon=False, loc="upper left")

    fig.text(0.07, 0.25, "Selected as-of date", fontsize=10, color="#667085")
    fig.text(0.07, 0.21, "10 July 2026", fontsize=17, weight="bold", color=NAVY)
    fig.text(0.28, 0.25, "June value returned", fontsize=10, color="#667085")
    fig.text(0.28, 0.21, f"{first.value:.2f}", fontsize=17, weight="bold", color=ORANGE)
    fig.text(0.47, 0.25, "Current June value", fontsize=10, color="#667085")
    fig.text(0.47, 0.21, f"{last.value:.2f}", fontsize=17, weight="bold", color=BLUE)
    fig.text(0.66, 0.25, "Storage rule", fontsize=10, color="#667085")
    fig.text(0.66, 0.21, "Append later vintage", fontsize=17, weight="bold", color=NAVY)
    fig.text(0.94, 0.055, "Generated from evidence/kinea.db", ha="right", fontsize=9, color="#98A2B3")
    fig.savefig(DOCS / "dashboard-as-of.png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    metadata, current, logs, demo_current, demo_history, old = _data()
    overview(metadata, current, logs, demo_current, demo_history)
    as_of_preview(metadata, demo_current, demo_history, old)
    print("Generated docs/dashboard-overview.png and docs/dashboard-as-of.png")


if __name__ == "__main__":
    main()
