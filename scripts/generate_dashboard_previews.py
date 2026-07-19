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
            (x, y),
            w,
            h,
            transform=fig.transFigure,
            boxstyle="round,pad=0.008,rounding_size=0.012",
            facecolor="white",
            edgecolor="#DDE5EF",
            linewidth=1.2,
        )
    )
    fig.text(x + 0.018, y + h - 0.038, label, fontsize=11, color="#667085", weight="medium")
    fig.text(x + 0.018, y + 0.027, value, fontsize=24, color=NAVY, weight="bold")
    fig.patches.append(
        FancyBboxPatch(
            (x, y),
            0.006,
            h,
            transform=fig.transFigure,
            boxstyle="round,pad=0,rounding_size=0.004",
            facecolor=accent,
            edgecolor=accent,
        )
    )


def overview(metadata, current, logs, demo_current, demo_history):
    labels = dict(zip(metadata.series_id, metadata.name, strict=True))
    fig = plt.figure(figsize=(16, 9), dpi=120, facecolor="#F6F8FB")
    fig.text(0.045, 0.94, "Czech inflation predictors", fontsize=27, weight="bold", color=NAVY)
    fig.text(
        0.045,
        0.905,
        "ECB HICP components and EUR/CZK · raw published levels · vintage-aware storage",
        fontsize=12,
        color="#667085",
    )

    metrics = [
        ("SERIES", f"{len(metadata)}", BLUE),
        ("CURRENT OBSERVATIONS", f"{len(current):,}", TEAL),
        ("REVISIONS RETAINED", f"{len(demo_history) - len(demo_current)}", ORANGE),
        ("SUCCESSFUL RUNS", f"{(logs.status == 'success').sum()}", BLUE),
    ]
    for index, (label, value, color) in enumerate(metrics):
        _card(fig, 0.045 + index * 0.235, 0.76, 0.215, 0.115, label, value, color)

    ax = fig.add_axes([0.06, 0.29, 0.56, 0.39], facecolor="white")
    hicp = current[current.series_id.str.contains("_HICP_")]
    for color, (series_id, frame) in zip(PALETTE, hicp.groupby("series_id"), strict=False):
        frame = frame.sort_values("reference_date")
        ax.plot(
            frame.reference_date,
            frame.value,
            color=color,
            linewidth=2.4,
            label=labels[series_id].replace("Czechia - ", ""),
        )
    ax.set_title(
        "HICP components · index level (2025 = 100)",
        loc="left",
        fontsize=14,
        weight="bold",
        color=NAVY,
        pad=14,
    )
    ax.grid(axis="y", color="#E7ECF2", linewidth=0.8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(colors="#667085")
    ax.set_ylabel("Index", color="#667085")
    ax.xaxis.set_major_locator(mdates.YearLocator(base=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.legend(loc="upper left", frameon=False, ncol=2, fontsize=9)

    ax_table = fig.add_axes([0.66, 0.29, 0.30, 0.39], facecolor="white")
    ax_table.axis("off")
    ax_table.text(
        0,
        1.03,
        "Dataset coverage",
        fontsize=14,
        weight="bold",
        color=NAVY,
        transform=ax_table.transAxes,
    )
    rows = []
    for row in metadata.itertuples():
        short = row.name.replace("Czechia - ", "")
        rows.append([short, row.frequency, f"{row.observation_count}", str(row.last_observation)])
    table = ax_table.table(
        cellText=rows,
        colLabels=["Series", "Freq.", "Obs.", "Last"],
        colWidths=[0.45, 0.16, 0.14, 0.25],
        loc="upper left",
        cellLoc="left",
        colLoc="left",
        bbox=[0, 0.12, 1, 0.80],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#E7ECF2")
        cell.set_facecolor("#F8FAFC" if row == 0 else "white")
        if row == 0:
            cell.set_text_props(weight="bold", color="#475467")
    ax_table.text(
        0,
        0.02,
        "Native frequency · raw level · structured series_id",
        fontsize=9,
        color="#667085",
        transform=ax_table.transAxes,
    )

    fig.text(0.06, 0.20, "Why it matters", fontsize=13, weight="bold", color=NAVY)
    fig.text(
        0.06,
        0.155,
        "HICP components support bottom-up inflation analysis; EUR/CZK captures exchange-rate pass-through.\n"
        "Every revision remains queryable without an is_current flag or destructive overwrite.",
        fontsize=11,
        color="#475467",
        linespacing=1.5,
    )
    fig.text(
        0.94, 0.055, "Generated from evidence/kinea.db", ha="right", fontsize=9, color="#98A2B3"
    )
    fig.savefig(DOCS / "dashboard-overview.png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def as_of_preview(metadata, demo_current, demo_history, old):
    labels = dict(zip(metadata.series_id, metadata.name, strict=True))
    series_id, reference = "CZ_HICP_CORE_INDEX", pd.Timestamp("2026-06-01")
    detail = demo_history[
        (demo_history.series_id == series_id) & (demo_history.reference_date == reference)
    ].sort_values("vintage_date")
    current_series = demo_current[demo_current.series_id == series_id].sort_values("reference_date")
    old_series = old[old.series_id == series_id].sort_values("reference_date")

    fig = plt.figure(figsize=(16, 9), dpi=120, facecolor="#F6F8FB")
    fig.text(0.045, 0.94, "Vintages & historical snapshot", fontsize=27, weight="bold", color=NAVY)
    fig.text(
        0.045,
        0.905,
        "Reconstruct exactly what was known on a past date from the same time_series table",
        fontsize=12,
        color="#667085",
    )

    fig.patches.append(
        FancyBboxPatch(
            (0.045, 0.76),
            0.91,
            0.105,
            transform=fig.transFigure,
            boxstyle="round,pad=0.01,rounding_size=0.012",
            facecolor="#ECFDF3",
            edgecolor="#ABEFC6",
        )
    )
    first, last = detail.iloc[0], detail.iloc[-1]
    fig.text(
        0.065,
        0.825,
        f"{labels[series_id]} · June 2026",
        fontsize=13,
        weight="bold",
        color="#067647",
    )
    fig.text(
        0.065,
        0.785,
        f"Revision retained: {first.value:.2f}  →  {last.value:.2f}   "
        f"(change {last.value - first.value:+.2f}; vintages {first.vintage_date.date()} and {last.vintage_date.date()})",
        fontsize=12,
        color="#067647",
    )

    ax_bar = fig.add_axes([0.07, 0.34, 0.33, 0.34], facecolor="white")
    bars = ax_bar.bar(
        ["1 Jul 2026", "18 Jul 2026"], detail.value, color=["#98A2B3", BLUE], width=0.55
    )
    ax_bar.set_title(
        "Two versions coexist", loc="left", fontsize=14, weight="bold", color=NAVY, pad=14
    )
    ax_bar.set_ylabel("Index value", color="#667085")
    ax_bar.set_ylim(detail.value.min() - 0.25, detail.value.max() + 0.25)
    ax_bar.grid(axis="y", color="#E7ECF2")
    ax_bar.spines[["top", "right", "left"]].set_visible(False)
    ax_bar.bar_label(bars, fmt="%.2f", padding=5, color=NAVY, weight="bold")
    ax_bar.tick_params(colors="#667085")

    ax_line = fig.add_axes([0.47, 0.34, 0.48, 0.34], facecolor="white")
    ax_line.plot(
        current_series.reference_date,
        current_series.value,
        color=BLUE,
        linewidth=2.4,
        label="Current view",
    )
    ax_line.plot(
        old_series.reference_date,
        old_series.value,
        color=ORANGE,
        linewidth=2,
        linestyle="--",
        label="As-of 10 Jul 2026",
    )
    ax_line.scatter([reference], [first.value], color=ORANGE, s=55, zorder=5)
    ax_line.scatter([reference], [last.value], color=BLUE, s=55, zorder=5)
    ax_line.set_title(
        "As-of view selects the correct vintage",
        loc="left",
        fontsize=14,
        weight="bold",
        color=NAVY,
        pad=14,
    )
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
    fig.text(
        0.94,
        0.055,
        "Generated from evidence/revision_demo.db",
        ha="right",
        fontsize=9,
        color="#98A2B3",
    )
    fig.savefig(DOCS / "dashboard-as-of.png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def hicp_preview(metadata, current):
    labels = dict(zip(metadata.series_id, metadata.name, strict=True))
    hicp = current[current.series_id.str.contains("_HICP_")]
    fig = plt.figure(figsize=(16, 9), dpi=120, facecolor="#F6F8FB")
    fig.text(0.045, 0.94, "HICP components", fontsize=27, weight="bold", color=NAVY)
    fig.text(
        0.045,
        0.905,
        "Monthly Czech price-index levels · ECB Data Portal · native values",
        fontsize=12,
        color="#667085",
    )
    ax = fig.add_axes([0.06, 0.29, 0.61, 0.50], facecolor="white")
    latest_rows = []
    for color, (series_id, frame) in zip(PALETTE, hicp.groupby("series_id"), strict=False):
        frame = frame.sort_values("reference_date")
        short = labels[series_id].replace("Czechia - ", "")
        ax.plot(frame.reference_date, frame.value, color=color, linewidth=2.4, label=short)
        latest_rows.append((short, frame.iloc[-1]))
    ax.set_title(
        "Full available history", loc="left", fontsize=14, weight="bold", color=NAVY, pad=14
    )
    ax.set_ylabel("Index (2025 = 100)", color="#667085")
    ax.grid(axis="y", color="#E7ECF2")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(colors="#667085")
    ax.xaxis.set_major_locator(mdates.YearLocator(base=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.legend(frameon=False, loc="upper left", ncol=2)

    table_ax = fig.add_axes([0.71, 0.29, 0.25, 0.50], facecolor="white")
    table_ax.axis("off")
    table_ax.text(
        0,
        1.02,
        "Latest observations",
        fontsize=14,
        weight="bold",
        color=NAVY,
        transform=table_ax.transAxes,
    )
    rows = [
        [name, row.reference_date.date(), f"{row.value:.2f}", row.vintage_date.date()]
        for name, row in latest_rows
    ]
    table = table_ax.table(
        cellText=rows,
        colLabels=["Component", "Period", "Index", "Vintage"],
        colWidths=[0.36, 0.24, 0.16, 0.24],
        cellLoc="left",
        colLoc="left",
        loc="upper left",
        bbox=[0, 0.42, 1, 0.48],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#E7ECF2")
        cell.set_facecolor("#F8FAFC" if row == 0 else "white")
        if row == 0:
            cell.set_text_props(weight="bold", color="#475467")
    table_ax.text(0, 0.30, "Frequency", fontsize=10, color="#667085")
    table_ax.text(0, 0.23, "Monthly", fontsize=18, weight="bold", color=NAVY)
    table_ax.text(0.52, 0.30, "Series", fontsize=10, color="#667085")
    table_ax.text(0.52, 0.23, "4", fontsize=18, weight="bold", color=NAVY)
    table_ax.text(
        0,
        0.08,
        "Period selection and component filters are available in Streamlit.",
        fontsize=9,
        color="#667085",
        wrap=True,
    )
    fig.text(
        0.94, 0.055, "HICP section · evidence/kinea.db", ha="right", fontsize=9, color="#98A2B3"
    )
    fig.savefig(DOCS / "dashboard-hicp.png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def fx_preview(metadata, current):
    fx = current[current.series_id.str.contains("_FX_")].sort_values("reference_date")
    meta = metadata[metadata.series_id.str.contains("_FX_")].iloc[0]
    fig = plt.figure(figsize=(16, 9), dpi=120, facecolor="#F6F8FB")
    fig.text(0.045, 0.94, "EUR/CZK exchange rate", fontsize=27, weight="bold", color=NAVY)
    fig.text(
        0.045,
        0.905,
        "Czech koruna per euro · daily ECB reference rate · raw published level",
        fontsize=12,
        color="#667085",
    )
    _card(fig, 0.045, 0.76, 0.205, 0.105, "FREQUENCY", meta.frequency, BLUE)
    _card(fig, 0.275, 0.76, 0.205, 0.105, "UNIT", meta.unit, TEAL)
    _card(fig, 0.505, 0.76, 0.205, 0.105, "OBSERVATIONS", f"{len(fx):,}", ORANGE)
    _card(fig, 0.735, 0.76, 0.205, 0.105, "LATEST", f"{fx.iloc[-1].value:.3f}", BLUE)
    ax = fig.add_axes([0.06, 0.22, 0.90, 0.45], facecolor="white")
    ax.plot(fx.reference_date, fx.value, color=BLUE, linewidth=1.7)
    ax.fill_between(fx.reference_date, fx.value, fx.value.min() - 0.5, color=BLUE, alpha=0.07)
    ax.set_title(
        "Full available history", loc="left", fontsize=14, weight="bold", color=NAVY, pad=14
    )
    ax.set_ylabel("CZK per EUR", color="#667085")
    ax.grid(axis="y", color="#E7ECF2")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(colors="#667085")
    ax.xaxis.set_major_locator(mdates.YearLocator(base=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.text(0.06, 0.14, "Interpretation", fontsize=11, color="#667085")
    fig.text(
        0.06,
        0.095,
        "A higher value means a weaker koruna against the euro.",
        fontsize=13,
        weight="bold",
        color=NAVY,
    )
    fig.text(
        0.94, 0.055, "EUR/CZK section · evidence/kinea.db", ha="right", fontsize=9, color="#98A2B3"
    )
    fig.savefig(DOCS / "dashboard-fx.png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def vintages_preview(metadata, demo_history):
    labels = dict(zip(metadata.series_id, metadata.name, strict=True))
    grouped = demo_history.groupby(["series_id", "reference_date"]).size()
    series_id, reference = grouped[grouped > 1].index[0]
    detail = demo_history[
        (demo_history.series_id == series_id) & (demo_history.reference_date == reference)
    ].sort_values("vintage_date")
    first, last = detail.iloc[0], detail.iloc[-1]
    fig = plt.figure(figsize=(16, 9), dpi=120, facecolor="#F6F8FB")
    fig.text(0.045, 0.94, "Vintage history", fontsize=27, weight="bold", color=NAVY)
    fig.text(
        0.045,
        0.905,
        "Old and current values coexist; a later revision never destroys prior knowledge",
        fontsize=12,
        color="#667085",
    )
    fig.patches.append(
        FancyBboxPatch(
            (0.045, 0.76),
            0.91,
            0.105,
            transform=fig.transFigure,
            boxstyle="round,pad=0.01,rounding_size=0.012",
            facecolor="#FFFAEB",
            edgecolor="#FEDF89",
        )
    )
    fig.text(
        0.065, 0.825, "Labelled simulated revision", fontsize=13, weight="bold", color="#B54708"
    )
    fig.text(
        0.065,
        0.785,
        "Stored in evidence/revision_demo.db; official ECB values in evidence/kinea.db are untouched.",
        fontsize=11,
        color="#B54708",
    )
    ax = fig.add_axes([0.07, 0.27, 0.36, 0.38], facecolor="white")
    bars = ax.bar(
        [str(value.date()) for value in detail.vintage_date],
        detail.value,
        color=["#98A2B3", BLUE],
        width=0.52,
    )
    ax.set_ylim(detail.value.min() - 0.25, detail.value.max() + 0.25)
    ax.set_title(
        "Both vintages retained", loc="left", fontsize=14, weight="bold", color=NAVY, pad=14
    )
    ax.set_ylabel("Index", color="#667085")
    ax.grid(axis="y", color="#E7ECF2")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.bar_label(bars, fmt="%.2f", padding=5, weight="bold", color=NAVY)
    ax.tick_params(colors="#667085")
    ax_table = fig.add_axes([0.50, 0.27, 0.45, 0.38], facecolor="white")
    ax_table.axis("off")
    ax_table.text(
        0,
        1.02,
        f"{labels[series_id]} · {reference.date()}",
        fontsize=14,
        weight="bold",
        color=NAVY,
        transform=ax_table.transAxes,
    )
    rows = [
        ["Old", first.vintage_date.date(), f"{first.value:.2f}", "+0.00"],
        [
            "Current",
            last.vintage_date.date(),
            f"{last.value:.2f}",
            f"{last.value - first.value:+.2f}",
        ],
    ]
    table = ax_table.table(
        cellText=rows,
        colLabels=["Version", "Vintage date", "Value", "Difference"],
        cellLoc="left",
        colLoc="left",
        loc="upper left",
        bbox=[0, 0.45, 1, 0.40],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#E7ECF2")
        cell.set_facecolor("#F8FAFC" if row == 0 else "white")
        if row == 0:
            cell.set_text_props(weight="bold", color="#475467")
    ax_table.text(0, 0.27, "Storage decision", fontsize=10, color="#667085")
    ax_table.text(
        0, 0.18, "Append a new vintage dated 18 July 2026", fontsize=16, weight="bold", color=NAVY
    )
    ax_table.text(
        0,
        0.06,
        "Composite key: (series_id, reference_date, vintage_date)",
        fontsize=10,
        color="#667085",
    )
    fig.text(
        0.94,
        0.055,
        "Vintages section · evidence/revision_demo.db",
        ha="right",
        fontsize=9,
        color="#98A2B3",
    )
    fig.savefig(DOCS / "dashboard-vintages.png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def audit_preview(metadata, current, logs):
    fig = plt.figure(figsize=(16, 9), dpi=120, facecolor="#F6F8FB")
    fig.text(0.045, 0.94, "Collection audit", fontsize=27, weight="bold", color=NAVY)
    fig.text(
        0.045,
        0.905,
        "One complete log per execution · success and error paths preserved",
        fontsize=12,
        color="#667085",
    )
    _card(fig, 0.045, 0.76, 0.205, 0.105, "METADATA ROWS", f"{len(metadata):,}", BLUE)
    _card(fig, 0.275, 0.76, 0.205, 0.105, "TIME SERIES ROWS", f"{len(current):,}", TEAL)
    _card(
        fig, 0.505, 0.76, 0.205, 0.105, "SUCCESS LOGS", f"{(logs.status == 'success').sum()}", BLUE
    )
    _card(fig, 0.735, 0.76, 0.205, 0.105, "ERROR LOGS", f"{(logs.status == 'error').sum()}", RED)
    ax = fig.add_axes([0.06, 0.31, 0.90, 0.35], facecolor="white")
    ax.axis("off")
    ax.text(
        0, 1.05, "Execution log", fontsize=14, weight="bold", color=NAVY, transform=ax.transAxes
    )
    rows = []
    for row in logs.sort_values("id", ascending=False).head(6).itertuples():
        summary = str(row.log_text)
        if len(summary) > 68:
            summary = summary[:65] + "..."
        rows.append([row.id, row.status.upper(), row.started_at, row.finished_at, summary])
    table = ax.table(
        cellText=rows,
        colLabels=["ID", "Status", "Started", "Finished", "Summary"],
        colWidths=[0.05, 0.09, 0.19, 0.19, 0.48],
        cellLoc="left",
        colLoc="left",
        loc="upper left",
        bbox=[0, 0.05, 1, 0.82],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8.8)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#E7ECF2")
        cell.set_facecolor("#F8FAFC" if row == 0 else "white")
        if row == 0:
            cell.set_text_props(weight="bold", color="#475467")
        elif col == 1:
            cell.set_text_props(
                weight="bold", color=TEAL if cell.get_text().get_text() == "SUCCESS" else RED
            )
    fig.text(0.06, 0.22, "Reproduce validation", fontsize=11, color="#667085")
    fig.text(
        0.06,
        0.17,
        "python scripts/validate_delivery.py",
        fontsize=16,
        family="monospace",
        weight="bold",
        color=NAVY,
    )
    fig.text(
        0.06,
        0.11,
        "Expected result: all checks PASS, followed by READY",
        fontsize=11,
        color="#475467",
    )
    fig.text(
        0.94, 0.055, "Audit section · evidence/kinea.db", ha="right", fontsize=9, color="#98A2B3"
    )
    fig.savefig(DOCS / "dashboard-audit.png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    metadata, current, logs, demo_current, demo_history, old = _data()
    overview(metadata, current, logs, demo_current, demo_history)
    hicp_preview(metadata, current)
    fx_preview(metadata, current)
    vintages_preview(metadata, demo_history)
    as_of_preview(metadata, demo_current, demo_history, old)
    audit_preview(metadata, current, logs)
    print("Generated six dashboard section previews in docs/")


if __name__ == "__main__":
    main()
