"""Streamlit presentation layer for the assignment's exact three-table schema.

Design goals: a professional, intuitive read of the dataset. The store holds raw published
levels versioned by vintage; every transformation (year-over-year, rebasing) is DERIVED
here in the view layer. The six tabs move from headline story -> component detail ->
FX -> the revision (vintage) mechanics -> point-in-time (as-of) -> audit.
"""

from __future__ import annotations

import argparse
import html
import sqlite3
import sys
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kinea import transforms  # noqa: E402
from kinea.db import AS_OF_QUERY, CURRENT_QUERY  # noqa: E402

DEFAULT_DB = ROOT / "evidence" / "kinea.db"
DEFAULT_REVISION_DB = ROOT / "evidence" / "revision_demo.db"

# Stable, semantic, colourblind-safe palette (validated with the dataviz palette checker).
COLORS = {
    "CZ_HICP_CORE_INDEX": "#2a78d6",  # blue
    "CZ_HICP_ENERGY_INDEX": "#eda100",  # amber  (energy)
    "CZ_HICP_FOOD_INDEX": "#008300",  # green  (food)
    "CZ_HICP_SERVICES_INDEX": "#4a3aa7",  # violet
    "CZ_FX_EURCZK": "#0e9384",  # teal
}
SHORT = {
    "CZ_HICP_CORE_INDEX": "Core",
    "CZ_HICP_ENERGY_INDEX": "Energy",
    "CZ_HICP_FOOD_INDEX": "Food",
    "CZ_HICP_SERVICES_INDEX": "Services",
    "CZ_FX_EURCZK": "EUR/CZK",
}
INK, MUTED, GRID = "#0f1e2e", "#5b6b7b", "#eef1f4"

CSS = """
<style>
.block-container {padding-top: 2.0rem; padding-bottom: 3rem; max-width: 1480px;}
#MainMenu, footer {visibility: hidden;}
h1 {font-weight: 750; letter-spacing: -0.02em; color: #0f1e2e; margin-bottom: .1rem;}
h2, h3 {color: #0f1e2e; font-weight: 650;}
.lead {color:#5b6b7b; font-size:1.02rem; margin:-2px 0 6px;}
/* KPI cards */
.kpi {background:#ffffff; border:1px solid #e6eaf0; border-radius:14px;
      padding:16px 18px 14px; box-shadow:0 1px 2px rgba(16,42,67,.04); height:100%;}
.kpi .lab {font-size:.72rem; letter-spacing:.06em; text-transform:uppercase; color:#7a8794; font-weight:600;}
.kpi .val {font-size:1.9rem; font-weight:740; color:#0f1e2e; line-height:1.15; margin-top:2px;}
.kpi .sub {font-size:.8rem; color:#7a8794; margin-top:2px;}
/* small stat tiles */
.tile {background:#f7f9fc; border:1px solid #e6eaf0; border-radius:12px; padding:12px 14px;}
.tile .t-lab {font-size:.74rem; color:#5b6b7b; font-weight:600;}
.tile .t-val {font-size:1.25rem; font-weight:700; color:#0f1e2e;}
.tile .t-dn {color:#b42318;} .tile .t-up {color:#067647;}
.pill {display:inline-block; background:#eef4ff; color:#155EEF; border:1px solid #d6e4ff;
       border-radius:999px; padding:3px 12px; font-size:.8rem; font-weight:600;}
div[data-testid="stMetric"] {background:#f7f9fc; border:1px solid #e6eaf0; border-radius:12px; padding:12px 16px;}
.stTabs [data-baseweb="tab-list"] {gap:6px;}
.stTabs [data-baseweb="tab"] {padding:8px 14px; font-weight:600;}
hr {margin:1.1rem 0; border-color:#eef1f4;}
</style>
"""


def _db_path() -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args, _ = parser.parse_known_args()
    return Path(args.db)


@st.cache_data(show_spinner=False)
def load_data(path: str, mtime: float):
    del mtime
    with sqlite3.connect(path) as conn:
        metadata = pd.read_sql_query("SELECT * FROM metadata ORDER BY series_id", conn)
        current = pd.read_sql_query(CURRENT_QUERY, conn)
        history = pd.read_sql_query(
            "SELECT * FROM time_series ORDER BY series_id, reference_date, vintage_date", conn
        )
        logs = pd.read_sql_query("SELECT * FROM logs ORDER BY id DESC", conn)
    for frame in (current, history):
        if not frame.empty:
            frame["reference_date"] = pd.to_datetime(frame["reference_date"])
            frame["vintage_date"] = pd.to_datetime(frame["vintage_date"])
    return metadata, current, history, logs


@st.cache_data(show_spinner=False)
def load_as_of(path: str, mtime: float, as_of: str) -> pd.DataFrame:
    del mtime
    with sqlite3.connect(path) as conn:
        frame = pd.read_sql_query(AS_OF_QUERY, conn, params={"as_of": as_of})
    if not frame.empty:
        frame["reference_date"] = pd.to_datetime(frame["reference_date"])
        frame["vintage_date"] = pd.to_datetime(frame["vintage_date"])
    return frame


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8")


def _short(sid: str) -> str:
    return SHORT.get(sid, sid)


def _color_scale(series_ids) -> alt.Scale:
    ids = [s for s in COLORS if s in set(series_ids)]
    return alt.Scale(domain=[_short(s) for s in ids], range=[COLORS[s] for s in ids])


def kpi(col, label: str, value: str, sub: str = "", accent: str = "#155EEF") -> None:
    safe_label = html.escape(str(label))
    safe_value = html.escape(str(value))
    safe_sub = html.escape(str(sub))
    col.markdown(
        f'<div class="kpi" style="border-top:3px solid {accent}">'
        f'<div class="lab">{safe_label}</div><div class="val">{safe_value}</div>'
        f'<div class="sub">{safe_sub}</div></div>',
        unsafe_allow_html=True,
    )


def _apply_transform(frame: pd.DataFrame, func) -> pd.DataFrame:
    """Apply a per-series transform from kinea.transforms and drop uncomputable rows."""
    out = frame.sort_values("reference_date").copy()
    out["value"] = out.groupby("series_id")["value"].transform(func)
    return out.dropna(subset=["value"])


def add_yoy(frame: pd.DataFrame, periods: int = 12) -> pd.DataFrame:
    return _apply_transform(frame, lambda s: transforms.year_over_year(s, periods))


def add_mom(frame: pd.DataFrame) -> pd.DataFrame:
    return _apply_transform(frame, transforms.month_over_month)


def add_annualized(frame: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    return _apply_transform(frame, lambda s: transforms.annualized(s, window))


def rebase100(frame: pd.DataFrame) -> pd.DataFrame:
    return _apply_transform(frame, transforms.rebase)


def line_chart(
    frame: pd.DataFrame,
    y_title: str,
    value_fmt: str = ".2f",
    height: int = 380,
    direct_labels: bool = False,
) -> alt.LayerChart:
    data = frame.copy()
    data["Series"] = data["series_id"].map(_short).fillna(data["series_id"])
    scale = _color_scale(frame["series_id"].unique())
    multi = data["series_id"].nunique() > 1
    color = alt.Color(
        "Series:N",
        title=None,
        scale=scale,
        legend=alt.Legend(orient="top", symbolType="stroke") if multi else None,
    )
    dash = alt.StrokeDash(
        "Series:N",
        title=None,
        legend=None,
        scale=alt.Scale(
            domain=[_short(s) for s in COLORS if s in set(frame["series_id"])],
            range=[[1, 0], [7, 3], [2, 2], [9, 3, 2, 3], [5, 2]],
        ),
    )

    base = alt.Chart(data).encode(
        x=alt.X("reference_date:T", title=None, axis=alt.Axis(grid=False)),
        y=alt.Y(
            "value:Q",
            title=y_title,
            scale=alt.Scale(zero=False),
            axis=alt.Axis(grid=True, gridColor=GRID),
        ),
    )
    line = base.mark_line(strokeWidth=2, interpolate="monotone").encode(
        color=color if multi else alt.value(list(scale.range)[0] if scale.range else "#2a78d6"),
        strokeDash=dash if multi else alt.value([1, 0]),
    )

    nearest = alt.selection_point(
        nearest=True, on="mouseover", fields=["reference_date"], empty=False
    )
    selectors = (
        base.mark_point(opacity=0)
        .add_params(nearest)
        .encode(
            tooltip=[
                alt.Tooltip("Series:N"),
                alt.Tooltip("reference_date:T", title="Reference date"),
                alt.Tooltip("value:Q", title=y_title, format=value_fmt),
                alt.Tooltip("vintage_date:T", title="Vintage"),
            ]
        )
    )
    hover_pts = base.mark_point(size=60, filled=True).encode(
        color=color if multi else alt.value("#2a78d6"),
        opacity=alt.condition(nearest, alt.value(1), alt.value(0)),
    )
    rule = (
        base.transform_filter(nearest)
        .mark_rule(color="#c3ccd6", strokeWidth=1)
        .encode(x="reference_date:T")
    )

    layers = [line, selectors, hover_pts, rule]
    if direct_labels and multi:
        last = data.sort_values("reference_date").groupby("series_id", as_index=False).tail(1)
        labels = (
            alt.Chart(last)
            .mark_text(align="left", dx=7, dy=0, fontWeight=600, fontSize=12)
            .encode(x="reference_date:T", y="value:Q", text="Series:N", color=color)
        )
        layers.append(labels)
    chart = alt.layer(*layers).properties(height=height)
    return (
        chart.configure_view(stroke=None)
        .configure_axis(
            labelColor=MUTED,
            titleColor=MUTED,
            titleFontWeight=600,
            domainColor=GRID,
            tickColor=GRID,
        )
        .configure_legend(labelColor=INK, labelFontSize=12)
    )


def latest_reading(current: pd.DataFrame, sid: str, periods: int):
    s = current[current["series_id"] == sid].sort_values("reference_date")
    if s.empty:
        return None, None
    latest = s["value"].iloc[-1]
    yoy = None
    if len(s) > periods:
        prev = s["value"].iloc[-1 - periods]
        if prev:
            yoy = (latest / prev - 1) * 100.0
    return latest, yoy


def period_selector(frame: pd.DataFrame, key: str, default: str = "Last 10 years") -> pd.DataFrame:
    options = ["Last 2 years", "Last 5 years", "Last 10 years", "Full history"]
    choice = st.radio("Window", options, index=options.index(default), horizontal=True, key=key)
    if frame.empty or choice == "Full history":
        return frame
    years = {"Last 2 years": 2, "Last 5 years": 5, "Last 10 years": 10}[choice]
    cutoff = frame["reference_date"].max() - pd.DateOffset(years=years)
    return frame[frame["reference_date"] >= cutoff]


def add_freshness(metadata: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    """Add a frequency-aware freshness indicator for operational review."""
    result = metadata.copy()
    as_of = pd.Timestamp(today or date.today())
    last = pd.to_datetime(result["last_observation"], errors="coerce")
    result["lag_days"] = (as_of - last).dt.days.clip(lower=0)
    thresholds = {"daily": 7, "weekly": 21, "monthly": 75, "quarterly": 150}
    allowed = result["frequency"].map(thresholds).fillna(90)
    result["freshness"] = result["lag_days"].le(allowed).map({True: "Fresh", False: "Review"})
    return result


def main() -> None:
    st.set_page_config(page_title="Czech inflation predictors", page_icon="📊", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    db_path = _db_path()
    if not db_path.exists():
        st.error(f"Database not found: {db_path}")
        st.code("python scripts/generate_evidence.py --mode live")
        st.stop()

    mtime = db_path.stat().st_mtime
    metadata, current, history, logs = load_data(str(db_path), mtime)
    if metadata.empty or current.empty:
        st.warning("The database exists but contains no collected series yet.")
        st.code("python scripts/generate_evidence.py --mode live")
        st.stop()

    # Vintages/as-of read from the labelled simulated-revision DB when the main DB has none.
    revision_path, revision_mtime = db_path, mtime
    revision_metadata, revision_current, revision_history = metadata, current, history
    demo_used = False
    revised_in_main = history.groupby(["series_id", "reference_date"]).size().gt(1).any()
    if not revised_in_main and DEFAULT_REVISION_DB.exists():
        revision_path = DEFAULT_REVISION_DB
        revision_mtime = revision_path.stat().st_mtime
        revision_metadata, revision_current, revision_history, _ = load_data(
            str(revision_path), revision_mtime
        )
        demo_used = True

    # ---- header -----------------------------------------------------------------------
    latest_reference = current["reference_date"].max() if not current.empty else None
    st.markdown("# Czech inflation predictors")
    st.markdown(
        '<p class="lead">ECB HICP components &amp; EUR/CZK — raw published levels, '
        "versioned by the day each value was observed.</p>",
        unsafe_allow_html=True,
    )
    if latest_reference is not None:
        st.markdown(
            f'<span class="pill">Data through {latest_reference.date()}</span>',
            unsafe_allow_html=True,
        )
    st.write("")

    revisions = max(len(revision_history) - len(revision_current), 0)
    revision_label = "Demo revisions" if demo_used else "Observed revisions"
    revision_sub = (
        "simulated evidence · official ECB data unchanged"
        if demo_used
        else "older vintages kept, never overwritten"
    )
    successful_runs = int((logs["status"] == "success").sum()) if not logs.empty else 0
    span = ""
    if not metadata.empty:
        span = f"{metadata['first_observation'].min()} → {metadata['last_observation'].max()}"
    c1, c2, c3, c4 = st.columns(4)
    kpi(c1, "Series collected", f"{len(metadata)}", "complete ECB predictor set", "#155EEF")
    kpi(c2, "Observations", f"{len(current):,}", span, "#0e9384")
    kpi(c3, revision_label, f"{revisions:,}", revision_sub, "#eda100")
    kpi(c4, "Successful runs", f"{successful_runs}", "idempotent · one log per run", "#4a3aa7")
    st.write("")

    overview, hicp_tab, fx_tab, vintage_tab, as_of_tab, audit_tab = st.tabs(
        ["Overview", "HICP components", "EUR/CZK", "Vintages", "As-of", "Audit"]
    )

    # ---- Overview ---------------------------------------------------------------------
    with overview:
        left, right = st.columns([3, 2], gap="large")
        with left:
            st.subheader("Inflation components, year over year")
            st.caption(
                "Derived from the stored index levels (2025 = 100). The store never keeps "
                "transformed values — only what the ECB publishes."
            )
            hicp_ids = [s for s in metadata["series_id"] if "_HICP_" in s]
            hero = current[current["series_id"].isin(hicp_ids)]
            hero = hero[
                hero["reference_date"] >= hero["reference_date"].max() - pd.DateOffset(years=10)
            ]
            hero_yoy = add_yoy(hero, 12)
            if not hero_yoy.empty:
                st.altair_chart(
                    line_chart(hero_yoy, "% change vs a year earlier", ".1f", 360), width="stretch"
                )
            st.markdown("**Latest reading**")
            tiles = st.columns(len(hicp_ids))
            for tcol, sid in zip(tiles, hicp_ids, strict=True):
                val, yoy = latest_reading(current, sid, 12)
                arrow = "" if yoy is None else ("▲" if yoy >= 0 else "▼")
                cls = "t-up" if (yoy is not None and yoy >= 0) else "t-dn"
                yoy_txt = (
                    "" if yoy is None else f'<span class="{cls}">{arrow} {yoy:+.1f}% y/y</span>'
                )
                tcol.markdown(
                    f'<div class="tile"><div class="t-lab">{_short(sid)}</div>'
                    f'<div class="t-val">{val:.2f}</div><div style="font-size:.8rem">{yoy_txt}</div></div>',
                    unsafe_allow_html=True,
                )
        with right:
            st.subheader("What's in the dataset")
            coverage = add_freshness(metadata)[
                [
                    "name",
                    "frequency",
                    "unit",
                    "first_observation",
                    "last_observation",
                    "observation_count",
                    "lag_days",
                    "freshness",
                ]
            ].rename(
                columns={
                    "name": "Series",
                    "frequency": "Freq",
                    "unit": "Unit",
                    "first_observation": "First",
                    "last_observation": "Last",
                    "observation_count": "Obs",
                    "lag_days": "Lag (days)",
                    "freshness": "Freshness",
                }
            )
            st.dataframe(coverage, hide_index=True, width="stretch")
            st.markdown(
                "**How the versioning works**\n"
                "- `reference_date` — the period a number describes\n"
                "- `vintage_date` — the day we observed that value\n"
                "- unchanged value → no new row · revision → new vintage · same-day fix → update in place"
            )
            with st.expander("Official ECB endpoints"):
                st.dataframe(
                    metadata[["name", "source_url"]].rename(
                        columns={"name": "Series", "source_url": "Endpoint"}
                    ),
                    hide_index=True,
                    width="stretch",
                )
            d1, d2 = st.columns(2)
            d1.download_button(
                "Metadata CSV",
                _csv_bytes(metadata),
                "kinea-metadata.csv",
                "text/csv",
                key="dl_meta",
                width="stretch",
            )
            d2.download_button(
                "Current obs CSV",
                _csv_bytes(current),
                "kinea-current.csv",
                "text/csv",
                key="dl_cur",
                width="stretch",
            )

    # ---- HICP components --------------------------------------------------------------
    with hicp_tab:
        hicp_ids = [s for s in metadata["series_id"] if "_HICP_" in s]
        top = st.columns([3, 2, 2])
        selected = top[0].multiselect("Components", hicp_ids, default=hicp_ids, format_func=_short)
        view = top[1].selectbox(
            "View",
            [
                "Index level (2025=100)",
                "Year-over-year %",
                "Month-over-month %",
                "3m annualized %",
                "Rebased to 100",
            ],
        )
        hicp_full = current[current["series_id"].isin(selected)]
        with top[2]:
            hicp = period_selector(hicp_full, "hicp_win", "Last 10 years")
        if hicp.empty:
            st.info("Select at least one component.")
        else:
            window_start = hicp["reference_date"].min()
            if view == "Year-over-year %":
                shown = add_yoy(hicp_full, 12)
                shown = shown[shown["reference_date"] >= window_start]
                ytitle, fmt = "% change vs a year earlier", ".1f"
            elif view == "Month-over-month %":
                shown = add_mom(hicp_full)
                shown = shown[shown["reference_date"] >= window_start]
                ytitle, fmt = "% change vs previous month", ".1f"
            elif view == "3m annualized %":
                shown = add_annualized(hicp_full)
                shown = shown[shown["reference_date"] >= window_start]
                ytitle, fmt = "3-month change, annualized %", ".1f"
            elif view == "Rebased to 100":
                shown, ytitle, fmt = rebase100(hicp), "Index (window start = 100)", ".1f"
            else:
                shown, ytitle, fmt = hicp, "HICP index (2025 = 100)", ".2f"
            st.altair_chart(line_chart(shown, ytitle, fmt, 400), width="stretch")
            if view != "Index level (2025=100)":
                st.caption("Derived in the view layer — the database stores only raw index levels.")
            if view in {"Month-over-month %", "3m annualized %"}:
                st.caption(
                    "ECB component levels are not seasonally adjusted; short-horizon changes "
                    "can contain recurring seasonal effects."
                )
            latest = hicp.sort_values("reference_date").groupby("series_id").tail(1).copy()
            latest["Series"] = latest["series_id"].map(_short)
            latest["reference_date"] = latest["reference_date"].dt.date
            latest["vintage_date"] = latest["vintage_date"].dt.date
            st.dataframe(
                latest[["Series", "reference_date", "value", "vintage_date"]].rename(
                    columns={
                        "reference_date": "Reference date",
                        "value": "Index",
                        "vintage_date": "Vintage",
                    }
                ),
                hide_index=True,
                width="stretch",
            )
            st.download_button(
                "Download displayed CSV",
                _csv_bytes(shown),
                "kinea-hicp.csv",
                "text/csv",
                key="dl_hicp",
            )

            st.write("")
            st.markdown("**Year-over-year heatmap**")
            st.caption(
                "Inflation regime at a glance — darker red indicates faster year-over-year "
                "price growth."
            )
            heat = add_yoy(hicp_full, 12)
            heat = heat[heat["reference_date"] >= window_start]
            if not heat.empty:
                heat = heat.copy()
                heat["Component"] = heat["series_id"].map(_short)
                heatmap = (
                    alt.Chart(heat)
                    .mark_rect()
                    .encode(
                        x=alt.X(
                            "yearmonth(reference_date):T",
                            title=None,
                            axis=alt.Axis(format="%Y", grid=False),
                        ),
                        y=alt.Y(
                            "Component:N",
                            title=None,
                            sort=[
                                _short(s) for s in hicp_ids if _short(s) in set(heat["Component"])
                            ],
                        ),
                        color=alt.Color(
                            "value:Q",
                            title="YoY %",
                            scale=alt.Scale(scheme="yelloworangered"),
                        ),
                        tooltip=[
                            alt.Tooltip("Component:N"),
                            alt.Tooltip("yearmonth(reference_date):T", title="Month"),
                            alt.Tooltip("value:Q", title="YoY %", format=".1f"),
                        ],
                    )
                    .properties(height=32 * heat["Component"].nunique() + 40)
                    .configure_view(stroke=None)
                    .configure_axis(
                        labelColor=MUTED, titleColor=MUTED, domainColor=GRID, tickColor=GRID
                    )
                )
                st.altair_chart(heatmap, width="stretch")

    # ---- EUR/CZK ----------------------------------------------------------------------
    with fx_tab:
        fx_all = current[current["series_id"].str.contains("_FX_", na=False)]
        st.subheader("EUR/CZK reference rate")
        st.caption(
            "Czech koruna per euro. A higher value means a weaker koruna — a channel for "
            "imported inflation into tradable goods and energy."
        )
        if not fx_all.empty:
            fx = period_selector(fx_all, "fx_win", "Last 5 years")
            s = fx.sort_values("reference_date")
            t = st.columns(4)
            t[0].markdown(
                f'<div class="tile"><div class="t-lab">Latest</div>'
                f'<div class="t-val">{s["value"].iloc[-1]:.3f}</div></div>',
                unsafe_allow_html=True,
            )
            t[1].markdown(
                f'<div class="tile"><div class="t-lab">Window min</div>'
                f'<div class="t-val">{s["value"].min():.3f}</div></div>',
                unsafe_allow_html=True,
            )
            t[2].markdown(
                f'<div class="tile"><div class="t-lab">Window max</div>'
                f'<div class="t-val">{s["value"].max():.3f}</div></div>',
                unsafe_allow_html=True,
            )
            t[3].markdown(
                f'<div class="tile"><div class="t-lab">Observations</div>'
                f'<div class="t-val">{len(fx):,}</div></div>',
                unsafe_allow_html=True,
            )
            st.write("")
            st.altair_chart(
                line_chart(fx, "CZK per EUR", ".3f", 400, direct_labels=False), width="stretch"
            )
            st.download_button(
                "Download displayed CSV",
                _csv_bytes(fx),
                "kinea-eurczk.csv",
                "text/csv",
                key="dl_fx",
            )

    # ---- Vintages ---------------------------------------------------------------------
    with vintage_tab:
        st.subheader("Revision history (vintages)")
        if demo_used:
            st.info(
                "Showing `evidence/revision_demo.db` — a labelled, simulated revision. The "
                "official values in `evidence/kinea.db` are never modified."
            )
        grouped = (
            revision_history.groupby(["series_id", "reference_date"], as_index=False)
            .size()
            .rename(columns={"size": "versions"})
        )
        revised = grouped[grouped["versions"] > 1]
        if revised.empty:
            st.info("No multi-vintage observation in this database yet.")
        else:
            c1, c2 = st.columns(2)
            rseries = sorted(revised["series_id"].unique())
            chosen = c1.selectbox("Revised series", rseries, format_func=_short)
            rdates = revised[revised["series_id"] == chosen]["reference_date"]
            chosen_ref = c2.selectbox("Reference date", sorted(rdates.dt.date.unique()))
            detail = revision_history[
                (revision_history["series_id"] == chosen)
                & (revision_history["reference_date"].dt.date == chosen_ref)
            ].sort_values("vintage_date")
            first, last = detail.iloc[0], detail.iloc[-1]
            dv = last["value"] - first["value"]
            safe_series = html.escape(_short(chosen))
            st.markdown(
                f'<div class="tile" style="border-left:4px solid {COLORS.get(chosen, "#155EEF")}">'
                f"<b>{safe_series} · {chosen_ref}</b> — first observed "
                f"<b>{first['value']:.2f}</b> on {first['vintage_date'].date()}, currently "
                f"<b>{last['value']:.2f}</b> (Δ {dv:+.2f})</div>",
                unsafe_allow_html=True,
            )
            st.write("")
            pct = (dv / first["value"] * 100.0) if first["value"] else float("nan")
            lag_days = (last["vintage_date"].date() - first["vintage_date"].date()).days
            rt = st.columns(3)
            rt[0].markdown(
                f'<div class="tile"><div class="t-lab">Revision size</div>'
                f'<div class="t-val">{dv:+.2f}</div></div>',
                unsafe_allow_html=True,
            )
            rt[1].markdown(
                f'<div class="tile"><div class="t-lab">Revision %</div>'
                f'<div class="t-val">{pct:+.2f}%</div></div>',
                unsafe_allow_html=True,
            )
            rt[2].markdown(
                f'<div class="tile"><div class="t-lab">Vintage lag</div>'
                f'<div class="t-val">{lag_days} days</div></div>',
                unsafe_allow_html=True,
            )
            st.write("")
            col_a, col_b = st.columns([3, 2], gap="large")
            with col_a:
                dd = detail.copy()
                dd["Vintage"] = dd["vintage_date"].dt.date.astype(str)
                dd["state"] = (
                    ["first"] + ["current"] * (len(dd) - 1) if len(dd) > 1 else ["current"]
                )
                bar = (
                    alt.Chart(dd)
                    .mark_bar(size=46, cornerRadiusEnd=3)
                    .encode(
                        x=alt.X("Vintage:N", title="Vintage (collection date)", sort=None),
                        y=alt.Y("value:Q", title="Published value", scale=alt.Scale(zero=False)),
                        color=alt.Color(
                            "state:N",
                            scale=alt.Scale(
                                domain=["first", "current"],
                                range=["#9aa7b4", COLORS.get(chosen, "#155EEF")],
                            ),
                            legend=alt.Legend(orient="top", title=None),
                        ),
                        tooltip=["Vintage", "value", "state"],
                    )
                )
                txt = (
                    alt.Chart(dd)
                    .mark_text(dy=-8, fontWeight=600, color=INK)
                    .encode(
                        x=alt.X("Vintage:N", sort=None),
                        y="value:Q",
                        text=alt.Text("value:Q", format=".2f"),
                    )
                )
                st.altair_chart(
                    (bar + txt)
                    .properties(height=320)
                    .configure_view(stroke=None)
                    .configure_axis(
                        labelColor=MUTED, titleColor=MUTED, domainColor=GRID, tickColor=GRID
                    ),
                    width="stretch",
                )
            with col_b:
                vtable = detail[["vintage_date", "value", "collected_at"]].copy()
                vtable["vintage_date"] = vtable["vintage_date"].dt.date
                st.dataframe(
                    vtable.rename(
                        columns={
                            "vintage_date": "Vintage",
                            "value": "Value",
                            "collected_at": "Collected at",
                        }
                    ),
                    hide_index=True,
                    width="stretch",
                )
                st.download_button(
                    "Download vintage history CSV",
                    _csv_bytes(detail),
                    "kinea-vintages.csv",
                    "text/csv",
                    key="dl_vint",
                )

    # ---- As-of ------------------------------------------------------------------------
    with as_of_tab:
        st.subheader("Point-in-time snapshot (as-of)")
        st.caption(
            "Reconstruct what we knew on a chosen date: the latest vintage of each "
            "observation with `vintage_date ≤ as-of`. Same ROW_NUMBER query as the current view."
        )
        vintage_dates = sorted(revision_history["vintage_date"].dropna().dt.date.unique())
        if not vintage_dates:
            st.info("No vintage is available for an as-of query yet.")
            vintage_dates = [date.today()]
        min_v, max_v = vintage_dates[0], vintage_dates[-1]
        default_v = min_v if demo_used and min_v < max_v else max_v
        a1, a2 = st.columns([1, 2])
        as_of = a1.date_input("Knowledge date", value=default_v, min_value=min_v, max_value=max_v)
        as_series = a2.selectbox(
            "Series", revision_metadata["series_id"].tolist(), format_func=_short, key="asof_series"
        )
        snap = load_as_of(str(revision_path), revision_mtime, date.isoformat(as_of))
        snap = snap[snap["series_id"] == as_series]
        cur_sel = revision_current[revision_current["series_id"] == as_series][
            ["reference_date", "value", "vintage_date"]
        ].rename(columns={"value": "current_value", "vintage_date": "current_vintage"})
        if snap.empty:
            st.warning("No value had been observed for this series by the selected date.")
        else:
            merged = snap[["reference_date", "value", "vintage_date"]].merge(
                cur_sel, on="reference_date", how="left"
            )
            changed = merged[(merged["current_value"] - merged["value"]).abs() > 1e-12]
            n = len(changed)
            st.markdown(
                f'<span class="pill">{n} observation(s) changed since {as_of}</span>',
                unsafe_allow_html=True,
            )
            st.write("")
            overlay = pd.concat(
                [
                    snap[["reference_date", "value"]].assign(series_id="_asof"),
                    cur_sel.rename(columns={"current_value": "value"})[
                        ["reference_date", "value"]
                    ].assign(series_id="_current"),
                ]
            )
            overlay["Snapshot"] = overlay["series_id"].map(
                {"_asof": f"as-of {as_of}", "_current": "current"}
            )
            lines = (
                alt.Chart(overlay)
                .mark_line(strokeWidth=2, interpolate="monotone")
                .encode(
                    x=alt.X("reference_date:T", title=None, axis=alt.Axis(grid=False)),
                    y=alt.Y(
                        "value:Q",
                        title="Value",
                        scale=alt.Scale(zero=False),
                        axis=alt.Axis(grid=True, gridColor=GRID),
                    ),
                    color=alt.Color(
                        "Snapshot:N",
                        scale=alt.Scale(
                            domain=[f"as-of {as_of}", "current"],
                            range=["#9aa7b4", COLORS.get(as_series, "#155EEF")],
                        ),
                        legend=alt.Legend(orient="top", title=None),
                    ),
                    tooltip=[
                        "Snapshot",
                        alt.Tooltip("reference_date:T"),
                        alt.Tooltip("value:Q", format=".3f"),
                    ],
                )
            )
            changed_points = overlay[overlay["reference_date"].isin(changed["reference_date"])]
            points = (
                alt.Chart(changed_points)
                .mark_point(size=95, filled=True, stroke="white", strokeWidth=1)
                .encode(
                    x="reference_date:T",
                    y="value:Q",
                    color=alt.Color(
                        "Snapshot:N",
                        scale=alt.Scale(
                            domain=[f"as-of {as_of}", "current"],
                            range=["#9aa7b4", COLORS.get(as_series, "#155EEF")],
                        ),
                        legend=None,
                    ),
                    tooltip=[
                        "Snapshot",
                        alt.Tooltip("reference_date:T"),
                        alt.Tooltip("value:Q", format=".3f"),
                    ],
                )
            )
            ch = (
                (lines + points)
                .properties(height=360)
                .configure_view(stroke=None)
                .configure_axis(
                    labelColor=MUTED, titleColor=MUTED, domainColor=GRID, tickColor=GRID
                )
            )
            st.altair_chart(ch, width="stretch")
            if not changed.empty:
                st.dataframe(
                    changed.assign(difference=lambda d: d["current_value"] - d["value"]).rename(
                        columns={
                            "reference_date": "Reference date",
                            "value": f"Value @ {as_of}",
                            "vintage_date": "As-of vintage",
                            "current_value": "Value now",
                            "current_vintage": "Current vintage",
                            "difference": "Δ",
                        }
                    )[
                        [
                            "Reference date",
                            f"Value @ {as_of}",
                            "Value now",
                            "Δ",
                            "As-of vintage",
                            "Current vintage",
                        ]
                    ],
                    hide_index=True,
                    width="stretch",
                )
            st.download_button(
                "Download as-of snapshot CSV",
                _csv_bytes(snap),
                f"kinea-as-of-{as_of}.csv",
                "text/csv",
                key="dl_asof",
            )

    # ---- Audit ------------------------------------------------------------------------
    with audit_tab:
        st.subheader("Execution log — one row per run")
        st.caption(
            "A success and an intentionally triggered error are both present, so a reviewer "
            "can confirm the collector logs even when it fails (§5.6)."
        )
        show = logs.copy()
        started = pd.to_datetime(show["started_at"], utc=True, errors="coerce")
        finished = pd.to_datetime(show["finished_at"], utc=True, errors="coerce")
        show["duration_s"] = (finished - started).dt.total_seconds().round(2)
        show["started_at"] = started.dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        show["finished_at"] = finished.dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        show["status"] = show["status"].str.upper()
        st.dataframe(
            show[["id", "started_at", "finished_at", "duration_s", "status", "log_text"]],
            hide_index=True,
            width="stretch",
        )
        errs = logs[logs["status"] == "error"]
        if not errs.empty:
            with st.expander("Latest captured traceback"):
                st.code(errs.iloc[0]["traceback"] or "No traceback")
        st.write("")
        latest_success = logs[logs["status"] == "success"]
        quality_state = "UNKNOWN"
        if not latest_success.empty:
            latest_log_text = str(latest_success.iloc[0]["log_text"])
            quality_state = "PASS" if "quality=pass" in latest_log_text else "REVIEW"
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("metadata rows", f"{len(metadata):,}")
        b2.metric("time_series rows", f"{len(history):,}")
        b3.metric("log rows", f"{len(logs):,}")
        b4.metric("data quality", quality_state)
        st.markdown("**Reproduce end to end**")
        st.code(
            "python -m pytest -q\n"
            "python scripts/generate_evidence.py --mode live\n"
            "python scripts/validate_delivery.py\n"
            "python -m streamlit run dashboard/app.py"
        )
        st.download_button(
            "Download execution logs CSV",
            _csv_bytes(logs),
            "kinea-logs.csv",
            "text/csv",
            key="dl_logs",
        )


if __name__ == "__main__":
    main()
