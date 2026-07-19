"""Streamlit presentation layer for the assignment's exact three-table schema."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kinea.db import AS_OF_QUERY, CURRENT_QUERY  # noqa: E402


DEFAULT_DB = ROOT / "evidence" / "kinea.db"
DEFAULT_REVISION_DB = ROOT / "evidence" / "revision_demo.db"
BLUE = "#155EEF"
NAVY = "#102A43"
TEAL = "#0E9384"
ORANGE = "#F79009"
RED = "#D92D20"
PALETTE = [BLUE, TEAL, ORANGE, RED]


def _db_path() -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args, _ = parser.parse_known_args()
    return Path(args.db)


@st.cache_data(show_spinner=False)
def load_data(path: str, mtime: float):
    del mtime
    conn = sqlite3.connect(path)
    metadata = pd.read_sql_query("SELECT * FROM metadata ORDER BY series_id", conn)
    current = pd.read_sql_query(CURRENT_QUERY, conn)
    history = pd.read_sql_query(
        "SELECT * FROM time_series ORDER BY series_id, reference_date, vintage_date", conn
    )
    logs = pd.read_sql_query("SELECT * FROM logs ORDER BY id DESC", conn)
    conn.close()
    for frame in (current, history):
        if not frame.empty:
            frame["reference_date"] = pd.to_datetime(frame["reference_date"])
            frame["vintage_date"] = pd.to_datetime(frame["vintage_date"])
    return metadata, current, history, logs


@st.cache_data(show_spinner=False)
def load_as_of(path: str, mtime: float, as_of: str) -> pd.DataFrame:
    del mtime
    conn = sqlite3.connect(path)
    frame = pd.read_sql_query(AS_OF_QUERY, conn, params={"as_of": as_of})
    conn.close()
    if not frame.empty:
        frame["reference_date"] = pd.to_datetime(frame["reference_date"])
        frame["vintage_date"] = pd.to_datetime(frame["vintage_date"])
    return frame


def _series_chart(frame: pd.DataFrame, labels: dict[str, str], y_title: str):
    chart_data = frame.copy()
    chart_data["series"] = chart_data["series_id"].map(labels).fillna(chart_data["series_id"])
    return (
        alt.Chart(chart_data)
        .mark_line(strokeWidth=2.4)
        .encode(
            x=alt.X("reference_date:T", title=None),
            y=alt.Y("value:Q", title=y_title, scale=alt.Scale(zero=False)),
            color=alt.Color(
                "series:N",
                title=None,
                scale=alt.Scale(range=PALETTE),
                legend=alt.Legend(orient="top", columns=2),
            ),
            tooltip=[
                alt.Tooltip("series:N", title="Series"),
                alt.Tooltip("reference_date:T", title="Reference date"),
                alt.Tooltip("value:Q", title="Value", format=".3f"),
                alt.Tooltip("vintage_date:T", title="Vintage"),
            ],
        )
        .properties(height=390)
        .interactive()
    )


def _period_filter(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    """Offer a compact period selector without changing the stored native data."""
    period = st.selectbox(
        "Display period",
        ["Full history", "Last 10 years", "Last 5 years", "Last 2 years"],
        key=key,
    )
    if frame.empty or period == "Full history":
        return frame
    years = {"Last 10 years": 10, "Last 5 years": 5, "Last 2 years": 2}[period]
    cutoff = frame["reference_date"].max() - pd.DateOffset(years=years)
    return frame[frame["reference_date"] >= cutoff]


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    """Serialize the visible data without adding a synthetic dataframe index."""

    return frame.to_csv(index=False).encode("utf-8")


def main() -> None:
    st.set_page_config(page_title="Czech inflation predictors", page_icon="📊", layout="wide")
    st.markdown(
        """
        <style>
        .block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1450px;}
        div[data-testid="stMetric"] {background:#F8FAFC; border:1px solid #E4E7EC;
            border-radius:12px; padding:16px 18px;}
        h1, h2, h3 {color:#102A43;}
        </style>
        """,
        unsafe_allow_html=True,
    )
    db_path = _db_path()
    if not db_path.exists():
        st.error(f"Database not found: {db_path}")
        st.code("python scripts/generate_evidence.py")
        st.stop()

    mtime = db_path.stat().st_mtime
    metadata, current, history, logs = load_data(str(db_path), mtime)
    labels = dict(zip(metadata["series_id"], metadata["name"]))

    revision_path = db_path
    revision_mtime = mtime
    revision_metadata = metadata
    revision_current = current
    revision_history = history
    demo_used = False
    revised_in_main = history.groupby(["series_id", "reference_date"]).size().gt(1).any()
    if not revised_in_main and DEFAULT_REVISION_DB.exists():
        revision_path = DEFAULT_REVISION_DB
        revision_mtime = revision_path.stat().st_mtime
        revision_metadata, revision_current, revision_history, _ = load_data(
            str(revision_path), revision_mtime
        )
        labels.update(dict(zip(revision_metadata["series_id"], revision_metadata["name"])))
        demo_used = True

    st.title("Czech inflation predictors")
    st.caption(
        "ECB HICP components and EUR/CZK · raw published levels · versioned by the day each value was observed"
    )

    revisions = max(len(revision_history) - len(revision_current), 0)
    latest_reference = current["reference_date"].max() if not current.empty else None
    successful_runs = int((logs["status"] == "success").sum()) if not logs.empty else 0
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Series", f"{len(metadata)}")
    k2.metric("Current observations", f"{len(current):,}")
    k3.metric("Revisions retained", f"{revisions:,}")
    k4.metric("Successful runs", f"{successful_runs}")

    overview, hicp_tab, fx_tab, vintage_tab, as_of_tab, audit_tab = st.tabs(
        ["Overview", "HICP components", "EUR/CZK", "Vintages", "As-of", "Audit"]
    )

    with overview:
        left, right = st.columns([3, 2])
        with left:
            st.subheader("What is in the dataset")
            st.markdown(
                "The four HICP components support bottom-up inflation analysis; EUR/CZK captures "
                "exchange-rate pass-through into tradable goods and energy. Values stay in their "
                "native frequency and raw form. Transformations belong in the analysis layer."
            )
            coverage = metadata[
                ["name", "frequency", "unit", "first_observation", "last_observation", "observation_count"]
            ].rename(
                columns={
                    "name": "Series",
                    "frequency": "Frequency",
                    "unit": "Unit",
                    "first_observation": "First",
                    "last_observation": "Last",
                    "observation_count": "Observations",
                }
            )
            st.dataframe(coverage, hide_index=True, width="stretch")
            sources = metadata[["name", "source_url"]].rename(
                columns={"name": "Series", "source_url": "Official ECB endpoint"}
            )
            with st.expander("Official sources"):
                st.dataframe(sources, hide_index=True, width="stretch")
            d1, d2 = st.columns(2)
            d1.download_button(
                "Download metadata CSV",
                _csv_bytes(metadata),
                "kinea-metadata.csv",
                "text/csv",
                key="download_metadata",
            )
            d2.download_button(
                "Download current observations CSV",
                _csv_bytes(current),
                "kinea-current-observations.csv",
                "text/csv",
                key="download_current",
            )
        with right:
            st.subheader("Data contract")
            st.markdown(
                """
                - `reference_date`: period described by the number
                - `vintage_date`: day this version was observed
                - unchanged values create no row
                - later revisions append a new vintage
                - same-day corrections update that day's row
                """
            )
            if latest_reference is not None:
                st.info(f"Latest reference date in this database: **{latest_reference.date()}**")
            if not logs.empty:
                last_log = logs.iloc[0]
                st.metric("Last collection status", str(last_log["status"]).upper())
                st.caption(f"Finished at {last_log['finished_at']}")

    with hicp_tab:
        hicp_ids = [sid for sid in metadata["series_id"] if "_HICP_" in sid]
        selected = st.multiselect(
            "Components",
            hicp_ids,
            default=hicp_ids,
            format_func=lambda value: labels[value],
        )
        hicp = current[current["series_id"].isin(selected)]
        hicp = _period_filter(hicp, "hicp_period")
        if hicp.empty:
            st.info("Select at least one component.")
        else:
            st.altair_chart(
                _series_chart(hicp, labels, "HICP index (2025 = 100)"),
                width="stretch",
            )
            latest = hicp.sort_values("reference_date").groupby("series_id").tail(1).copy()
            latest["Series"] = latest["series_id"].map(labels)
            st.dataframe(
                latest[["Series", "reference_date", "value", "vintage_date"]]
                .rename(columns={"reference_date": "Reference date", "value": "Index", "vintage_date": "Vintage"}),
                hide_index=True,
                width="stretch",
            )
            st.download_button(
                "Download displayed HICP CSV",
                _csv_bytes(hicp),
                "kinea-hicp-selection.csv",
                "text/csv",
                key="download_hicp",
            )

    with fx_tab:
        fx = current[current["series_id"].str.contains("_FX_", na=False)]
        st.markdown(
            "**Interpretation:** the number of Czech koruna per euro. A higher value means a weaker koruna."
        )
        if not fx.empty:
            fx = _period_filter(fx, "fx_period")
            st.altair_chart(
                _series_chart(fx, labels, "CZK per EUR"), width="stretch"
            )
            fx_meta = metadata[metadata["series_id"].str.contains("_FX_", na=False)].iloc[0]
            f1, f2, f3 = st.columns(3)
            f1.metric("Frequency", fx_meta["frequency"])
            f2.metric("Unit", fx_meta["unit"])
            f3.metric("Displayed observations", f"{len(fx):,}")
            st.download_button(
                "Download displayed EUR/CZK CSV",
                _csv_bytes(fx),
                "kinea-eurczk-selection.csv",
                "text/csv",
                key="download_fx",
            )

    with vintage_tab:
        st.subheader("Inspect revision history")
        if demo_used:
            st.info(
                "This tab uses `evidence/revision_demo.db`, a labelled simulated revision. "
                "The official values in `evidence/kinea.db` remain untouched."
            )
        grouped = (
            revision_history.groupby(["series_id", "reference_date"], as_index=False)
            .size()
            .rename(columns={"size": "versions"})
        )
        revised = grouped[grouped["versions"] > 1]
        if revised.empty:
            st.info("This database has no multi-vintage observation yet.")
        else:
            c1, c2 = st.columns(2)
            revised_series = sorted(revised["series_id"].unique())
            chosen_series = c1.selectbox(
                "Revised series", revised_series, format_func=lambda value: labels[value]
            )
            revised_dates = revised[revised["series_id"] == chosen_series]["reference_date"]
            chosen_reference = c2.selectbox(
                "Reference date", sorted(revised_dates.dt.date.unique())
            )
            detail = revision_history[
                (revision_history["series_id"] == chosen_series)
                & (revision_history["reference_date"].dt.date == chosen_reference)
            ].sort_values("vintage_date")
            first, latest = detail.iloc[0], detail.iloc[-1]
            st.success(
                f"{labels[chosen_series]} · {chosen_reference}: "
                f"{first['value']:.2f} → {latest['value']:.2f} "
                f"(change {latest['value'] - first['value']:+.2f})"
            )
            st.dataframe(
                detail[["reference_date", "value", "vintage_date", "collected_at"]]
                .rename(columns={"reference_date": "Reference date", "value": "Value",
                                 "vintage_date": "Vintage date", "collected_at": "Collected at"}),
                hide_index=True,
                width="stretch",
            )
            st.download_button(
                "Download vintage history CSV",
                _csv_bytes(detail),
                "kinea-vintage-history.csv",
                "text/csv",
                key="download_vintages",
            )

            st.subheader("Old versus current")
            comparison = detail[["vintage_date", "value"]].copy()
            comparison["Version"] = ["Old"] + ["Current"] * (len(comparison) - 1)
            comparison["Difference from first"] = comparison["value"] - first["value"]
            st.dataframe(
                comparison[["Version", "vintage_date", "value", "Difference from first"]]
                .rename(columns={"vintage_date": "Vintage date", "value": "Value"}),
                hide_index=True,
                width="stretch",
            )

    with as_of_tab:
        st.subheader("Historical snapshot (as-of)")
        st.markdown(
            "Select a series and a knowledge date. The query excludes every vintage observed "
            "after that date, then ranks the remaining versions per reference date."
        )
        vintage_dates = revision_history["vintage_date"].dt.date
        min_vintage, max_vintage = vintage_dates.min(), vintage_dates.max()
        a1, a2 = st.columns([1, 2])
        as_of = a1.date_input(
            "What did we know on?",
            value=max_vintage,
            min_value=min_vintage,
            max_value=max_vintage,
        )
        as_of_series = a2.selectbox(
            "Series for snapshot", revision_metadata["series_id"].tolist(),
            format_func=lambda value: labels[value], key="as_of_series"
        )
        snapshot = load_as_of(
            str(revision_path), revision_mtime, date.isoformat(as_of)
        )
        snapshot = snapshot[snapshot["series_id"] == as_of_series]
        if snapshot.empty:
            st.warning("No value had been observed for this series by the selected date.")
        else:
            st.altair_chart(
                _series_chart(snapshot, labels, "Value known on selected date"),
                width="stretch",
            )
            st.caption(
                f"Snapshot contains {len(snapshot)} reference dates, using only vintages on or before {as_of}."
            )
            st.download_button(
                "Download as-of snapshot CSV",
                _csv_bytes(snapshot),
                f"kinea-as-of-{as_of}.csv",
                "text/csv",
                key="download_as_of",
            )
            current_selection = revision_current[
                revision_current["series_id"] == as_of_series
            ][["reference_date", "value", "vintage_date"]].rename(
                columns={"value": "current_value", "vintage_date": "current_vintage"}
            )
            comparison = snapshot[["reference_date", "value", "vintage_date"]].merge(
                current_selection, on="reference_date", how="left"
            )
            comparison["difference"] = comparison["current_value"] - comparison["value"]
            changed = comparison[comparison["difference"].abs() > 1e-12]
            st.subheader("Snapshot versus current")
            if changed.empty:
                st.info("No later revision changes this selected snapshot.")
            else:
                st.dataframe(
                    changed.rename(
                        columns={
                            "reference_date": "Reference date",
                            "value": "As-of value",
                            "vintage_date": "As-of vintage",
                            "current_value": "Current value",
                            "current_vintage": "Current vintage",
                            "difference": "Difference",
                        }
                    ),
                    hide_index=True,
                    width="stretch",
                )

    with audit_tab:
        st.subheader("One row per execution")
        st.markdown(
            "A success and an intentionally triggered error are included so reviewers can verify "
            "that logging also happens when collection fails."
        )
        display_logs = logs[["id", "started_at", "finished_at", "status", "log_text"]]
        st.dataframe(display_logs, hide_index=True, width="stretch")
        st.download_button(
            "Download execution logs CSV",
            _csv_bytes(logs),
            "kinea-execution-logs.csv",
            "text/csv",
            key="download_logs",
        )
        errors = logs[logs["status"] == "error"]
        if not errors.empty:
            with st.expander("Latest captured traceback"):
                st.code(errors.iloc[0]["traceback"] or "No traceback")
        st.subheader("Database audit")
        a1, a2, a3 = st.columns(3)
        a1.metric("metadata rows", f"{len(metadata):,}")
        a2.metric("time_series rows", f"{len(history):,}")
        a3.metric("log rows", f"{len(logs):,}")
        st.markdown("**Reproduce the delivery validation**")
        st.code(
            "python -m pytest -q\n"
            "python scripts/generate_evidence.py --mode live\n"
            "python scripts/validate_delivery.py\n"
            "streamlit run dashboard/app.py"
        )
        st.caption("Every source URL is preserved in metadata; every execution is recorded in logs.")


if __name__ == "__main__":
    main()
