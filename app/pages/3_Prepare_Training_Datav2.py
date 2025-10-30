# 4_run_experimentv2.py — Explore ➜ Prepare (explicit mapping; no auto-priority)
import os
import re
import io
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from sklearn.linear_model import LinearRegression
from scipy import stats

from app_shared import (
    # shared state/helpers from your app
    build_meta_views,
    build_plat_map_df,
    render_sidebar,
    filter_range,
    previous_window,
    resample_numeric,
    period_label,
    freq_to_rule,
    fmt_num,
    safe_eff,
    GREEN, RED,
)

st.set_page_config(page_title="Run Experiment v2 — Explore → Prepare", layout="wide")
st.title("Run Experiment v2 — Explore → Prepare")

# ---- Expect df/meta already loaded by your loader page
df = st.session_state.get("df", pd.DataFrame())
meta = st.session_state.get("meta", {}) or {}
DATE_COL = st.session_state.get("date_col", "DATE")
CHANNELS_MAP = st.session_state.get("channels_map", {}) or {}

if df.empty or not meta:
    st.info("Load data & metadata first in **Select Data**.")
    st.stop()

# ---------- Meta helpers ----------
(
    display_map,
    nice,
    goal_cols,
    mapping,
    m,
    ALL_COLS_UP,     # dict: col -> UPPER(col)
    IMPR_COLS,
    CLICK_COLS,
    SESSION_COLS,
    INSTALL_COLS,
) = build_meta_views(meta, df)

paid_spend_cols = [c for c in (mapping.get("paid_media_spends", []) or []) if c in df.columns]
paid_var_cols   = [c for c in (mapping.get("paid_media_vars",   []) or []) if c in df.columns]
organic_cols    = [c for c in (mapping.get("organic_vars",      []) or []) if c in df.columns]
context_cols    = [c for c in (mapping.get("context_vars",      []) or []) if c in df.columns]
factor_cols     = [c for c in (mapping.get("factor_vars",       []) or []) if c in df.columns]

# Platform/color map (for paid spend columns)
plat_map_df, platforms, PLATFORM_COLORS = build_plat_map_df(
    present_spend=paid_spend_cols,
    df=df, meta=meta, m=m,
    COL="column_name", PLAT="platform",
    CHANNELS_MAP=CHANNELS_MAP,
)

# ---------- Sidebar (timeframe etc.) ----------
GOAL, sel_countries, TIMEFRAME_LABEL, RANGE, agg_label, FREQ = render_sidebar(meta, df, nice, goal_cols)
RULE = freq_to_rule(FREQ)

# Filter by country (if provided)
if sel_countries and "COUNTRY" in df.columns:
    df = df[df["COUNTRY"].astype(str).isin(sel_countries)].copy()

# Windowed frames
df_r = filter_range(df.copy(), DATE_COL, RANGE)
df_prev = previous_window(df, df_r, DATE_COL, RANGE)

# Lightweight utils
def _prepare_numeric(frame: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    if not cols:
        return pd.DataFrame()
    X = frame[cols].apply(pd.to_numeric, errors="coerce")
    X = X.dropna(axis=1, how="all").fillna(0.0)
    nun = X.nunique(dropna=False)
    X = X[nun[nun > 1].index.tolist()]
    std = X.std(ddof=0)
    return X[std[std > 1e-12].index.tolist()]

def _vif_table(X: pd.DataFrame) -> pd.DataFrame:
    vars_ = X.columns.tolist()
    if len(vars_) < 2:
        return pd.DataFrame({"variable": vars_, "VIF": [np.nan] * len(vars_)})
    # standardize
    Xs = (X - X.mean(0)) / X.std(0).replace(0, 1)
    Xs = Xs.replace([np.inf, -np.inf], 0.0)
    out = []
    for col in vars_:
        y = Xs[col].values
        X_oth = Xs.drop(columns=[col]).values
        try:
            r2 = LinearRegression().fit(X_oth, y).score(X_oth, y)
            vif = np.inf if (1 - r2) <= 1e-12 else 1.0 / (1.0 - r2)
        except Exception:
            vif = np.nan
        out.append((col, float(vif)))
    return pd.DataFrame(out, columns=["variable", "VIF"]).sort_values("VIF", ascending=False)

def _cond_number(X: pd.DataFrame):
    if X.shape[1] < 2:
        return np.nan
    Xs = (X - X.mean(0)) / X.std(0).replace(0, 1)
    Xs = Xs.replace([np.inf, -np.inf], 0).values
    try:
        s = np.linalg.svd(Xs, compute_uv=False)
        s = s[s > 1e-12]
        if len(s) < 2:
            return np.nan
        return float(s.max() / s.min())
    except Exception:
        return np.nan

def _signal_trio(frame: pd.DataFrame, x_col: str, y_col: str):
    x = pd.to_numeric(frame.get(x_col, pd.Series(dtype=float)), errors="coerce")
    y = pd.to_numeric(frame.get(y_col, pd.Series(dtype=float)), errors="coerce")
    mask = x.notna() & y.notna()
    x, y = x[mask], y[mask]
    if len(x) < 5:
        return dict(r2=np.nan, nmae=np.nan, rho=np.nan)
    # quadratic fit
    X = np.vstack([np.ones_like(x), x, x**2]).T
    try:
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        y_hat = X @ beta
        # R^2
        ssr = ((y - y_hat) ** 2).sum()
        sst = ((y - y.mean()) ** 2).sum()
        r2 = 1 - (ssr / sst) if sst > 0 else np.nan
        # relative MAE (scale by P95-P05)
        mae = np.mean(np.abs(y - y_hat))
        y_p5, y_p95 = np.percentile(y, [5, 95])
        scale = max(y_p95 - y_p5, 1e-9)
        nmae = mae / scale
        # Spearman
        rho, _ = stats.spearmanr(x, y, nan_policy="omit")
        return dict(r2=float(r2), nmae=float(nmae), rho=float(rho))
    except Exception:
        return dict(r2=np.nan, nmae=np.nan, rho=np.nan)

# ===== Tabs =====
tab_explore, tab_prepare = st.tabs(["A) Explore relationships", "B) Prepare dataset"])

# -----------------------------------------------------------------------------
# TAB A — EXPLORE RELATIONSHIPS
# -----------------------------------------------------------------------------
with tab_explore:
    st.subheader("Explore & pick candidates (no transforms)")
    st.caption("Inspect coverage & signal; pick per-bucket candidates. VIF here is a **preview** (bucket-local).")

    # ---- Column-level stats (compact)
    with st.expander("Column stats (coverage, zeros, basic moments)", expanded=False):
        num_cols = df_r.select_dtypes(include=[np.number]).columns.tolist()
        if num_cols:
            stats_rows = []
            for c in num_cols:
                s = pd.to_numeric(df_r[c], errors="coerce")
                n = s.notna().sum()
                zeros = (s == 0).sum()
                miss = len(s) - n
                if n > 0:
                    desc = s.describe(percentiles=[.05, .5, .95])
                    row = dict(
                        column=c,
                        non_null=n,
                        missing=miss,
                        zero=zeros,
                        mean=desc.get("mean", np.nan),
                        std=desc.get("std", np.nan),
                        p5=desc.get("5%", np.nan),
                        p50=desc.get("50%", np.nan),
                        p95=desc.get("95%", np.nan),
                    )
                else:
                    row = dict(column=c, non_null=0, missing=len(s), zero=zeros,
                               mean=np.nan, std=np.nan, p5=np.nan, p50=np.nan, p95=np.nan)
                stats_rows.append(row)
            stat_df = pd.DataFrame(stats_rows)
            show_df = stat_df.copy()
            for k in ["non_null","missing","zero"]:
                show_df[k] = show_df[k].map(lambda x: fmt_num(x) if pd.notna(x) else "–")
            st.dataframe(show_df, use_container_width=True, hide_index=True)
        else:
            st.info("No numeric columns.")

    # ---- Signal vs goal (R², rel-MAE, Spearman ρ)
    with st.expander("Signal checks vs goals (R², rel-MAE, Spearman ρ)", expanded=False):
        if not goal_cols:
            st.info("No goals in metadata.")
        else:
            g_sel = st.selectbox("Goal", [nice(g) for g in goal_cols], index=0, key="exp_goal")
            y_col = {nice(g): g for g in goal_cols}[g_sel]
            # Evaluate for each candidate driver across buckets (concise)
            buckets = {
                "Paid Spend": paid_spend_cols,
                "Paid Vars":  paid_var_cols,
                "Organic":    organic_cols,
                "Context":    context_cols,
                "Factors":    factor_cols,
            }
            out_rows = []
            for title, cols in buckets.items():
                for c in cols:
                    sig = _signal_trio(df_r, c, y_col)
                    out_rows.append(dict(bucket=title, column=c, **sig))
            if out_rows:
                sig_df = pd.DataFrame(out_rows)
                disp = sig_df.copy()
                for k in ["r2","nmae","rho"]:
                    disp[k] = disp[k].map(lambda v: f"{v:.2f}" if pd.notna(v) else "–")
                st.dataframe(disp[["bucket","column","r2","nmae","rho"]],
                             use_container_width=True, hide_index=True)
            else:
                st.info("No candidate drivers found by metadata.")

    # ---- Per-bucket selection with checkboxes + preview VIF
    st.markdown("### Pick candidates per bucket")
    c1, c2 = st.columns([1.2, 1])

    # --- REPLACE this helper entirely ---
    def multi_pick(label, options, key):
        # options: list[str] of raw column names
        nice_opts = sorted([(nice(c), c) for c in options], key=lambda t: t[0].lower())
        default = [nice(c) for c in options]  # preselect all
        sel_nice = st.multiselect(label, [n for n, _ in nice_opts], default=default, key=key)
        # return raw names for the selected nice labels
        return [raw for n, raw in nice_opts if n in sel_nice]

    with c1:
        sel_paid_vars = multi_pick("Paid media variables (exposure candidates)", paid_var_cols, "pick_paid_vars")
        sel_org_vars  = multi_pick("Organic variables", organic_cols, "pick_org_vars")
        sel_ctx_vars  = multi_pick("Context variables", context_cols, "pick_ctx_vars")
        sel_fct_vars  = multi_pick("Factor variables (optional)", factor_cols, "pick_fct_vars")

    with c2:
        # VIF PREVIEW (within bucket)
        st.caption("VIF preview (within bucket; not binding)")
        for title, cols, key in [
            ("Paid Vars", sel_paid_vars, "vif_paid"),
            ("Organic",   sel_org_vars,  "vif_org"),
            ("Context",   sel_ctx_vars,  "vif_ctx"),
            ("Factors",   sel_fct_vars,  "vif_fct"),
        ]:
            st.markdown(f"**{title}**")
            X = _prepare_numeric(df_r, cols)
            if X.empty or X.shape[1] < 2:
                st.caption("— insufficient columns —")
            else:
                cn = _cond_number(X)
                vif = _vif_table(X)
                vdisp = vif.copy()
                vdisp["Variable"] = vdisp["variable"].map(nice)
                vdisp["VIF"] = vdisp["VIF"].map(lambda v: f"{v:.2f}" if pd.notna(v) else "–")
                st.caption(f"Cond#: {cn:.1f}" if pd.notna(cn) else "Cond#: –")
                st.dataframe(vdisp[["Variable","VIF"]], hide_index=True, use_container_width=True)

    # Persist candidate selection for Tab B
    if st.button("▶ Continue with variable selection", type="primary"):
        st.session_state["candidate_selection_v2"] = {
            "paid_vars": sel_paid_vars,
            "organic":   sel_org_vars,
            "context":   sel_ctx_vars,
            "factors":   sel_fct_vars,
        }
        st.success("Selection saved. Switch to **B) Prepare dataset**.")
        # Optional: auto-jump
        st.experimental_set_query_params(tab="prepare")

# -----------------------------------------------------------------------------
# TAB B — PREPARE DATASET
# -----------------------------------------------------------------------------
with tab_prepare:
    st.subheader("Prepare Robyn-ready dataset (explicit mapping; rules → global VIF)")
    pick = st.session_state.get("candidate_selection_v2", None)
    if not pick:
        st.info("Pick candidates in **A) Explore** first.")
        st.stop()

    # --- 1) Explicit exposure mapping per platform (no auto-priority)
    st.markdown("### 1) Choose ONE exposure per paid platform (Impr / Click / Session / Spend)")
    st.caption("Robyn expects a consistent exposure set. Choose exactly one per platform **or** choose none for all (then skip paid exposures).")

    # Helper: find candidate columns per platform token
    def _has_token(col: str, token: str) -> bool:
        u = ALL_COLS_UP.get(col, col.upper())
        return token in u

    exposure_choices = {}
    for plat in platforms:
        tok = plat.upper()
        # candidates intersect with your selected paid_vars only
        paid_candidates = pick["paid_vars"]
        impr = [c for c in paid_candidates if (_has_token(c, tok) and c in IMPR_COLS)]
        click= [c for c in paid_candidates if (_has_token(c, tok) and c in CLICK_COLS)]
        sess = [c for c in paid_candidates if (_has_token(c, tok) and c in SESSION_COLS)]
        spend_candidates = plat_map_df.loc[plat_map_df["platform"].eq(plat), "col"].tolist()
        # UI row
        st.markdown(f"**{plat}**")
        cc1, cc2, cc3, cc4 = st.columns(4)
        choice_label = cc1.selectbox(
            "Metric", ["None","Impressions","Clicks","Sessions","Spend"], index=0, key=f"exp_kind_{plat}"
        )
        if choice_label == "Impressions":
            chosen = cc2.selectbox("Pick column", ["—"] + impr, index=0 if not impr else 1, key=f"exp_col_{plat}")
        elif choice_label == "Clicks":
            chosen = cc2.selectbox("Pick column", ["—"] + click, index=0 if not click else 1, key=f"exp_col_{plat}")
        elif choice_label == "Sessions":
            chosen = cc2.selectbox("Pick column", ["—"] + sess, index=0 if not sess else 1, key=f"exp_col_{plat}")
        elif choice_label == "Spend":
            chosen = cc2.selectbox("Pick column", ["—"] + spend_candidates, index=0 if not spend_candidates else 1, key=f"exp_col_{plat}")
        else:
            chosen = "—"
        exposure_choices[plat] = dict(kind=choice_label, column=None if chosen in (None,"—") else chosen)
        st.markdown("---")

    # Validate rule: either all platforms have a column OR none have (re: “expects either all or none”)
    chosen_flags = [bool(v["column"]) for v in exposure_choices.values()]
    all_none = not any(chosen_flags)
    all_have = all(chosen_flags) if platforms else True
    if not (all_none or all_have):
        st.error("Exposure mapping must be **all platforms mapped** or **none**. Adjust selections above.")
        st.stop()

    # --- 2) Spend mapping per platform (explicit)
    st.markdown("### 2) Confirm spend columns per platform")
    st.caption("Robyn ROI requires spend aligned with exposures. If you chose no exposures, you can still keep spends for reporting (not fed as drivers).")
    spend_map = {}
    for plat in platforms:
        spend_cols = plat_map_df.loc[plat_map_df["platform"].eq(plat), "col"].tolist()
        val = st.selectbox(
            f"{plat} spend column", options=(["—"] + spend_cols), index=0 if not spend_cols else 1, key=f"spend_{plat}"
        )
        spend_map[plat] = None if val in (None,"—") else val

    # Optional check: if exposures are chosen, strongly suggest spend present as well
    if all_have:
        missing_spend = [p for p in platforms if not spend_map.get(p)]
        if missing_spend:
            st.warning(f"Spends missing for: {', '.join(missing_spend)}. Robyn ROI will not be computed for them.")

    # --- 3) Rules profile (before global VIF)
    st.markdown("### 3) Rule profile")
    profile = st.selectbox("Profile", ["Strict","Balanced","Lenient","Custom"], index=1)
    defaults = dict(
        Strict  = dict(min_nonnull=0.85, min_nonzero=0.60, max_spike=0.25),
        Balanced= dict(min_nonnull=0.75, min_nonzero=0.50, max_spike=0.35),
        Lenient = dict(min_nonnull=0.65, min_nonzero=0.30, max_spike=0.45),
    )
    if profile == "Custom":
        c1,c2,c3 = st.columns(3)
        min_nonnull = c1.slider("Min non-null ratio", 0.0, 1.0, 0.75, 0.01)
        min_nonzero = c2.slider("Min non-zero ratio", 0.0, 1.0, 0.50, 0.01)
        max_spike   = c3.slider("Max spike rate (IQR outliers)", 0.0, 1.0, 0.35, 0.01)
    else:
        p = defaults[profile]
        min_nonnull, min_nonzero, max_spike = p["min_nonnull"], p["min_nonzero"], p["max_spike"]

    def _passes_rules(series: pd.Series) -> bool:
        s = pd.to_numeric(series, errors="coerce")
        n = s.notna().mean()
        nz = (s.fillna(0) != 0).mean()
        # simple spike: values beyond Q3 + 1.5*IQR
        if s.notna().sum() >= 10:
            q1, q3 = np.nanpercentile(s, [25, 75])
            iqr = q3 - q1
            hi = q3 + 1.5 * iqr
            spike = (s > hi).mean()
        else:
            spike = 0.0
        return (n >= min_nonnull) and (nz >= min_nonzero) and (spike <= max_spike)

    # Build initial driver pool = selected (paid/organic/context/factors) + exposures (if all_have)
    drivers = list(dict.fromkeys(pick["organic"] + pick["context"] + pick["factors"]))
    if all_have:
        # add chosen exposure columns (dedup)
        for plat, cfg in exposure_choices.items():
            if cfg["column"]:
                drivers.append(cfg["column"])
    # Apply rules
    drivers_rules_ok = [c for c in drivers if c in df_r.columns and _passes_rules(df_r[c])]
    dropped_by_rules = sorted(set(drivers) - set(drivers_rules_ok))

    if dropped_by_rules:
        st.info(f"Dropped by rules: {', '.join(dropped_by_rules)}")

    # --- 4) Global VIF (authoritative)
    st.markdown("### 4) Global VIF on post-rule set")
    Xg = _prepare_numeric(df_r, drivers_rules_ok)
    if Xg.empty or Xg.shape[1] < 2:
        st.info("Not enough variables after rules to compute VIF.")
        final_vars = Xg.columns.tolist()
    else:
        cn = _cond_number(Xg)
        vifg = _vif_table(Xg)
        vdisp = vifg.copy()
        vdisp["Variable"] = vdisp["variable"].map(nice)
        vdisp["VIF"] = vdisp["VIF"].map(lambda v: f"{v:.2f}" if pd.notna(v) else "–")
        st.caption(f"Cond#: {cn:.1f}" if pd.notna(cn) else "Cond#: –")
        st.dataframe(vdisp[["Variable","VIF"]], hide_index=True, use_container_width=True)

        # Drop buttons
        strong = set(vifg.loc[vifg["VIF"] >= 10, "variable"].tolist())
        moderate = set(vifg.loc[(vifg["VIF"] >= 7.5) & (vifg["VIF"] < 10), "variable"].tolist())
        mild = set(vifg.loc[(vifg["VIF"] >= 5.0) & (vifg["VIF"] < 7.5), "variable"].tolist())

        to_keep = set(Xg.columns.tolist())

        cA, cB, cC = st.columns(3)
        if cA.button(f"Drop ≥10 (Strong) — {len(strong)}"):
            to_keep -= strong
        if cB.button(f"Drop ≥7.5 (Strong+Moderate) — {len(strong|moderate)}"):
            to_keep -= (strong | moderate)
        if cC.button(f"Drop ≥5 (Strict) — {len(strong|moderate|mild)}"):
            to_keep -= (strong | moderate | mild)

        final_vars = list(dict.fromkeys([c for c in Xg.columns if c in to_keep]))

    # --- 5) Final summary & export
    st.markdown("### 5) Final selection")
    col_left, col_right = st.columns([1.2, 1])
    with col_left:
        st.write("**Drivers**")
        if final_vars:
            st.write(pd.DataFrame({"variable": final_vars, "nice": [nice(c) for c in final_vars]}))
        else:
            st.info("No drivers selected.")
    with col_right:
        st.write("**Spend map (per platform)**")
        sm = pd.DataFrame(
            [{"platform": p, "spend_col": spend_map.get(p) or "—"} for p in platforms]
        )
        st.dataframe(sm, use_container_width=True, hide_index=True)

    # Persist “experiment v2” payload
    payload = dict(
        goal=GOAL,
        timeframe=TIMEFRAME_LABEL,
        rule_profile=dict(profile=profile, min_nonnull=min_nonnull, min_nonzero=min_nonzero, max_spike=max_spike),
        exposures={p: exposure_choices[p]["column"] for p in platforms} if all_have else {},
        spends=spend_map,
        drivers=final_vars,
        dropped_by_rules=dropped_by_rules,
    )
    st.session_state["experiment_v2_payload"] = payload

    # Download buttons
    buf = io.StringIO()
    pd.DataFrame({"variable": final_vars}).to_csv(buf, index=False)
    st.download_button("Download final drivers (CSV)", buf.getvalue(), "experiment_v2_drivers.csv", "text/csv")

    import json
    st.download_button("Download config (JSON)", json.dumps(payload, indent=2), "experiment_v2_config.json", "application/json")

    st.success("Experiment v2 payload stored in session_state['experiment_v2_payload'].")