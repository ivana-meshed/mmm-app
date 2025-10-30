# 3_Prepare_Training_Datav2.py — Single-page wizard: Qualify → Signal → Map Exposures → Pick Buckets → Rules+VIF
import io
import json
import numpy as np
import pandas as pd
import streamlit as st
from sklearn.linear_model import LinearRegression
from scipy import stats

from app_shared import (
    build_meta_views,
    build_plat_map_df,
    render_sidebar,
    filter_range,
    previous_window,
    freq_to_rule,
    fmt_num,
    GREEN, RED,
)

st.set_page_config(page_title="Prepare Training Data (v2)", layout="wide")
st.title("Prepare Training Data (v2) — Guided Wizard")

# ---- Require data loaded by your loader page
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

# =========================
# Helpers (local, lightweight)
# =========================
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

def _passes_rules(series: pd.Series, min_nonnull: float, min_nonzero: float, max_spike: float) -> bool:
    s = pd.to_numeric(series, errors="coerce")
    n = s.notna().mean()
    nz = (s.fillna(0) != 0).mean()
    if s.notna().sum() >= 10:
        q1, q3 = np.nanpercentile(s, [25, 75])
        iqr = q3 - q1
        hi = q3 + 1.5 * iqr
        spike = (s > hi).mean()
    else:
        spike = 0.0
    return (n >= min_nonnull) and (nz >= min_nonzero) and (spike <= max_spike)

def _has_token(col: str, token: str) -> bool:
    u = ALL_COLS_UP.get(col, col.upper())
    return token in u

# =========================
# Wizard state
# =========================
st.session_state.setdefault("wizard_step", 1)
step = st.session_state["wizard_step"]

# Step stores
# 1
st.session_state.setdefault("pool_step1", None)
# 2
st.session_state.setdefault("goal_step2", None)
st.session_state.setdefault("exposure_metric_choice", {})  # {plat: {"kind": str, "column": or None}}
st.session_state.setdefault("pool_step2", None)
# 3
st.session_state.setdefault("bucket_picks", None)  # {"paid_vars": [], "organic": [], "context": [], "factors": []}
# 4 / 5
st.session_state.setdefault("rules_profile", {"profile":"Balanced","min_nonnull":0.75,"min_nonzero":0.50,"max_spike":0.35})
st.session_state.setdefault("drivers_post_rules", None)
st.session_state.setdefault("vif_keep", None)
st.session_state.setdefault("spend_map", {})  # {platform: spend_col_or_None}
st.session_state.setdefault("drivers_final", None)

def _step_header():
    badges = []
    for i, name in [
        (1, "Qualify"),
        (2, "Signal & Exposures"),
        (3, "Pick Buckets"),
        (4, "Rules"),
        (5, "VIF & Export"),
    ]:
        done = "✅ " if st.session_state.get({
            1:"pool_step1", 2:"pool_step2", 3:"bucket_picks", 4:"drivers_post_rules", 5:"drivers_final"
        }[i]) else ""
        cur = " ▶" if step == i else ""
        badges.append(f"{done}**{i}. {name}**{cur}")
    st.markdown(" • ".join(badges))

_step_header()
st.markdown("---")

# =========================
# STEP 1 — Column stats → qualify
# =========================
if step >= 1:
    with st.expander("Step 1 — Column stats & qualification", expanded=(step == 1)):
        num_cols = df_r.select_dtypes(include=[np.number]).columns.tolist()

        profile = st.selectbox("Profile", ["Strict","Balanced","Lenient","Custom"],
                               index=["Strict","Balanced","Lenient","Custom"].index(
                                   st.session_state["rules_profile"]["profile"] if step==1 else "Balanced"
                               ))
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

        st.caption("We preselect columns that pass thresholds; you can adjust below.")

        stats_rows = []
        passing = []
        for c in num_cols:
            s = pd.to_numeric(df_r[c], errors="coerce")
            non_null = s.notna().mean()
            non_zero = (s.fillna(0) != 0).mean()
            if s.notna().sum() >= 10:
                q1, q3 = np.nanpercentile(s, [25, 75])
                iqr = q3 - q1
                hi = q3 + 1.5 * iqr
                spike = (s > hi).mean()
            else:
                spike = 0.0
            if (non_null >= min_nonnull) and (non_zero >= min_nonzero) and (spike <= max_spike):
                passing.append(c)
            stats_rows.append(dict(
                column=c, non_null=f"{non_null:.2%}", non_zero=f"{non_zero:.2%}", spike=f"{spike:.2%}"
            ))
        st.dataframe(pd.DataFrame(stats_rows), use_container_width=True, hide_index=True)

        nice_map = {nice(c): c for c in num_cols}
        default_labels = [nice(c) for c in passing]
        sel_labels = st.multiselect("Qualified columns", list(nice_map.keys()), default=default_labels, key="step1_multiselect")
        pool_step1 = [nice_map[lbl] for lbl in sel_labels]

        cA, cB = st.columns([1,1])
        if cA.button("Continue → Step 2 (Signal & Exposures)", type="primary"):
            st.session_state["pool_step1"] = pool_step1
            st.session_state["rules_profile"] = dict(profile=profile, min_nonnull=min_nonnull, min_nonzero=min_nonzero, max_spike=max_spike)
            st.session_state["wizard_step"] = 2
            st.experimental_rerun()
        if cB.button("Reset Step 1"):
            st.session_state["pool_step1"] = None
            st.session_state["wizard_step"] = 1
            st.experimental_rerun()

st.markdown("---")

# =========================
# STEP 2 — Signal vs goal + exposure recommendation/mapping
# =========================
if step >= 2 and st.session_state.get("pool_step1"):
    with st.expander("Step 2 — Signal checks vs goal & exposure mapping", expanded=(step == 2)):
        if not goal_cols:
            st.info("No goals in metadata.")
            st.stop()
        g_sel = st.selectbox("Goal", [nice(g) for g in goal_cols],
                             index=0 if st.session_state.get("goal_step2") is None
                             else [nice(g) for g in goal_cols].index(nice(st.session_state["goal_step2"])))
        y_col = {nice(g): g for g in goal_cols}[g_sel]
        st.session_state["goal_step2"] = y_col

        # Compute signal trio for pool_step1
        sig_rows = []
        for c in st.session_state["pool_step1"]:
            sig = _signal_trio(df_r, c, y_col)
            sig_rows.append(dict(column=c, r2=sig["r2"], nmae=sig["nmae"], rho=sig["rho"]))
        if sig_rows:
            disp = pd.DataFrame(sig_rows)
            d2 = disp.copy()
            for k in ["r2","nmae","rho"]:
                d2[k] = d2[k].map(lambda v: f"{v:.2f}" if pd.notna(v) else "–")
            st.dataframe(d2[["column","r2","nmae","rho"]], use_container_width=True, hide_index=True)

        # Exposure recommendations per platform (Paid vars only, intersect pool_step1)
        st.markdown("#### Exposure mapping (per platform)")
        st.caption("Recommendation uses |Spearman ρ| (primary), then R², then lower nMAE. You can override.")

        # Build per-platform candidate groups
        def _recommend_for_platform(plat: str):
            tok = plat.upper()
            pool = [c for c in st.session_state["pool_step1"] if _has_token(c, tok)]
            candidates = dict(
                Impressions=[c for c in pool if c in IMPR_COLS],
                Clicks     =[c for c in pool if c in CLICK_COLS],
                Sessions   =[c for c in pool if c in SESSION_COLS],
                Spend      =plat_map_df.loc[plat_map_df["platform"].eq(plat), "col"].tolist()
            )
            best_kind, best_col, best_key = "None", None, (1e9, 1e9, 1e9)
            for kind, cols in candidates.items():
                for col in cols:
                    s = _signal_trio(df_r, col, y_col)
                    # sort key: lower is better
                    key = (-(abs(s["rho"])) if pd.notna(s["rho"]) else -0.0,
                           -(s["r2"]) if pd.notna(s["r2"]) else -0.0,
                           (s["nmae"]) if pd.notna(s["nmae"]) else 1e9)
                    # because we want max rho/r2, min nmae → use negative for max
                    key_conv = (-key[0], -key[1], key[2])
                    if key_conv < best_key:
                        best_key = key_conv
                        best_kind, best_col = kind, col
            return best_kind, best_col, candidates

        exp_choice = st.session_state["exposure_metric_choice"] or {}
        for plat in platforms:
            rec_kind, rec_col, candidates = _recommend_for_platform(plat)
            # Preselect recommendation unless already chosen in state
            prev = exp_choice.get(plat, {"kind":"None","column":None})
            cc1, cc2 = st.columns([1,2])
            with cc1:
                st.markdown(f"**{plat}**")
                if rec_col:
                    st.caption(f"Recommended: **{rec_kind}** (`{rec_col}`)")
                else:
                    st.caption("No recommendation (insufficient signal).")
            with cc2:
                kind = st.selectbox(
                    f"{plat} metric",
                    ["None","Impressions","Clicks","Sessions","Spend"],
                    index=["None","Impressions","Clicks","Sessions","Spend"].index(prev["kind"] if prev else (rec_kind if rec_col else "None")),
                    key=f"exp_kind_{plat}"
                )
                if kind == "None":
                    chosen = "—"
                else:
                    opts = candidates.get(kind, [])
                    default_idx = 0
                    if prev and prev.get("column") in opts:
                        default_idx = opts.index(prev["column"]) + 1
                    elif rec_col and rec_col in opts and kind == rec_kind:
                        default_idx = opts.index(rec_col) + 1
                    chosen = st.selectbox(f"{plat} {kind} column", ["—"] + opts, index=min(default_idx, len(opts)), key=f"exp_col_{plat}")
                exp_choice[plat] = {"kind": kind, "column": None if chosen in (None,"—") else chosen}
            st.markdown("---")
        st.session_state["exposure_metric_choice"] = exp_choice

        # Validate all-or-none rule
        flags = [bool(v["column"]) for v in exp_choice.values()] if platforms else []
        all_none = not any(flags)
        all_have = all(flags) if platforms else True
        if not (all_none or all_have):
            st.error("Exposure mapping must be **all platforms mapped** or **none**. Adjust selections above.")

        # Continue / reset
        cA, cB = st.columns([1,1])
        if cA.button("Continue → Step 3 (Pick Buckets)", type="primary", disabled=not (all_none or all_have)):
            # Build pool_step2 = pool_step1 ∪ chosen exposure cols
            extra = [cfg["column"] for cfg in exp_choice.values() if cfg.get("column")]
            st.session_state["pool_step2"] = list(dict.fromkeys(st.session_state["pool_step1"] + extra))
            st.session_state["wizard_step"] = 3
            st.experimental_rerun()
        if cB.button("Back to Step 1"):
            st.session_state["wizard_step"] = 1
            st.experimental_rerun()

st.markdown("---")

# =========================
# STEP 3 — Pick candidates per bucket
# =========================
if step >= 3 and st.session_state.get("pool_step2"):
    with st.expander("Step 3 — Pick candidates per bucket", expanded=(step == 3)):
        pool = set(st.session_state["pool_step2"])
        def _picker(label, options, key):
            opts = sorted([c for c in options if c in pool])
            if not opts:
                st.caption(f"— no variables mapped to **{label}** by metadata or qualified earlier —")
                return []
            nice_opts = sorted([(nice(c), c) for c in opts], key=lambda t: t[0].lower())
            default = [n for n,_ in nice_opts]  # preselect all that survived
            sel = st.multiselect(label, [n for n,_ in nice_opts], default=default, key=key)
            return [raw for n, raw in nice_opts if n in sel]

        sel_paid_vars = _picker("Paid media variables (exposure candidates)", paid_var_cols, "pick_paid_vars_v2")
        sel_org_vars  = _picker("Organic variables", organic_cols, "pick_org_vars_v2")
        sel_ctx_vars  = _picker("Context variables", context_cols, "pick_ctx_vars_v2")
        sel_fct_vars  = _picker("Factor variables (optional)", factor_cols, "pick_fct_vars_v2")

        cA, cB = st.columns([1,1])
        if cA.button("Continue → Step 4 (Rules)", type="primary"):
            st.session_state["bucket_picks"] = {
                "paid_vars": sel_paid_vars,
                "organic":   sel_org_vars,
                "context":   sel_ctx_vars,
                "factors":   sel_fct_vars,
            }
            st.session_state["wizard_step"] = 4
            st.experimental_rerun()
        if cB.button("Back to Step 2"):
            st.session_state["wizard_step"] = 2
            st.experimental_rerun()

st.markdown("---")

# =========================
# STEP 4 & 5 — Rules → Global VIF & Export
# =========================
if step >= 4 and st.session_state.get("bucket_picks"):
    with st.expander("Step 4 — Apply rules (pre-VIF)", expanded=(step == 4)):
        # Profile
        prof = st.session_state["rules_profile"]
        profile = st.selectbox("Profile", ["Strict","Balanced","Lenient","Custom"],
                               index=["Strict","Balanced","Lenient","Custom"].index(prof.get("profile","Balanced")))
        defaults = dict(
            Strict  = dict(min_nonnull=0.85, min_nonzero=0.60, max_spike=0.25),
            Balanced= dict(min_nonnull=0.75, min_nonzero=0.50, max_spike=0.35),
            Lenient = dict(min_nonnull=0.65, min_nonzero=0.30, max_spike=0.45),
        )
        if profile == "Custom":
            c1,c2,c3 = st.columns(3)
            min_nonnull = c1.slider("Min non-null ratio", 0.0, 1.0, prof.get("min_nonnull",0.75), 0.01)
            min_nonzero = c2.slider("Min non-zero ratio", 0.0, 1.0, prof.get("min_nonzero",0.50), 0.01)
            max_spike   = c3.slider("Max spike rate (IQR outliers)", 0.0, 1.0, prof.get("max_spike",0.35), 0.01)
        else:
            p = defaults[profile]
            min_nonnull, min_nonzero, max_spike = p["min_nonnull"], p["min_nonzero"], p["max_spike"]

        # Build initial driver pool = selected (org/context/factors) + exposures (if all-have)
        exp_choice = st.session_state["exposure_metric_choice"]
        flags = [bool(v["column"]) for v in exp_choice.values()] if platforms else []
        all_none = not any(flags)
        all_have = all(flags) if platforms else True

        drivers = list(dict.fromkeys(
            st.session_state["bucket_picks"]["organic"] +
            st.session_state["bucket_picks"]["context"] +
            st.session_state["bucket_picks"]["factors"]
        ))
        if all_have:
            for plat, cfg in exp_choice.items():
                if cfg["column"]:
                    drivers.append(cfg["column"])
        # Apply rules
        keep = [c for c in drivers if c in df_r.columns and _passes_rules(df_r[c], min_nonnull, min_nonzero, max_spike)]
        dropped = sorted(set(drivers) - set(keep))
        if dropped:
            st.info(f"Dropped by rules: {', '.join(dropped)}")
        st.session_state["drivers_post_rules"] = keep
        st.session_state["rules_profile"] = dict(profile=profile, min_nonnull=min_nonnull, min_nonzero=min_nonzero, max_spike=max_spike)

        cA, cB = st.columns([1,1])
        if cA.button("Continue → Step 5 (VIF & Export)", type="primary"):
            st.session_state["wizard_step"] = 5
            st.experimental_rerun()
        if cB.button("Back to Step 3"):
            st.session_state["wizard_step"] = 3
            st.experimental_rerun()

st.markdown("---")

if step >= 5 and st.session_state.get("drivers_post_rules") is not None:
    with st.expander("Step 5 — Global VIF, Spend map & Export", expanded=(step == 5)):
        Xg = _prepare_numeric(df_r, st.session_state["drivers_post_rules"])
        if Xg.empty or Xg.shape[1] < 2:
            st.info("Not enough variables after rules to compute VIF.")
            st.session_state["drivers_final"] = Xg.columns.tolist()
        else:
            cn = _cond_number(Xg)
            vifg = _vif_table(Xg)
            vdisp = vifg.copy()
            vdisp["Variable"] = vdisp["variable"].map(nice)
            vdisp["VIF"] = vdisp["VIF"].map(lambda v: f"{v:.2f}" if pd.notna(v) else "–")
            st.caption(f"Cond#: {cn:.1f}" if pd.notna(cn) else "Cond#: –")
            st.dataframe(vdisp[["Variable","VIF"]], hide_index=True, use_container_width=True)

            # Persist a keep-set across button clicks
            if st.session_state["vif_keep"] is None or set(st.session_state["vif_keep"]) - set(Xg.columns):
                st.session_state["vif_keep"] = set(Xg.columns.tolist())

            strong   = set(vifg.loc[vifg["VIF"] >= 10, "variable"].tolist())
            moderate = set(vifg.loc[(vifg["VIF"] >= 7.5) & (vifg["VIF"] < 10), "variable"].tolist())
            mild     = set(vifg.loc[(vifg["VIF"] >= 5.0) & (vifg["VIF"] < 7.5), "variable"].tolist())

            cA, cB, cC, cD = st.columns(4)
            if cA.button(f"Drop ≥10 (Strong) — {len(strong)}"):
                st.session_state["vif_keep"] -= strong
            if cB.button(f"Drop ≥7.5 (Strong+Moderate) — {len(strong|moderate)}"):
                st.session_state["vif_keep"] -= (strong | moderate)
            if cC.button(f"Drop ≥5 (Strict) — {len(strong|moderate|mild)}"):
                st.session_state["vif_keep"] -= (strong | moderate | mild)
            if cD.button("Reset VIF filters"):
                st.session_state["vif_keep"] = set(Xg.columns.tolist())

            final_vars = [c for c in Xg.columns if c in st.session_state["vif_keep"]]
            st.session_state["drivers_final"] = final_vars

        # Spend mapping (explicit, per platform)
        st.markdown("#### Spend map per platform")
        spend_map = st.session_state.get("spend_map", {}) or {}
        for plat in platforms:
            spend_cols = plat_map_df.loc[plat_map_df["platform"].eq(plat), "col"].tolist()
            current = spend_map.get(plat)
            idx = 0
            if current in spend_cols:
                idx = spend_cols.index(current) + 1
            val = st.selectbox(f"{plat} spend column", options=(["—"] + spend_cols), index=min(idx, len(spend_cols)), key=f"spend_{plat}_v2")
            spend_map[plat] = None if val in (None, "—") else val
        st.session_state["spend_map"] = spend_map

        # Guard: if exposures chosen for all platforms, suggest spend presence
        exp_choice = st.session_state["exposure_metric_choice"]
        flags = [bool(v["column"]) for v in exp_choice.values()] if platforms else []
        all_have = all(flags) if platforms else True
        if all_have:
            missing_spend = [p for p in platforms if not spend_map.get(p)]
            if missing_spend:
                st.warning(f"Spends missing for: {', '.join(missing_spend)}. Robyn ROI cannot be computed for these.")

        # Final summary
        st.markdown("#### Final selection")
        col_left, col_right = st.columns([1.2, 1])
        with col_left:
            st.write("**Drivers**")
            if st.session_state["drivers_final"]:
                st.write(pd.DataFrame({"variable": st.session_state["drivers_final"], "nice": [nice(c) for c in st.session_state["drivers_final"]]}))
            else:
                st.info("No drivers selected.")
        with col_right:
            st.write("**Spend map (per platform)**")
            sm = pd.DataFrame([{"platform": p, "spend_col": st.session_state["spend_map"].get(p) or "—"} for p in platforms])
            st.dataframe(sm, use_container_width=True, hide_index=True)

        # Persist payload
        payload = dict(
            page="Prepare_Training_Datav2",
            goal=GOAL,
            timeframe=TIMEFRAME_LABEL,
            rule_profile=st.session_state["rules_profile"],
            exposures={p: st.session_state["exposure_metric_choice"].get(p, {}).get("column") for p in platforms
                       if st.session_state["exposure_metric_choice"].get(p, {}).get("column")},
            spends=st.session_state["spend_map"],
            drivers=st.session_state["drivers_final"] or [],
            dropped_by_rules=list(sorted(set(st.session_state.get("drivers_post_rules", [])) - set(st.session_state.get("drivers_final", [])))),
        )
        st.session_state["experiment_v2_payload"] = payload

        # Downloads
        buf = io.StringIO()
        pd.DataFrame({"variable": st.session_state["drivers_final"] or []}).to_csv(buf, index=False)
        st.download_button("Download final drivers (CSV)", buf.getvalue(), "experiment_v2_drivers.csv", "text/csv")
        st.download_button("Download config (JSON)", json.dumps(payload, indent=2), "experiment_v2_config.json", "application/json")

        cA, cB = st.columns([1,1])
        if cA.button("Back to Step 4"):
            st.session_state["wizard_step"] = 4
            st.experimental_rerun()
        if cB.button("Restart Wizard"):
            for k in ["pool_step1","goal_step2","exposure_metric_choice","pool_step2","bucket_picks",
                      "drivers_post_rules","vif_keep","spend_map","drivers_final"]:
                st.session_state[k] = None if k != "exposure_metric_choice" else {}
            st.session_state["wizard_step"] = 1
            st.experimental_rerun()