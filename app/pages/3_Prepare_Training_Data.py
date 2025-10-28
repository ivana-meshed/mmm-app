import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import warnings
import re
from sklearn.preprocessing import PolynomialFeatures
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score, mean_absolute_error
from scipy import stats
from app_shared import (
    build_meta_views, build_plat_map_df, validate_against_metadata, parse_date,
    pretty, fmt_num, freq_to_rule, period_label, safe_eff,
    render_sidebar, filter_range, previous_window, resample_numeric, total_with_prev,
)

# ---- Pull state from the loader page ---- 
df = st.session_state.get("df", pd.DataFrame())
meta = st.session_state.get("meta", {}) or {}
DATE_COL = st.session_state.get("date_col", "DATE")
CHANNELS_MAP = st.session_state.get("channels_map", {}) or {}

if df.empty or not meta:
    st.stop()

# ---- Build meta helpers & buckets ----
display_map, nice, goal_cols, mapping, m, ALL_COLS_UP, IMPR_COLS, CLICK_COLS, SESSION_COLS, INSTALL_COLS = build_meta_views(meta, df)

# Explicit column/meta tokens used later in Tab 5 try/except
COL = "column_name"
CAT = "main_category"

# Main goals (used as fallback in Tab 5)
meta_goals_main = [
    g.get("var")
    for g in (meta.get("goals") or [])
    if g and g.get("var") and (g.get("group","primary").strip().lower() in ("primary","main","goal",""))
]

# ---- Sidebar (own controls for this page) ----
GOAL, sel_countries, TIMEFRAME_LABEL, RANGE, agg_label, FREQ = render_sidebar(meta, df, nice, goal_cols)

# Country filter
if sel_countries and "COUNTRY" in df:
    df = df[df["COUNTRY"].astype(str).isin(sel_countries)].copy()

# ---- Target, spend, platforms ----
target = GOAL if (GOAL and GOAL in df.columns) else (goal_cols[0] if goal_cols else None)
paid_spend_cols = [c for c in (mapping.get("paid_media_spends", []) or []) if c in df.columns]
paid_var_cols   = [c for c in (mapping.get("paid_media_vars",   []) or []) if c in df.columns]
organic_cols    = [c for c in (mapping.get("organic_vars",      []) or []) if c in df.columns]

# context variables + include secondary goals as context (signal-only)
_context_base   = [c for c in (mapping.get("context_vars",      []) or []) if c in df.columns]
_secondary_goals = [
    g.get("var") for g in (meta.get("goals") or [])
    if g and g.get("var") and g.get("group","").strip().lower() in ("secondary","alt","secondary_goal")
    and g.get("var") in df.columns
]
# de-dup while preserving order
seen = set()
context_cols = []
for c in (_context_base + _secondary_goals):
    if c not in seen:
        context_cols.append(c); seen.add(c)

# Total spend column for efficiency calcs if needed later
df["_TOTAL_SPEND"] = df[paid_spend_cols].sum(axis=1) if paid_spend_cols else 0.0


# ---- Reactivity & persistence: DO NOT overwrite with session copies ----
# Persist current sidebar selections for other pages, but keep *current* values active here.
st.session_state["RANGE"]         = RANGE
st.session_state["FREQ"]          = FREQ
st.session_state["GOAL"]          = GOAL
st.session_state["SEL_COUNTRIES"] = sel_countries

# Rebuild helpers/buckets on the *filtered* df (after country filter), not the raw session df.
display_map, nice, goal_cols, mapping, m, ALL_COLS_UP, IMPR_COLS, CLICK_COLS, SESSION_COLS, INSTALL_COLS = build_meta_views(meta, df)

paid_spend_cols = [c for c in (mapping.get("paid_media_spends", []) or []) if c in df.columns]
paid_var_cols   = [c for c in (mapping.get("paid_media_vars",   []) or []) if c in df.columns]
organic_cols    = [c for c in (mapping.get("organic_vars",      []) or []) if c in df.columns]
context_cols    = [c for c in (mapping.get("context_vars",      []) or []) if c in df.columns]

# Recompute windows using the *current* RANGE/FREQ from the sidebar
RULE   = freq_to_rule(FREQ)
df_r   = filter_range(df.copy(), DATE_COL, RANGE)
df_prev = previous_window(df, df_r, DATE_COL, RANGE)
def total_with_prev_local(collist):
    return total_with_prev(df_r, df_prev, collist)
    
# -----------------------------
# Tabs
# -----------------------------
tab_rel, tab_diag, tab_deep  = st.tabs(
    [
        "Explore Relationships",
        "Prepare Dataset for Modeling",
        "Deep Dive: Individual Driver"
    ]
)

# =============================
# TAB 1 — RELATIONSHIPS 
# =============================
with tab_rel:
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    st.subheader("Relationships")

    # ----- Winsorize controls (this tab only) -----
    st.markdown("##### Data Conditioning")
    wins_mode_label = st.selectbox(
        "Winsorize mode",
        ["No", "Upper only", "Upper and lower"],
        index=0,
        help="Remove outliers",
    )
    _mode_map = {"No": "None", "Upper only": "Upper", "Upper and lower": "Both"}
    wins_mode = _mode_map[wins_mode_label]
    if wins_mode != "None":
        wins_pct_label = st.selectbox(
            "Winsorize level (keep up to this percentile)",
            ["99", "98", "95"],
            index=0,
        )
        wins_pct = int(wins_pct_label)
    else:
        wins_pct = 99  # unused when "None"

    def winsorize_columns(
        frame: pd.DataFrame, cols: list, mode: str, pct: int
    ) -> pd.DataFrame:
        if mode == "None" or not cols:
            return frame
        dfw = frame.copy()
        upper_q = pct / 100.0
        lower_q = 1 - upper_q if mode == "Both" else None
        for c in cols:
            if c not in dfw.columns:
                continue
            s = pd.to_numeric(dfw[c], errors="coerce")
            if s.notna().sum() == 0:
                continue
            hi = s.quantile(upper_q)
            if mode == "Upper":
                dfw[c] = np.where(s > hi, hi, s)
            else:
                lo = s.quantile(lower_q)
                dfw[c] = s.clip(lower=lo, upper=hi)
        return dfw

    # -------------------------------
    # Buckets & helpers (per-category)
    # -------------------------------
    def _uniq_preserve(seq):
        seen = set(); out = []
        for x in seq:
            if x not in seen:
                out.append(x); seen.add(x)
        return out

    # 1) Base buckets from metadata (present in df)
    paid_spend_cols = [c for c in (mapping.get("paid_media_spends", []) or []) if c in df.columns]
    paid_var_cols   = [c for c in (mapping.get("paid_media_vars",   []) or []) if c in df.columns]
    organic_cols    = [c for c in (mapping.get("organic_vars",      []) or []) if c in df.columns]

    # 2) Other Drivers = numeric columns not in any of the above, not goals/date/country/etc.
    EXCLUDE = set((paid_spend_cols or []) + (paid_var_cols or []) + (organic_cols or []) + (goal_cols or []))
    for col in ["DATE", DATE_COL, "COUNTRY", "DATE_PERIOD", "PERIOD_LABEL", "_TOTAL_SPEND"]:
        if col in df.columns: EXCLUDE.add(col)

    # numeric-only candidates
    numeric_df = df.select_dtypes(include=[np.number]).copy()
    other_driver_cols = [c for c in numeric_df.columns if c not in EXCLUDE]

    # final buckets (display order)
    buckets = {
        "Paid Media Spend":     paid_spend_cols,
        "Paid Media Variables": paid_var_cols,
        "Organic Variables":    organic_cols,
        "Other Drivers":        other_driver_cols,
    }

    all_driver_cols = [c for cols in buckets.values() for c in (cols or [])]
    all_corr_cols = list(set(all_driver_cols + (goal_cols or [])))

    # ---------------------------------
    # Winsorization (current & previous)
    # ---------------------------------
    df_r_w    = winsorize_columns(df_r,    all_corr_cols, wins_mode, wins_pct)
    df_prev_w = winsorize_columns(df_prev, all_corr_cols, wins_mode, wins_pct)

    # ----------
    # Heatmap UI
    # ----------
    def as_pct_text(df_vals: pd.DataFrame) -> pd.DataFrame:
        return df_vals.applymap(lambda v: (f"{v*100:.1f}%" if pd.notna(v) else ""))

    def heatmap_fig_from_matrix(mat: pd.DataFrame, title=None, zmin=-1, zmax=1):
        fig = go.Figure(
            go.Heatmap(
                z=mat.values,
                x=list(mat.columns),
                y=list(mat.index),
                zmin=zmin,
                zmax=zmax,
                colorscale="RdYlGn",
                text=as_pct_text(mat).values,
                texttemplate="%{text}",
                hovertemplate="Driver: %{y}<br>Goal: %{x}<br>r: %{z:.2f}<extra></extra>",
                colorbar=dict(title="r"),
            )
        )
        if title:
            fig.update_layout(title=title)
        fig.update_layout(xaxis=dict(side="top"))
        return fig

    # --------------------------
    # Correlation calculations
    # --------------------------
    def corr_matrix(frame: pd.DataFrame, drivers: list, goals: list, min_n: int = 8) -> pd.DataFrame:
        if frame.empty or not drivers or not goals:
            return pd.DataFrame(index=[nice(c) for c in drivers], columns=[nice(g) for g in goals], dtype=float)
        out = pd.DataFrame(index=[nice(c) for c in drivers], columns=[nice(g) for g in goals], dtype=float)
        for d in drivers:
            if d not in frame: 
                continue
            for g in goals:
                if g not in frame:
                    continue
                if d == g:
                    out.loc[nice(d), nice(g)] = np.nan
                    continue
                pair = frame[[d, g]].replace([np.inf, -np.inf], np.nan).dropna().copy()
                pair.columns = ["drv", "goal"]
                if len(pair) < min_n:
                    out.loc[nice(d), nice(g)] = np.nan
                else:
                    sd = pair["drv"].std(ddof=1); sg = pair["goal"].std(ddof=1)
                    if (pd.isna(sd) or pd.isna(sg)) or (sd <= 0 or sg <= 0):
                        out.loc[nice(d), nice(g)] = np.nan
                    else:
                        r = np.corrcoef(pair["drv"].values, pair["goal"].values)[0, 1]
                        out.loc[nice(d), nice(g)] = float(r)
        return out

    def corr_with_target_safe(frame, cols, tgt, min_n=8):
        rows = []
        if frame.empty or tgt not in frame:
            return pd.DataFrame(columns=["col", "corr"])
        for c in cols or []:
            if c not in frame: 
                continue
            pair = frame[[tgt, c]].replace([np.inf, -np.inf], np.nan).dropna().copy()
            pair.columns = (["goal", "drv"] if list(pair.columns)[0] == tgt else ["drv", "goal"])
            if len(pair) < min_n:
                continue
            sx, sy = pair["goal"].std(ddof=1), pair["drv"].std(ddof=1)
            if (pd.isna(sx) or pd.isna(sy)) or (sx <= 0 or sy <= 0):
                continue
            r = np.corrcoef(pair["goal"].values, pair["drv"].values)[0, 1]
            if np.isfinite(r):
                rows.append((c, float(r)))
        return pd.DataFrame(rows, columns=["col", "corr"]).set_index("col")

    # -----------------------------
    # Per-variable metric (quadratic)
    # -----------------------------
    def _per_var_metrics(frame, x_col, y_col):
        """Return dict with R2, NMAE, Rho, n for a single driver vs target."""
        x_raw = pd.to_numeric(frame[x_col], errors="coerce")
        y_raw = pd.to_numeric(frame[y_col], errors="coerce")
        mask = x_raw.notna() & y_raw.notna()
        x_f = x_raw[mask]; y_f = y_raw[mask]
        n = int(len(x_f))
        if n < 5:
            return dict(R2=np.nan, NMAE=np.nan, Rho=np.nan, n=n)

        # quadratic fit
        X = np.array(x_f).reshape(-1, 1)
        poly = PolynomialFeatures(degree=2)
        Xp = poly.fit_transform(X)
        mdl = LinearRegression().fit(Xp, y_f)
        y_hat = mdl.predict(Xp)

        r2  = r2_score(y_f, y_hat)
        mae = mean_absolute_error(y_f, y_hat)
        # relative MAE on 5–95% scale
        if y_f.nunique() > 1:
            y_p5, y_p95 = np.percentile(y_f, [5, 95])
            y_scale = max(y_p95 - y_p5, 1e-9)
        else:
            y_scale = max(float(y_f.max() - y_f.min()), 1e-9)
        nmae = mae / y_scale

        rho, _ = stats.spearmanr(x_f, y_f, nan_policy="omit")
        return dict(R2=float(r2), NMAE=float(nmae), Rho=(float(rho) if np.isfinite(rho) else np.nan), n=n)

    def metrics_table_for_bucket(frame, cols, y_col):
        if not cols or y_col not in frame:
            return pd.DataFrame(columns=["Variable", "R²", "MAE (rel)", "Spearman ρ", "n"])
        rows = []
        for c in cols:
            if c not in frame: 
                continue
            m = _per_var_metrics(frame, c, y_col)
            rows.append([nice(c), m["R2"], m["NMAE"], m["Rho"], m["n"]])
        out = pd.DataFrame(rows, columns=["Variable", "R²", "MAE (rel)", "Spearman ρ", "n"])
        if not out.empty:
            out = out.sort_values("R²", ascending=False)
        return out

    # ---------------------------------------------
    # Explore Relationships — per bucket UI layout
    # ---------------------------------------------
    st.markdown("### Explore Relationships — Per Driver Category")

    if not goal_cols:
        st.info("No goals found in metadata.")
    else:
        # single goal for Δ and metrics table
        _goal_opts = [nice(g) for g in goal_cols]
        _goal_default_idx = 0
        if GOAL and GOAL in goal_cols:
            try:
                _goal_default_idx = _goal_opts.index(nice(GOAL))
            except ValueError:
                _goal_default_idx = 0

        goal_sel = st.selectbox(
            "Goal for Δ and metrics table",
            _goal_opts,
            index=_goal_default_idx,
            key="rel_bucket_goal"
        )
        goal_sel_col = {nice(g): g for g in goal_cols}[goal_sel]

        # Render each bucket block: [corr heatmap | delta bar] then metrics table
        for bucket_name, cols_list in buckets.items():
            st.markdown(f"#### {bucket_name}")
            c1, c2 = st.columns(2)

            with c1:
                st.caption("Correlation matrix vs all goals")
                if not cols_list:
                    st.caption("No variables in this bucket for the current dataset.")
                else:
                    mat = corr_matrix(df_r_w, cols_list, goal_cols)
                    if mat.empty or mat.isna().all().all():
                        st.info("Not enough data to compute correlations.")
                    else:
                        st.plotly_chart(heatmap_fig_from_matrix(mat), use_container_width=True)

            with c2:
                st.caption(f"Δ correlation vs previous window (goal: {nice(goal_sel_col)})")
                if df_prev_w.empty or not cols_list:
                    st.info("Previous timeframe empty or no variables.")
                else:
                    cur  = corr_with_target_safe(df_r_w,    cols_list, goal_sel_col)
                    prev = corr_with_target_safe(df_prev_w, cols_list, goal_sel_col)

                    joined = cur.join(prev, how="outer", lsuffix="_cur", rsuffix="_prev").fillna(np.nan)
                    if "corr_cur" not in joined or "corr_prev" not in joined or joined.empty:
                        st.info("Not enough data to compute changes.")
                    else:
                        joined["delta"] = joined["corr_cur"] - joined["corr_prev"]

                        # robustly capture the index name after reset_index()
                        disp = joined.reset_index()
                        idx_col = "col" if "col" in disp.columns else ("index" if "index" in disp.columns else disp.columns[0])
                        disp = disp.rename(columns={idx_col: "Variable"})
                        disp["Variable_nice"] = disp["Variable"].apply(nice)

                        disp = disp.sort_values("delta", ascending=True)
                        colors = disp["delta"].apply(lambda x: "#2e7d32" if x >= 0 else "#a94442")

                        figd = go.Figure(
                            go.Bar(
                                x=disp["delta"],
                                y=disp["Variable_nice"],
                                orientation="h",
                                marker_color=colors,
                                customdata=np.stack([disp["corr_cur"], disp["corr_prev"]], axis=1),
                                hovertemplate="Δr: %{x:.2f}<br>Current r: %{customdata[0]:.2f}<br>Prev r: %{customdata[1]:.2f}<extra></extra>",
                            )
                        )
                        figd.update_layout(
                            xaxis=dict(title="Δr (current - previous)", range=[-1, 1], zeroline=True),
                            yaxis=dict(title=""),
                            bargap=0.2,
                        )
                        st.plotly_chart(figd, use_container_width=True)

            # Metrics table (per variable → R² / MAE(rel) / Spearman ρ)
            st.caption("Per-variable signal (quadratic fit)")
            if cols_list:
                tbl = metrics_table_for_bucket(df_r_w, cols_list, goal_sel_col)
                if tbl.empty:
                    st.info("Not enough data points to compute per-variable metrics.")
                else:
                    st.dataframe(tbl, hide_index=True, use_container_width=True)
            else:
                st.caption("—")
            st.markdown("---")

# =============================
# TAB 2 — COLLINEARITY & PCA 
# =============================
with tab_diag:
    # Local imports
    try:
        from sklearn.decomposition import PCA
    except Exception:
        st.error("Missing scikit-learn component: `sklearn.decomposition.PCA`.")

    st.subheader("Collinearity & PCA — Overall → Adjust → Details")

    # ---------- UI CSS: tooltips; scope wide dropdown ONLY to adjust section ----------
    st.markdown(
        """
    <style>
      /* Horizontal hint badges with reliable hover tips */
      .hintrow{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:.25rem 0 .5rem 0}
      .hbadge{position:relative;display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#444;background:#fafafa;border:1px solid #eee;border-radius:999px;padding:6px 10px}
      .hbadge::after{
        content: attr(data-tip);
        position:absolute; bottom:135%; left:50%; transform:translateX(-50%);
        background:#111; color:#fff; padding:8px 10px; border-radius:6px; font-size:12px; line-height:1.35;
        white-space:normal; max-width:520px; min-width:160px; z-index:9999; display:none;
      }
      .hbadge:hover::after{display:block}
      .idot{border:1px solid #999;border-radius:50%;width:16px;height:16px;display:inline-flex;align-items:center;justify-content:center;font-size:12px;color:#666}

      /* Scope wider multiselect ONLY inside #adjust-picker container */
      #adjust-picker .stMultiSelect [role="combobox"]{min-width:640px}
      #adjust-picker div[data-baseweb="select"] > div{min-width:640px}
      #adjust-picker div[data-baseweb="popover"] { width: 660px; max-width: 660px; }
      #adjust-picker div[data-baseweb="menu"] { width: 660px; max-width: 660px; }

      /* Tighter dataframes */
      .stDataFrame table td, .stDataFrame table th { white-space: nowrap; }
    </style>
    """,
        unsafe_allow_html=True,
    )

    def htip(label, text):
        st.markdown(
            f"""<span class="hbadge" data-tip="{text}">{label}<span class="idot">i</span></span>""",
            unsafe_allow_html=True,
        )

    # ---------- Exclude MAIN goals via metadata (no other functional changes) ----------
    try:
        goals_to_exclude = (
            set(
                m.loc[m[CAT].str.lower().eq("goal"), COL]
                .dropna()
                .astype(str)
                .tolist()
            )
            if (CAT in m.columns and COL in m.columns)
            else set()
        )
    except Exception:
        goals_to_exclude = (
            set(meta_goals_main) if "meta_goals_main" in locals() else set()
        )

    # ---------- Helpers ----------
    def _prepare_X(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
        if not cols:
            return pd.DataFrame()
        X = frame[cols].apply(pd.to_numeric, errors="coerce")
        X = X.dropna(axis=1, how="all").fillna(0.0)
        nun = X.nunique(dropna=False)
        X = X[nun[nun > 1].index.tolist()]
        std = X.std(ddof=0)
        return X[std[std > 1e-12].index.tolist()]

    def _condition_number(X: pd.DataFrame):
        """
        Cond# = (largest singular value) / (smallest singular value) on standardized X (σmax/σmin from SVD).
        Not a count; can be > #Vars; lower is better. Rough bands: <15 low, 15–30 medium, >30 high.
        """
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

    def _vif_table(X: pd.DataFrame):
        vars_ = X.columns.tolist()
        if len(vars_) < 2:
            return pd.DataFrame(
                {"variable": vars_, "VIF": [np.nan] * len(vars_)}
            )
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
        return pd.DataFrame(out, columns=["variable", "VIF"]).sort_values(
            "VIF", ascending=False
        )

    def _pca_summary(X: pd.DataFrame, var_target: float = 0.80):
        if X.shape[1] < 2:
            return dict(
                n_components=0,
                var_ratio=[],
                cum_ratio=[],
                loadings=pd.DataFrame(),
            )
        Xs = (X - X.mean(0)) / X.std(0).replace(0, 1)
        Xs = Xs.replace([np.inf, -np.inf], 0.0).values
        try:
            pca = PCA().fit(Xs)
            vr = pca.explained_variance_ratio_.tolist()
            cum = np.cumsum(vr).tolist()
            k = next(
                (i + 1 for i, c in enumerate(cum) if c >= var_target), len(cum)
            )
            load = pd.DataFrame(pca.components_, columns=list(X.columns))
            load.index = [f"PC{i+1}" for i in range(load.shape[0])]
            return dict(
                n_components=k, var_ratio=vr, cum_ratio=cum, loadings=load
            )
        except Exception:
            return dict(
                n_components=np.nan,
                var_ratio=[],
                cum_ratio=[],
                loadings=pd.DataFrame(),
            )

    def _bucket_map():
        return {
            "Paid Spend": [
                c
                for c in (paid_spend_cols or [])
                if c in df.columns and c not in goals_to_exclude
            ],
            "Paid Media Vars": [
                c
                for c in (paid_var_cols or [])
                if c in df.columns and c not in goals_to_exclude
            ],
            "Organic Vars": [
                c
                for c in (organic_cols or [])
                if c in df.columns and c not in goals_to_exclude
            ],
            "Context Vars": [
                c
                for c in (context_cols or [])
                if c in df.columns and c not in goals_to_exclude
            ],
        }

    def _all_drivers():
        b = _bucket_map()
        return [c for c in dict.fromkeys(sum(b.values(), []))]

    def _vif_band(v: float):
        if not np.isfinite(v):
            return "–"
        return "🟢" if v < 5 else ("🟡" if v < 7.5 else "🔴")

    def _pca_band_wholeset(pcs_needed: int, n_vars: int):
        """
        PCA band for the country-level whole set: PCs@80% relative to #Vars.
        ≤30% → 🟢 ; 30–60% → 🟡 ; >60% → 🔴
        """
        if n_vars <= 0 or not np.isfinite(pcs_needed):
            return "–"
        r = pcs_needed / max(1, n_vars)
        return "🟢" if r <= 0.30 else ("🟡" if r <= 0.60 else "🔴")

    # ------------------- CONFIGURATIONS (collapsible) -------------------
    with st.expander("Configurations", expanded=False):
        countries_all = (
            sorted(df["COUNTRY"].dropna().astype(str).unique())
            if "COUNTRY" in df
            else []
        )
        c1, c2, c3 = st.columns([1.6, 1.0, 1.0])
        with c1:
            sel_ctry = st.multiselect(
                "Countries",
                options=countries_all,
                default=countries_all,
                help="Per-country analysis.",
            )
        with c2:
            vif_label_to_val = {
                "5.0 — strict / low tolerance": 5.0,
                "7.5 — balanced / medium": 7.5,
                "10.0 — lenient / high tolerance": 10.0,
            }
            sel_vif_label = st.selectbox(
                "VIF flag threshold", list(vif_label_to_val.keys()), index=1
            )
            vif_thr = float(vif_label_to_val[sel_vif_label])
        with c3:
            st.caption("PCA coverage fixed at **80%** for comparability.")

    if not sel_ctry:
        st.info("Select at least one country to run diagnostics.")
        st.stop()

    # ---- Inline help (horizontal) ----
    st.markdown('<div class="hintrow">', unsafe_allow_html=True)
    htip(
        "Cond#",
        "Computed on standardized X as σ_max/σ_min (SVD). Not a count; lower is better. Rough bands: <15 low, 15–30 medium, >30 high.",
    )
    htip(
        f"VIF>{vif_thr}",
        "How many variables in that country are highly redundant with the rest.",
    )
    htip(
        "PCs @ 80%",
        "How many latent patterns are needed to capture ~80% of movement across drivers.",
    )
    htip(
        "#Vars",
        "Columns that survived basic cleaning (constant/empty dropped).",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    # =========================================================
    # 1) OVERALL DATASET — country summary + (collapsed) bucket details
    # =========================================================
    st.markdown("### 1) Overall ratings (all drivers) — per country")

    var_target_num = 0.80
    buckets = _bucket_map()
    all_drivers = _all_drivers()

    bench_rows = []
    overall_vif_by_country = {}

    for ctry in sel_ctry:
        dct = (
            df[df["COUNTRY"].astype(str).eq(ctry)]
            if "COUNTRY" in df
            else df.copy()
        )
        X_all = _prepare_X(dct, all_drivers)
        cn = _condition_number(X_all)
        vif_df = _vif_table(X_all)
        overall_vif_by_country[ctry] = (
            vif_df.copy()
            if not vif_df.empty
            else pd.DataFrame(columns=["variable", "VIF"])
        )
        high_vif = (
            int((vif_df["VIF"] > float(vif_thr)).sum())
            if not vif_df.empty
            else 0
        )
        pca_s_all = _pca_summary(X_all, var_target=var_target_num)
        bench_rows.append(
            dict(
                Country=ctry,
                Vars=X_all.shape[1],
                ConditionNo=(round(cn, 1) if np.isfinite(cn) else np.nan),
                VIF_Flags=high_vif,
                PCA_k=pca_s_all["n_components"],
                PCA_Band=_pca_band_wholeset(
                    pca_s_all["n_components"], X_all.shape[1]
                ),
                CondBand=(
                    "🟢 Low"
                    if (np.isfinite(cn) and cn < 15)
                    else (
                        "🟡 Medium"
                        if (np.isfinite(cn) and cn < 30)
                        else ("🔴 High" if np.isfinite(cn) else "–")
                    )
                ),
            )
        )

    bench_all = pd.DataFrame(bench_rows).sort_values(
        ["VIF_Flags", "ConditionNo"], ascending=[False, False]
    )
    disp_all = bench_all.rename(
        columns={
            "Vars": "#Vars",
            "ConditionNo": "Cond#",
            "VIF_Flags": f"VIF>{vif_thr}",
            "PCA_k": "PCs @ 80%",
            "PCA_Band": "PCA Band",
        }
    )
    st.dataframe(
        disp_all[
            [
                "Country",
                "#Vars",
                "Cond#",
                "CondBand",
                f"VIF>{vif_thr}",
                "PCs @ 80%",
                "PCA Band",
            ]
        ],
        use_container_width=True,
    )

    # Bucket variable tables (overall run) — COLLAPSIBLE
    with st.expander(
        "Bucket view — variables & VIF (overall run)", expanded=False
    ):
        bucket_names = list(buckets.keys())
        cols_pair = st.columns(2)
        for i, bucket_name in enumerate(bucket_names):
            with cols_pair[i % 2]:
                st.markdown(f"**{bucket_name}**")
                rows = []
                for ctry in sel_ctry:
                    vdf = overall_vif_by_country.get(ctry)
                    if vdf is None or vdf.empty:
                        continue
                    subset = vdf[
                        vdf["variable"].isin(buckets[bucket_name])
                    ].copy()
                    if subset.empty:
                        continue
                    subset["Country"] = ctry
                    subset["Variable"] = subset["variable"].map(nice)
                    subset["VIF Band"] = subset["VIF"].apply(_vif_band)
                    subset["VIF"] = subset["VIF"].map(
                        lambda v: f"{v:.2f}" if np.isfinite(v) else "–"
                    )
                    rows.append(
                        subset[["Country", "Variable", "VIF", "VIF Band"]]
                    )
                if rows:
                    out = (
                        pd.concat(rows, axis=0)
                        .sort_values(["Country", "Variable"])
                        .copy()
                    )
                    st.dataframe(out, hide_index=True, use_container_width=True)
                else:
                    st.info(
                        "No variables available in this bucket for the selected countries."
                    )
        st.caption(
            "Legend: VIF bands — 🟢 <5 (OK), 🟡 5–7.5 (Watch), 🔴 ≥7.5 (Flag). Spend variables are **not** auto-suggested for removal."
        )

    # Suggestions: group by severity (spend excluded) — COLLAPSIBLE
    with st.expander(
        "Suggested removals (grouped by severity; spend excluded)",
        expanded=False,
    ):
        for ctry in sel_ctry:
            st.markdown(f"**{ctry}**")
            vdf = overall_vif_by_country.get(ctry)
            if vdf is None or vdf.empty:
                st.info("No suggestion (not enough variables).")
                continue
            spend_set = set(paid_spend_cols or [])
            pruned = vdf[~vdf["variable"].isin(spend_set)].copy()
            if pruned.empty:
                st.info(
                    "All top offenders are spend variables. Keep them, or consider bundling correlated spends."
                )
                continue
            pruned["Variable"] = pruned["variable"].map(nice)
            pruned["Band"] = pruned["VIF"].apply(
                lambda v: (
                    "Strong (🔴 ≥10)"
                    if v >= 10
                    else (
                        "Moderate (🟡 7.5–10)"
                        if v >= 7.5
                        else ("Mild (🟢 5–7.5)" if v >= 5 else "OK (<5)")
                    )
                )
            )
            pruned["VIF"] = pruned["VIF"].map(
                lambda v: f"{v:.2f}" if np.isfinite(v) else "–"
            )
            for band in [
                "Strong (🔴 ≥10)",
                "Moderate (🟡 7.5–10)",
                "Mild (🟢 5–7.5)",
            ]:
                seg = pruned[pruned["Band"] == band][
                    ["Variable", "VIF", "Band"]
                ]
                if seg.empty:
                    continue
                st.markdown(f"*{band}*")
                st.dataframe(
                    seg.sort_values("VIF", ascending=False),
                    hide_index=True,
                    use_container_width=True,
                )

    st.markdown("---")

    # =========================================================
    # 2) ADJUST VARIABLE SELECTION → RE-SCORE (sorted picker + drop suggested + download)
    # =========================================================
    st.markdown("### 2) Adjust drivers & re-score (what-if)")

    # Persist manual selection; default to all drivers
    if "diag_selected_drivers_v9" not in st.session_state:
        st.session_state["diag_selected_drivers_v9"] = _all_drivers().copy()

    # Build (nice, raw) map sorted by nice
    nice_raw_pairs = sorted(
        [(nice(c), c) for c in _all_drivers()], key=lambda t: t[0].lower()
    )
    pick_options = [nr[0] for nr in nice_raw_pairs]
    nice_to_raw = {nr[0]: nr[1] for nr in nice_raw_pairs}

    default_nice = [
        nice(c)
        for c in st.session_state["diag_selected_drivers_v9"]
        if c in nice_to_raw.values()
    ]
    default_nice = sorted(default_nice, key=lambda s: s.lower())

    st.markdown('<div id="adjust-picker">', unsafe_allow_html=True)
    sel = st.multiselect(
        "Include these variables",
        options=pick_options,
        default=default_nice,
        help="Refine columns for a MMM-ready set. Alphabetically sorted; long names fully visible.",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    st.session_state["diag_selected_drivers_v9"] = [
        nice_to_raw[n] for n in sel if n in nice_to_raw
    ]
    drivers_sel = st.session_state["diag_selected_drivers_v9"]

    # Compute combined VIF (across selected countries) for action buttons
    strong_to_drop, mod_to_drop, mild_to_drop = [], [], []
    if drivers_sel:
        d_comb = (
            df[df["COUNTRY"].astype(str).isin(sel_ctry)]
            if "COUNTRY" in df
            else df.copy()
        )
        X_comb = _prepare_X(d_comb, drivers_sel)
        vif_comb = _vif_table(X_comb)
        if not vif_comb.empty:
            spend_set = set(paid_spend_cols or [])
            for _, row in vif_comb.iterrows():
                var, v = row["variable"], row["VIF"]
                if var in spend_set or not np.isfinite(v):
                    continue
                if v >= 10:
                    strong_to_drop.append(var)
                elif v >= 7.5:
                    mod_to_drop.append(var)
                elif v >= 5:
                    mild_to_drop.append(var)

    # Buttons (vertical)
    if drivers_sel:
        if strong_to_drop:
            if st.button(
                f"Drop suggested: Strong (≥10) — {len(set(strong_to_drop))} vars"
            ):
                st.session_state["diag_selected_drivers_v9"] = [
                    c
                    for c in st.session_state["diag_selected_drivers_v9"]
                    if c not in set(strong_to_drop)
                ]
                st.rerun()
        if strong_to_drop or mod_to_drop:
            total_sm = len(set(strong_to_drop) | set(mod_to_drop))
            if st.button(
                f"Drop suggested: Strong + Moderate (≥7.5) — {total_sm} vars"
            ):
                to_drop = set(strong_to_drop) | set(mod_to_drop)
                st.session_state["diag_selected_drivers_v9"] = [
                    c
                    for c in st.session_state["diag_selected_drivers_v9"]
                    if c not in to_drop
                ]
                st.rerun()
        # NEW: Strict option (drops ≥5, i.e., strong + moderate + mild)
        if strong_to_drop or mod_to_drop or mild_to_drop:
            total_strict = len(
                set(strong_to_drop) | set(mod_to_drop) | set(mild_to_drop)
            )
            if st.button(f"Drop suggested: Strict (≥5) — {total_strict} vars"):
                to_drop = (
                    set(strong_to_drop) | set(mod_to_drop) | set(mild_to_drop)
                )
                st.session_state["diag_selected_drivers_v9"] = [
                    c
                    for c in st.session_state["diag_selected_drivers_v9"]
                    if c not in to_drop
                ]
                st.rerun()
    else:
        st.info(
            "No variables selected. Pick at least one to compute what-if scores."
        )

    # What-if re-score per country (auto refresh on change)
    bench2 = None
    if drivers_sel:
        bench_rows2 = []
        for ctry in sel_ctry:
            dct = (
                df[df["COUNTRY"].astype(str).eq(ctry)]
                if "COUNTRY" in df
                else df.copy()
            )
            Xs = _prepare_X(dct, drivers_sel)
            cn2 = _condition_number(Xs)
            vif2 = _vif_table(Xs)
            flags2 = (
                int((vif2["VIF"] > float(vif_thr)).sum())
                if not vif2.empty
                else 0
            )
            pca2 = _pca_summary(Xs, var_target=var_target_num)
            bench_rows2.append(
                dict(
                    Country=ctry,
                    Vars=Xs.shape[1],
                    ConditionNo=(round(cn2, 1) if np.isfinite(cn2) else np.nan),
                    VIF_Flags=flags2,
                    PCA_k=pca2["n_components"],
                    PCA_Band=_pca_band_wholeset(
                        pca2["n_components"], Xs.shape[1]
                    ),
                    CondBand=(
                        "🟢 Low"
                        if (np.isfinite(cn2) and cn2 < 15)
                        else (
                            "🟡 Medium"
                            if (np.isfinite(cn2) and cn2 < 30)
                            else ("🔴 High" if np.isfinite(cn2) else "–")
                        )
                    ),
                )
            )
        bench2 = pd.DataFrame(bench_rows2).sort_values(
            ["VIF_Flags", "ConditionNo"], ascending=[False, False]
        )
        disp2 = bench2.rename(
            columns={
                "Vars": "#Vars",
                "ConditionNo": "Cond#",
                "VIF_Flags": f"VIF>{vif_thr}",
                "PCA_k": "PCs @ 80%",
                "PCA_Band": "PCA Band",
            }
        )
        st.dataframe(
            disp2[
                [
                    "Country",
                    "#Vars",
                    "Cond#",
                    "CondBand",
                    f"VIF>{vif_thr}",
                    "PCs @ 80%",
                    "PCA Band",
                ]
            ],
            use_container_width=True,
        )

    # ---- Download current variable selection as CSV ----
    if drivers_sel:
        import io

        sel_df = pd.DataFrame({"variable": drivers_sel})
        csv_buf = io.StringIO()
        sel_df.to_csv(csv_buf, index=False)
        st.download_button(
            "Download variable selection (CSV)",
            data=csv_buf.getvalue(),
            file_name="mmm_variable_selection.csv",
            mime="text/csv",
            help="Exports the current feature set you'll take into Robyn / experiments.",
        )

    st.markdown("---")

    # =========================================================
    # 3) COUNTRY DETAILS — MIRRORS SECTION 1, uses CURRENT SELECTION
    # =========================================================
    mode_note = (
        "Adjusted selection from Section 2"
        if drivers_sel
        else "All drivers (no adjusted selection)"
    )
    st.markdown(
        f"### 3) Country details — mirrored tables (using: **{mode_note}**)"
    )

    active_cols = drivers_sel if drivers_sel else all_drivers

    # Build mirrored summary first (country-level)
    detail_rows = []
    detail_vif_by_country = {}

    for ctry in sel_ctry:
        dct = (
            df[df["COUNTRY"].astype(str).eq(ctry)]
            if "COUNTRY" in df
            else df.copy()
        )
        Xd = _prepare_X(dct, active_cols)
        cn = _condition_number(Xd)
        vif_df = _vif_table(Xd)
        detail_vif_by_country[ctry] = (
            vif_df.copy()
            if not vif_df.empty
            else pd.DataFrame(columns=["variable", "VIF"])
        )
        high_vif = (
            int((vif_df["VIF"] > float(vif_thr)).sum())
            if not vif_df.empty
            else 0
        )
        pca_s = _pca_summary(Xd, var_target=var_target_num)
        detail_rows.append(
            dict(
                Country=ctry,
                Vars=Xd.shape[1],
                ConditionNo=(round(cn, 1) if np.isfinite(cn) else np.nan),
                VIF_Flags=high_vif,
                PCA_k=pca_s["n_components"],
                PCA_Band=_pca_band_wholeset(pca_s["n_components"], Xd.shape[1]),
                CondBand=(
                    "🟢 Low"
                    if (np.isfinite(cn) and cn < 15)
                    else (
                        "🟡 Medium"
                        if (np.isfinite(cn) and cn < 30)
                        else ("🔴 High" if np.isfinite(cn) else "–")
                    )
                ),
            )
        )

    detail_sum = pd.DataFrame(detail_rows).sort_values(
        ["VIF_Flags", "ConditionNo"], ascending=[False, False]
    )
    detail_disp = detail_sum.rename(
        columns={
            "Vars": "#Vars",
            "ConditionNo": "Cond#",
            "VIF_Flags": f"VIF>{vif_thr}",
            "PCA_k": "PCs @ 80%",
            "PCA_Band": "PCA Band",
        }
    )
    st.dataframe(
        detail_disp[
            [
                "Country",
                "#Vars",
                "Cond#",
                "CondBand",
                f"VIF>{vif_thr}",
                "PCs @ 80%",
                "PCA Band",
            ]
        ],
        use_container_width=True,
    )

    # Bucket variable tables (current selection) — COLLAPSIBLE
    with st.expander(
        "Bucket view — variables & VIF (current selection)", expanded=False
    ):
        cols_pair2 = st.columns(2)
        for i, bucket_name in enumerate(buckets.keys()):
            with cols_pair2[i % 2]:
                st.markdown(f"**{bucket_name}**")
                rows = []
                for ctry in sel_ctry:
                    vdf = detail_vif_by_country.get(ctry)
                    if vdf is None or vdf.empty:
                        continue
                    subset = vdf[
                        vdf["variable"].isin(buckets[bucket_name])
                    ].copy()
                    if subset.empty:
                        continue
                    subset["Country"] = ctry
                    subset["Variable"] = subset["variable"].map(nice)
                    subset["VIF Band"] = subset["VIF"].apply(_vif_band)
                    subset["VIF"] = subset["VIF"].map(
                        lambda v: f"{v:.2f}" if np.isfinite(v) else "–"
                    )
                    rows.append(
                        subset[["Country", "Variable", "VIF", "VIF Band"]]
                    )
                if rows:
                    out = (
                        pd.concat(rows, axis=0)
                        .sort_values(["Country", "Variable"])
                        .copy()
                    )
                    st.dataframe(out, hide_index=True, use_container_width=True)
                else:
                    st.info(
                        "No variables available in this bucket for the current selection."
                    )
 
 # =============================
# TAB 3 — DEEP DIVE (Individual Driver)
# =============================
with tab_deep:
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    st.subheader("Deep Dive — Individual Driver")
    st.caption("Quick, non-causal curve fit to gauge whether a driver has enough signal to justify inclusion/engineering in a future MMM.")

    # --- Local winsorizer (self-contained; same behavior as Tab 1) ---
    def winsorize_columns(frame: pd.DataFrame, cols: list, mode: str, pct: int) -> pd.DataFrame:
        if mode == "None" or not cols:
            return frame
        dfw = frame.copy()
        upper_q = pct / 100.0
        lower_q = 1 - upper_q if mode == "Both" else None
        for c in cols:
            if c not in dfw.columns:
                continue
            s = pd.to_numeric(dfw[c], errors="coerce")
            if s.notna().sum() == 0:
                continue
            hi = s.quantile(upper_q)
            if mode == "Upper":
                dfw[c] = np.where(s > hi, hi, s)
            else:
                lo = s.quantile(lower_q)
                dfw[c] = s.clip(lower=lo, upper=hi)
        return dfw

    # --- Conditioning controls (independent of Tab 1) ---
    st.markdown("##### Data Conditioning")
    wins_mode_label = st.selectbox(
        "Winsorize mode",
        ["No", "Upper only", "Upper and lower"],
        index=0,
        key="deep_wins_mode",
        help="Remove outliers",
    )
    _mode_map = {"No": "None", "Upper only": "Upper", "Upper and lower": "Both"}
    wins_mode = _mode_map[wins_mode_label]
    if wins_mode != "None":
        wins_pct_label = st.selectbox(
            "Winsorize level (keep up to this percentile)",
            ["99", "98", "95"],
            index=0,
            key="deep_wins_pct",
        )
        wins_pct = int(wins_pct_label)
    else:
        wins_pct = 99

    # --- Build driver universe (robust; does not rely on Tab 1 locals) ---
    # Explicit buckets (already computed above this tabs section)
    _paid_spend = [c for c in paid_spend_cols if c in df.columns]
    _paid_vars  = [c for c in paid_var_cols   if c in df.columns]
    _organic    = [c for c in organic_cols    if c in df.columns]
    _context    = [c for c in context_cols    if c in df.columns]

    # Also include "other drivers" = numeric columns not in the above nor goals/date/country/etc.
    EXCLUDE = set(_paid_spend + _paid_vars + _organic + _context + (goal_cols or []))
    for col in ["DATE", DATE_COL, "COUNTRY", "DATE_PERIOD", "PERIOD_LABEL", "_TOTAL_SPEND"]:
        if col in df.columns:
            EXCLUDE.add(col)
    numeric_df = df.select_dtypes(include=[np.number]).copy()
    _other = [c for c in numeric_df.columns if c not in EXCLUDE]

    # Driver candidates present in current filtered window
    driver_all = sorted(
        dict.fromkeys([c for c in (_paid_spend + _paid_vars + _organic + _context + _other) if c in df_r.columns])
    )

    # Winsorize target + drivers used in this tab
    all_corr_cols = list(set(driver_all + (goal_cols or [])))
    df_r_w = winsorize_columns(df_r, all_corr_cols, wins_mode, wins_pct)

    if not goal_cols:
        st.info("No goals found in metadata.")
    elif not driver_all:
        st.info("No driver columns available.")
    else:
        # Selections
        y_goal_label = st.selectbox(
            "Goal (Y)",
            [nice(g) for g in goal_cols],
            index=0,
            key="deep_goal",
        )
        y_col = {nice(g): g for g in goal_cols}[y_goal_label]

        x_driver_label = st.selectbox(
            "Driver (X)",
            [nice(c) for c in driver_all if c != y_col],
            index=0,
            key="deep_drv",
        )
        x_col = {nice(c): c for c in driver_all}[x_driver_label]

        exclude_zero = st.checkbox(
            "Exclude zero values for driver",
            value=(x_col.upper() != "TV_IS_ON"),
            key="deep_exz",
        )
        outlier_method = st.selectbox(
            "Outlier handling",
            ["none", "percentile (top only)", "zscore (<3)"],
            index=0,
            key="deep_outlier",
            help="Applies after the (optional) winsorization above. Percentile drops top ~2% of X; z-score drops |z(X)| ≥ 3.",
        )

        # Filter + outliers
        x_raw = pd.to_numeric(df_r_w[x_col], errors="coerce")
        y_raw = pd.to_numeric(df_r_w[y_col], errors="coerce")
        mask = x_raw.notna() & y_raw.notna()
        if exclude_zero and x_col.upper() != "TV_IS_ON":
            mask &= x_raw != 0
        x_f = x_raw[mask].copy()
        y_f = y_raw[mask].copy()

        if outlier_method.startswith("percentile") and len(x_f) >= 2:
            upper = np.percentile(x_f, 98)
            keep = x_f <= upper
            x_f, y_f = x_f[keep], y_f[keep]
        elif outlier_method.startswith("zscore") and len(x_f) >= 2:
            z = np.abs(stats.zscore(x_f))
            keep = z < 3
            x_f, y_f = x_f[keep], y_f[keep]

        if len(x_f) < 5:
            st.info("Not enough data points after filtering to fit a curve.")
        else:
            # Quadratic fit
            X = np.array(x_f).reshape(-1, 1)
            poly = PolynomialFeatures(degree=2)
            Xp = poly.fit_transform(X)
            mdl = LinearRegression().fit(Xp, y_f)
            y_hat = mdl.predict(Xp)

            # Metrics
            r2 = r2_score(y_f, y_hat)
            mae = mean_absolute_error(y_f, y_hat)

            # Normalize MAE to goal scale (5th–95th pct)
            if y_f.nunique() > 1:
                y_p5, y_p95 = np.percentile(y_f, [5, 95])
                y_scale = max(y_p95 - y_p5, 1e-9)
            else:
                y_scale = max(float(y_f.max() - y_f.min()), 1e-9)
            nmae = mae / y_scale

            # Spearman rank correlation (ρ)
            rho, _ = stats.spearmanr(x_f, y_f, nan_policy="omit")

            # --- Tiny style helpers (local) ---
            SCORE_COLORS = {
                "green": "#2e7d32",
                "yellow": "#f9a825",
                "red": "#a94442",
                "bg": "#fafafa",
                "ink": "#222",
            }

            def score_bar_metric_first(metric_title: str, tooltip_text: str, value_txt: str, level: str, percent: int):
                def hex_to_rgba(hex_color: str, alpha: float) -> str:
                    h = hex_color.lstrip("#")
                    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                    return f"rgba({r},{g},{b},{alpha})"
                pct_css = max(percent, 6)
                color = SCORE_COLORS[level]
                title_html = (
                    f'<span style="font-size:18px;font-weight:700;color:{SCORE_COLORS["ink"]};display:inline-flex;align-items:center;">'
                    f"{metric_title}"
                    f'<span class="tooltip-wrap"><span class="i-dot">i</span>'
                    f'<span class="tooltip-text">{tooltip_text}</span></span></span>'
                )
                html = (
                    f'<div style="border:1px solid #eee;border-radius:12px;padding:14px;background:{SCORE_COLORS["bg"]};">'
                    f'<div style="display:flex;justify-content:space-between;align-items:baseline;">'
                    f"{title_html}"
                    f'<div style="font-size:18px;font-weight:700;color:{SCORE_COLORS["ink"]};">{value_txt}</div>'
                    f"</div>"
                    f'<div style="margin-top:10px;height:16px;border-radius:999px;background:#eee;overflow:hidden;">'
                    f'<div style="width:{pct_css}%;height:100%;background:{color};"></div>'
                    f"</div></div>"
                )
                st.markdown(html, unsafe_allow_html=True)

            def classify_r2_card(val: float):
                if val is None or not np.isfinite(val): return ("yellow", "Insufficient data — treat with caution.")
                if val >= 0.35: return ("green", "Promising signal — likely meaningful; candidate for MMM.")
                if val >= 0.15: return ("yellow", "Some signal — consider with transforms/lags or as part of a bundle.")
                return ("red", "Weak/noisy — unlikely to add value without re-engineering.")

            def classify_mae_card(val: float):
                if val is None or not np.isfinite(val): return ("yellow", "Insufficient data — treat with caution.")
                if val <= 0.10: return ("green", "Average error small vs goal scale — usable for exploration.")
                if val <= 0.30: return ("yellow", "Average error moderate — interpret cautiously.")
                return ("red", "Average error large — not reliable for exploration.")

            def classify_rho_card(val: float):
                if val is None or not np.isfinite(val): return ("yellow", "Insufficient data — treat with caution.")
                s = abs(val)
                if s >= 0.35: return ("green", "Clear monotonic pattern in ranks — usable signal.")
                if s >= 0.15: return ("yellow", "Some monotonic pattern — consider with caution.")
                return ("red", "Weak/none — ranks move inconsistently.")

            TITLE_R2, TITLE_MAE, TITLE_RHO = ("R²", "MAE (relative)", "Spearman ρ")
            TIP_R2 = "Explained variance (fit strength) between driver and goal. Higher is better."
            TIP_MAE = "Average error vs goal’s typical scale (5th–95th pct). Smaller is better."
            TIP_RHO = "Monotonic rank correlation (strength & direction). Farther from 0 is stronger."

            def fill_from_r2(v):
                if v is None or not np.isfinite(v): return 50
                s = (v - (-0.2)) / (1.0 - (-0.2))  # map ~[-0.2..1.0] → 0..100
                return int(max(0, min(1, s)) * 100)

            def fill_from_mae(v):
                if v is None or not np.isfinite(v): return 50
                s = 1 - min(v / 0.30, 1.0)        # 0..0.30 → 100..0
                return int(max(0, min(1, s)) * 100)

            def fill_from_rho(v):
                if v is None or not np.isfinite(v): return 50
                return int(min(abs(v), 1.0) * 100)

            # Scorecards
            lvl_r2, msg_r2 = classify_r2_card(r2)
            lvl_mae, msg_mae = classify_mae_card(nmae)
            lvl_rho, msg_rho = classify_rho_card(rho if np.isfinite(rho) else np.nan)

            r2_txt  = f"{r2:.2f}" if np.isfinite(r2)  else "—"
            mae_txt = f"{nmae*100:.1f}%" if np.isfinite(nmae) else "—"
            rho_txt = f"{rho:+.2f}" if np.isfinite(rho) else "—"

            p_r2  = fill_from_r2(r2)
            p_mae = fill_from_mae(nmae)
            p_rho = fill_from_rho(rho if np.isfinite(rho) else np.nan)

            # One-time CSS (ok to repeat)
            st.markdown("""
<style>
.tooltip-wrap { position: relative; display: inline-flex; align-items: center; cursor: help; }
.tooltip-wrap .i-dot { font-size: 13px; border:1px solid #999; border-radius:50%; width:16px; height:16px; display:inline-flex; align-items:center; justify-content:center; color:#666; margin-left:6px; line-height:1; }
.tooltip-wrap .tooltip-text { visibility: hidden; opacity: 0; transition: opacity 0.08s ease-in-out;
  position: absolute; bottom: 125%; left: 50%; transform: translateX(-50%);
  background: #111; color: #fff; padding: 8px 10px; border-radius: 6px;
  font-size: 12px; z-index: 9999; box-shadow: 0 2px 8px rgba(0,0,0,0.25);
  max-width: 520px; width: max-content; white-space: normal; line-height: 1.35; overflow-wrap: anywhere; text-align: center; }
.tooltip-wrap:hover .tooltip-text { visibility: visible; opacity: 1; }
.tooltip-wrap .tooltip-text::after { content: ""; position: absolute; top: 100%; left: 50%;
  transform: translateX(-50%); border-width: 6px; border-style: solid; border-color: #111 transparent transparent transparent; }
</style>
            """, unsafe_allow_html=True)

            st.markdown("#### Model Fit — Scorecards (signal for MMM)")
            c1, c2, c3 = st.columns(3)
            with c1:
                score_bar_metric_first("R²", TIP_R2, r2_txt, lvl_r2, p_r2)
                st.caption(msg_r2)
            with c2:
                score_bar_metric_first("MAE (relative)", TIP_MAE, mae_txt, lvl_mae, p_mae)
                st.caption(msg_mae)
            with c3:
                score_bar_metric_first("Spearman ρ", TIP_RHO, rho_txt, lvl_rho, p_rho)
                if np.isfinite(rho):
                    direction = "positive" if rho > 0 else ("negative" if rho < 0 else "no")
                    st.caption(f"Ranks move in a {direction} monotonic pattern (ρ = {rho:+.2f}). {msg_rho}")
                else:
                    st.caption(msg_rho)

            # Fit visualization + marginal returns
            pcts = [10, 25, 50, 75, 90]
            x_pts = np.percentile(np.array(x_f), pcts)
            dydx  = mdl.coef_[1] + 2 * mdl.coef_[2] * x_pts
            y_pts = mdl.predict(poly.transform(x_pts.reshape(-1, 1)))

            xs = np.sort(np.array(x_f)).reshape(-1, 1)
            ys = mdl.predict(poly.transform(xs))

            figfit = go.Figure()
            figfit.add_trace(go.Scatter(x=x_f.values, y=y_f.values, mode="markers", name="Actual", opacity=0.45))
            figfit.add_trace(go.Scatter(x=xs.squeeze(), y=ys, mode="lines", name="Fitted Curve"))
            figfit.add_trace(go.Scatter(x=x_pts, y=y_pts, mode="markers+text", name="Percentiles",
                                        text=[f"{p}%" for p in pcts], textposition="top center"))
            figfit.update_layout(
                title=f"Fitted Curve for {nice(x_col)} → {nice(y_col)}",
                xaxis_title=nice(x_col),
                yaxis_title=nice(y_col),
            )
            st.plotly_chart(figfit, use_container_width=True)

            mr_tbl = pd.DataFrame({
                "Percentile": [f"{p}%" for p in pcts],
                "Driver value": [f"{float(v):.2f}" for v in x_pts],
                "Marginal return (dy/dx)": [f"{float(v):.4f}" for v in dydx],
            })
            st.dataframe(mr_tbl, hide_index=True, use_container_width=True)