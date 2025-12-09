import re
import warnings

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from app_shared import (
    build_meta_views,
    build_plat_map_df,
    filter_range,
    fmt_num,
    freq_to_rule,
    parse_date,
    period_label,
    pretty,
    previous_window,
    render_sidebar,
    resample_numeric,
    safe_eff,
    total_with_prev,
    validate_against_metadata,
)
from scipy import stats
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.preprocessing import PolynomialFeatures

# Authentication
require_login_and_domain()
ensure_session_defaults()

# ---- Pull state from the loader page ----
df = st.session_state.get("df", pd.DataFrame())
meta = st.session_state.get("meta", {}) or {}
DATE_COL = st.session_state.get("date_col", "DATE")
CHANNELS_MAP = st.session_state.get("channels_map", {}) or {}

if df.empty or not meta:
    st.stop()

# ---- Metadata-driven basics ----
DATE_COL = meta.get("data", {}).get("date_field", DATE_COL)
DEP_VAR = meta.get("dep_var")

# Initial helper build (for nice(), labels, etc.)
(
    display_map,
    nice,
    goal_cols_init,
    mapping_init,
    m,
    ALL_COLS_UP,
    IMPR_COLS,
    CLICK_COLS,
    SESSION_COLS,
    INSTALL_COLS,
) = build_meta_views(meta, df)

# ---- Sidebar (page-local controls) ----
GOAL, sel_countries, TIMEFRAME_LABEL, RANGE, agg_label, FREQ = render_sidebar(
    meta, df, nice, goal_cols_init
)

# Country filter
if sel_countries and "COUNTRY" in df.columns:
    df = df[df["COUNTRY"].astype(str).isin(sel_countries)].copy()

# Coerce numerics from metadata typing (more robust than dtypes)
for c, t in (meta.get("data_types") or {}).items():
    if t == "numeric" and c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

# Goals (authoritative from metadata, present in df)
goal_cols = [
    g["var"] for g in (meta.get("goals") or []) if g.get("var") in df.columns
]
dep_type = meta.get("dep_variable_type") or {}
goal_labels = {
    g: f'{g} [{dep_type.get(g, "").strip()}]'.strip(" []") for g in goal_cols
}

# Resolve target (prefer sidebar pick, else dep_var, else first goal)
target = None
if GOAL and GOAL in df.columns:
    target = GOAL
elif DEP_VAR and DEP_VAR in df.columns:
    target = DEP_VAR
elif goal_cols:
    target = goal_cols[0]

# -------------------------------
# Buckets from metadata (single source of truth)
# -------------------------------
mp = meta.get("mapping", {}) or {}
auto = meta.get("autotag_rules") or {}


def _expand_with_rules(explicit_list, rule_tokens, dfcols):
    """
    Combine explicit mapping with rule-based matches.
    Rule tokens are treated as case-insensitive 'contains' tokens.
    """
    dfu = [c for c in dfcols]  # preserve original case
    dfu_l = [c.lower() for c in dfu]
    out = set(c for c in (explicit_list or []) if c in dfu)
    for tok in rule_tokens or []:
        tok_l = tok.lower()
        for col, col_l in zip(dfu, dfu_l):
            if tok_l in col_l:
                out.add(col)
    return sorted(out)


# ---- Build driver buckets (mapping + prefix handling) ----
# Fix NameError: use mp (meta["mapping"]) instead of mapping

paid_spend_cols = [
    c for c in (mp.get("paid_media_spends") or []) if c in df.columns
]
paid_var_cols = [
    c for c in (mp.get("paid_media_vars") or []) if c in df.columns
]


# Strip prefixes like "ORGANIC_" or "CONTEXT_" before matching to df
def _strip_prefix(lst, prefix):
    prefix = prefix.lower()
    out = []
    for c in lst or []:
        c_stripped = re.sub(rf"^{prefix}", "", c, flags=re.IGNORECASE)
        if c_stripped in df.columns:
            out.append(c_stripped)
        elif c in df.columns:  # fallback to unstripped
            out.append(c)
    return sorted(set(out))


organic_cols = _strip_prefix(mp.get("organic_vars"), "ORGANIC_")
context_cols = _strip_prefix(mp.get("context_vars"), "CONTEXT_")
factor_cols = [c for c in (mp.get("factor_vars") or []) if c in df.columns]

# ---- De-dup organic/context against paid & factor ----
_exclude = set(paid_spend_cols + paid_var_cols + factor_cols)
organic_cols = [c for c in organic_cols if c not in _exclude]
context_cols = [c for c in context_cols if c not in _exclude]

# ---- Buckets ----
buckets = {
    "Paid Media Spend": paid_spend_cols,
    "Paid Media Variables": paid_var_cols,
    "Organic Variables": organic_cols,
    "Context Variables": context_cols,
    # "Factor Flags":       factor_cols,
}

# Total spend strictly from mapped spend cols
df["_TOTAL_SPEND"] = df[paid_spend_cols].sum(axis=1) if paid_spend_cols else 0.0

# ---- Columns used for correlations / winsorization (precompute candidates)
all_driver_cols = [c for group in buckets.values() for c in group]
all_corr_cols = sorted(set(all_driver_cols + goal_cols))
wins_cols = [c for c in all_corr_cols if c not in (factor_cols or [])]

# ---- Persist current selections for other pages (don’t overwrite local state) ----
st.session_state["RANGE"] = RANGE
st.session_state["FREQ"] = FREQ
st.session_state["GOAL"] = target
st.session_state["SEL_COUNTRIES"] = sel_countries

# ---- Windows from current sidebar values ----
RULE = freq_to_rule(FREQ)
df_r = filter_range(df.copy(), DATE_COL, RANGE)
df_prev = previous_window(df, df_r, DATE_COL, RANGE)


def total_with_prev_local(collist):
    return total_with_prev(df_r, df_prev, collist)


# -----------------------------
# Tabs
# -----------------------------
tab_rel, tab_diag, tab_deep = st.tabs(
    [
        "Explore Relationships",
        "Prepare Dataset for Modeling",
        "Deep Dive: Individual Driver",
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

    # ---- Use precomputed bucket lists — do NOT rebuild them here ----
    # buckets, paid_spend_cols, paid_var_cols, organic_cols, context_cols already exist

    # ---------------------------------
    # Winsorization (current & previous)
    # ---------------------------------
    df_r_w = winsorize_columns(df_r, all_corr_cols, wins_mode, wins_pct)
    df_prev_w = winsorize_columns(df_prev, all_corr_cols, wins_mode, wins_pct)

    # ----------
    # Helpers
    # ----------
    def _eligible_for_goal(
        frame: pd.DataFrame, x_col: str, y_col: str, min_n: int = 8
    ) -> bool:
        """Eligible if both present, enough non-NA pairs, variation, and x has >0 after NA drop."""
        if x_col not in frame or y_col not in frame:
            return False
        x = pd.to_numeric(frame[x_col], errors="coerce")
        y = pd.to_numeric(frame[y_col], errors="coerce")
        mask = x.notna() & y.notna()
        if mask.sum() < min_n:
            return False
        x_f, y_f = x[mask], y[mask]
        # Require variation
        if x_f.std(ddof=1) <= 0 or y_f.std(ddof=1) <= 0:
            return False
        # Require some non-zero X (so we don't display dead channels)
        if (x_f != 0).sum() == 0:
            return False
        return True

    def corr_matrix(
        frame: pd.DataFrame, drivers: list, goals: list, min_n: int = 8
    ) -> pd.DataFrame:
        """Compute r only for eligible columns; drop rows with all-NA at the end."""
        if frame.empty or not drivers or not goals:
            return pd.DataFrame()
        out_rows = []
        row_names = []
        col_names = [nice(g) for g in goals]
        for d in drivers:
            vals = []
            used = False
            for g in goals:
                if _eligible_for_goal(frame, d, g, min_n=min_n):
                    pair = (
                        frame[[d, g]]
                        .replace([np.inf, -np.inf], np.nan)
                        .dropna()
                    )
                    r = np.corrcoef(pair[d].values, pair[g].values)[0, 1]
                    vals.append(float(r))
                    used = True
                else:
                    vals.append(np.nan)
            if used:
                out_rows.append(vals)
                row_names.append(nice(d))
        if not out_rows:
            return pd.DataFrame()
        return pd.DataFrame(
            out_rows, index=row_names, columns=col_names, dtype=float
        )

    def corr_with_target_safe(frame, cols, tgt, min_n=8):
        rows = []
        if frame.empty or tgt not in frame:
            return pd.DataFrame(columns=["col", "corr"])
        for c in cols or []:
            if not _eligible_for_goal(frame, c, tgt, min_n=min_n):
                continue
            pair = (
                frame[[tgt, c]]
                .replace([np.inf, -np.inf], np.nan)
                .dropna()
                .copy()
            )
            r = np.corrcoef(pair[tgt].values, pair[c].values)[0, 1]
            if np.isfinite(r):
                rows.append((c, float(r)))
        return pd.DataFrame(rows, columns=["col", "corr"]).set_index("col")

    def _rel_var(x: pd.Series) -> float:
        """Relative variability on 5–95% goal-like scale to avoid CoV blow-ups."""
        x = pd.to_numeric(x, errors="coerce").dropna()
        if x.size < 2:
            return np.nan
        p5, p95 = np.percentile(x, [5, 95])
        scale = max(p95 - p5, 1e-9)
        return float(x.std(ddof=1) / scale)

    def _per_var_metrics(frame, x_col, y_col):
        """Return dict with R2, NMAE, Rho, n_pair (non-NA with X!=0), avg(X), relvar(X)."""
        x_raw = pd.to_numeric(frame[x_col], errors="coerce")
        y_raw = pd.to_numeric(frame[y_col], errors="coerce")
        mask = x_raw.notna() & y_raw.notna() & (x_raw != 0)
        x_f = x_raw[mask]
        y_f = y_raw[mask]
        n = int(len(x_f))
        if n < 5 or x_f.std(ddof=1) <= 0 or y_f.std(ddof=1) <= 0:
            return dict(
                R2=np.nan,
                NMAE=np.nan,
                Rho=np.nan,
                n=n,
                AVG=float(x_f.mean()) if n else np.nan,
                RV=_rel_var(x_f),
            )

        X = np.array(x_f).reshape(-1, 1)
        poly = PolynomialFeatures(degree=2)
        Xp = poly.fit_transform(X)
        mdl = LinearRegression().fit(Xp, y_f)
        y_hat = mdl.predict(Xp)

        r2 = r2_score(y_f, y_hat)
        mae = mean_absolute_error(y_f, y_hat)
        # relative MAE on 5–95% scale of Y
        if y_f.nunique() > 1:
            y_p5, y_p95 = np.percentile(y_f, [5, 95])
            y_scale = max(y_p95 - y_p5, 1e-9)
        else:
            y_scale = max(float(y_f.max() - y_f.min()), 1e-9)
        nmae = mae / y_scale

        rho, _ = stats.spearmanr(x_f, y_f, nan_policy="omit")
        return dict(
            R2=float(r2),
            NMAE=float(nmae),
            Rho=(float(rho) if np.isfinite(rho) else np.nan),
            n=n,
            AVG=float(x_f.mean()),
            RV=_rel_var(x_f),
        )

    def metrics_table_for_bucket(frame, cols, y_col):
        rows = []
        for c in cols or []:
            if not _eligible_for_goal(frame, c, y_col, min_n=8):
                continue
            m = _per_var_metrics(frame, c, y_col)
            rows.append(
                [
                    nice(c),
                    m["R2"],
                    m["NMAE"],
                    m["Rho"],
                    m["n"],
                    m["AVG"],
                    m["RV"],
                ]
            )
        out = pd.DataFrame(
            rows,
            columns=[
                "Variable",
                "R²",
                "MAE (rel)",
                "Spearman ρ",
                "n (X>0, pair)",
                "Avg(X)",
                "RelVar(X)",
            ],
        )
        if not out.empty:
            out = out.sort_values(["R²", "RelVar(X)"], ascending=[False, False])
        return out

    def as_pct_text(df_vals: pd.DataFrame) -> pd.DataFrame:
        return df_vals.applymap(
            lambda v: (f"{v*100:.1f}%" if pd.notna(v) else "")
        )

    def heatmap_fig_from_matrix(mat: pd.DataFrame, title=None, zmin=-1, zmax=1):
        """Render correlation heatmap with readable text, no undefined artefacts."""
        if mat is None or mat.empty:
            return go.Figure()

        mat = mat.sort_index(ascending=False)
        rows = max(1, mat.shape[0])
        height = min(120 + 26 * rows, 1100)
        tick_size = 12 if rows <= 25 else (10 if rows <= 45 else 9)

        text_vals = mat.applymap(
            lambda v: f"{v:.2f}" if pd.notna(v) else "\u00a0"
        )  # non-breaking space
        fig = go.Figure(
            go.Heatmap(
                z=mat.values,
                x=list(mat.columns),
                y=list(mat.index),
                zmin=zmin,
                zmax=zmax,
                colorscale="RdYlGn",
                text=text_vals.values,
                texttemplate="%{text}",
                hovertemplate="Driver: %{y}<br>Goal: %{x}<br>r: %{z:.2f}<extra></extra>",
                colorbar=dict(title="r"),
            )
        )
        fig.update_layout(
            title=title if title else None,
            xaxis=dict(side="top"),
            height=height,
            margin=dict(l=8, r=8, t=40, b=8),
            yaxis=dict(tickfont=dict(size=tick_size)),
        )
        return fig

    # ---------------------------------------------
    # Explore Relationships — per bucket UI layout
    # ---------------------------------------------
    st.markdown("### Explore Relationships — Per Driver Category")

    if not goal_cols:
        st.info("No goals found in metadata.")
    else:
        # Use the GOAL from the sidebar (resolved earlier to `target`) — no extra selector here
        goal_sel_col = target
        st.caption(f"Active goal (from sidebar): {nice(goal_sel_col)}")

        # Winsorize once per current/previous windows
        df_r_w = winsorize_columns(df_r, all_corr_cols, wins_mode, wins_pct)
        df_prev_w = winsorize_columns(
            df_prev, all_corr_cols, wins_mode, wins_pct
        )

        # helper functions (unchanged) are already defined above: _eligible_for_goal, corr_matrix, etc.

        for bucket_name, cols_list in buckets.items():
            st.markdown(f"#### {bucket_name}")

            # Eligible = any correlation to any goal in current window
            eligible_cols = []
            for c in cols_list or []:
                for g in goal_cols or []:
                    if _eligible_for_goal(df_r_w, c, g, min_n=8):
                        eligible_cols.append(c)
                        break

            # Sort variable list alphabetically ASC for readability; heatmap rows will be DESC per helper
            eligible_cols = sorted(
                set(eligible_cols), key=lambda x: nice(x).lower()
            )

            c1, c2 = st.columns(2)

            with c1:
                st.caption(
                    "Correlation matrix vs all goals (auto-pruned by availability/variation)"
                )
                if not eligible_cols:
                    st.caption(
                        "No variables in this bucket for the current timeframe."
                    )
                else:
                    mat = corr_matrix(df_r_w, eligible_cols, goal_cols)
                    if mat.empty or mat.isna().all().all():
                        st.info("Not enough data to compute correlations.")
                    else:
                        st.plotly_chart(
                            heatmap_fig_from_matrix(mat),
                            width="stretch",
                        )

            with c2:
                st.caption(
                    f"Δ correlation vs previous window (goal: {nice(goal_sel_col)})"
                )
                if df_prev_w.empty or not eligible_cols:
                    st.info(
                        "Previous timeframe empty or no eligible variables."
                    )
                else:
                    cur = corr_with_target_safe(
                        df_r_w, eligible_cols, goal_sel_col
                    )
                    prev = corr_with_target_safe(
                        df_prev_w, eligible_cols, goal_sel_col
                    )

                    joined = cur.join(
                        prev, how="outer", lsuffix="_cur", rsuffix="_prev"
                    ).fillna(np.nan)
                    # Ensure both cols exist before diff
                    if (
                        ("corr_cur" not in joined.columns)
                        or ("corr_prev" not in joined.columns)
                        or joined.empty
                    ):
                        st.info("Not enough data to compute changes.")
                    else:
                        joined["delta"] = (
                            joined["corr_cur"] - joined["corr_prev"]
                        )
                        disp = joined.reset_index().rename(
                            columns={"col": "Variable"}
                        )
                        disp["Variable_nice"] = disp["Variable"].apply(nice)
                        disp = disp.sort_values("delta", ascending=True)
                        colors = disp["delta"].apply(
                            lambda x: "#2e7d32" if x >= 0 else "#a94442"
                        )

                        figd = go.Figure(
                            go.Bar(
                                x=disp["delta"],
                                y=disp["Variable_nice"],
                                orientation="h",
                                marker_color=colors,
                                customdata=np.stack(
                                    [disp["corr_cur"], disp["corr_prev"]],
                                    axis=1,
                                ),
                                hovertemplate="Δr: %{x:.2f}<br>Current r: %{customdata[0]:.2f}<br>Prev r: %{customdata[1]:.2f}<extra></extra>",
                            )
                        )
                        bar_rows = max(1, disp.shape[0])
                        bar_h = min(120 + 24 * bar_rows, 900)
                        figd.update_layout(
                            xaxis=dict(
                                title="Δr (current - previous)",
                                range=[-1, 1],
                                zeroline=True,
                            ),
                            yaxis=dict(
                                title="",
                                tickfont=dict(
                                    size=(12 if bar_rows <= 25 else 10)
                                ),
                            ),
                            bargap=0.2,
                            height=bar_h,
                            margin=dict(l=8, r=8, t=20, b=8),
                        )
                        st.plotly_chart(figd, width="stretch")

            # Metrics table (adds Avg(X) & RelVar(X); n counts non-NA pairs with X>0)
            st.caption("Per-variable signal (quadratic fit)")
            if eligible_cols:
                tbl = metrics_table_for_bucket(
                    df_r_w, eligible_cols, goal_sel_col
                )
                if tbl.empty:
                    st.info(
                        "Not enough data points to compute per-variable metrics."
                    )
                else:
                    st.dataframe(
                        tbl.style.format(
                            {
                                "R²": "{:.2f}",
                                "MAE (rel)": "{:.1%}",
                                "Spearman ρ": "{:+.2f}",
                                "Avg(X)": "{:.2f}",
                                "RelVar(X)": "{:.2f}",
                            }
                        ),
                        hide_index=True,
                        width="stretch",
                    )
            else:
                st.caption("—")

            st.markdown("---")

# =============================
# TAB 2 — COLLINEARITY & PCA
# =============================
with tab_diag:
    # Local import
    try:
        from sklearn.decomposition import PCA
    except Exception:
        st.error("Missing scikit-learn component: `sklearn.decomposition.PCA`.")

    st.subheader("Collinearity & PCA — Overall → Adjust → Details")

    # ---------- UI CSS (tooltips + wide picker in section 2 only) ----------
    st.markdown(
        """
    <style>
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
      #adjust-picker .stMultiSelect [role="combobox"]{min-width:640px}
      #adjust-picker div[data-baseweb="select"] > div{min-width:640px}
      #adjust-picker div[data-baseweb="popover"] { width: 660px; max-width: 660px; }
      #adjust-picker div[data-baseweb="menu"] { width: 660px; max-width: 660px; }
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

    # ---------- Exclude goals (main + secondary) from drivers ----------
    goals_to_exclude = set(goal_cols or [])

    # ---------- Helpers ----------
    def _prepare_X(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
        if frame.empty or not cols:
            return pd.DataFrame()
        X = frame[[c for c in cols if c in frame.columns]].apply(
            pd.to_numeric, errors="coerce"
        )
        X = X.dropna(axis=1, how="all").fillna(0.0)
        nun = X.nunique(dropna=False)
        X = X[nun[nun > 1].index.tolist()]
        std = X.std(ddof=0)
        X = X[std[std > 1e-12].index.tolist()]
        return X

    def _condition_number(X: pd.DataFrame):
        """σ_max / σ_min on standardized X."""
        if X.shape[1] < 2:
            return np.nan
        Xs = (X - X.mean(0)) / X.std(0).replace(0, 1)
        Xs = Xs.replace([np.inf, -np.inf], 0.0).values
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
                if c in df_r.columns and c not in goals_to_exclude
            ],
            "Paid Media Vars": [
                c
                for c in (paid_var_cols or [])
                if c in df_r.columns and c not in goals_to_exclude
            ],
            "Organic Vars": [
                c
                for c in (organic_cols or [])
                if c in df_r.columns and c not in goals_to_exclude
            ],
            "Context Vars": [
                c
                for c in (context_cols or [])
                if c in df_r.columns and c not in goals_to_exclude
            ],
        }

    def _all_drivers():
        b = _bucket_map()
        # preserve order within buckets
        flat = []
        for k in [
            "Paid Spend",
            "Paid Media Vars",
            "Organic Vars",
            "Context Vars",
        ]:
            flat.extend(b.get(k, []))
        # de-dup preserve order
        seen = set()
        out = []
        for c in flat:
            if c not in seen:
                out.append(c)
                seen.add(c)
        return out

    def _vif_band(v: float):
        if not np.isfinite(v):
            return "–"
        return "🟢" if v < 5 else ("🟡" if v < 7.5 else "🔴")

    def _pca_band_wholeset(pcs_needed: int, n_vars: int):
        if n_vars <= 0 or not np.isfinite(pcs_needed):
            return "–"
        r = pcs_needed / max(1, n_vars)
        return "🟢" if r <= 0.30 else ("🟡" if r <= 0.60 else "🔴")

    # ------------------- CONFIGURATIONS -------------------
    with st.expander("Configurations", expanded=False):
        countries_all = (
            sorted(df_r["COUNTRY"].dropna().astype(str).unique())
            if "COUNTRY" in df_r
            else []
        )
        c1, c2, c3 = st.columns([1.6, 1.0, 1.0])
        with c1:
            sel_ctry = st.multiselect(
                "Countries",
                options=countries_all,
                default=countries_all,
                help="Per-country analysis (based on current time window).",
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

    # ---- Inline help ----
    st.markdown('<div class="hintrow">', unsafe_allow_html=True)
    htip(
        "Cond#",
        "Standardized condition number σ_max/σ_min (SVD). Lower is better: <15 low, 15–30 medium, >30 high.",
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
    # 1) OVERALL DATASET — per-country summary (uses df_r)
    # =========================================================
    st.markdown("### 1) Overall ratings (all drivers) — per country")

    var_target_num = 0.80
    buckets = _bucket_map()
    all_drivers = _all_drivers()

    bench_rows = []
    overall_vif_by_country = {}

    for ctry in sel_ctry:
        dct = (
            df_r[df_r["COUNTRY"].astype(str).eq(ctry)]
            if "COUNTRY" in df_r
            else df_r.copy()
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
        width="stretch",
    )

    # Bucket variable tables (overall run)
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
                    st.dataframe(out, hide_index=True, width="stretch")
                else:
                    st.info(
                        "No variables available in this bucket for the selected countries."
                    )
        st.caption(
            "Legend: VIF bands — 🟢 <5 (OK), 🟡 5–7.5 (Watch), 🔴 ≥7.5 (Flag). Spend variables are **not** auto-suggested for removal."
        )

    # =========================================================
    # 2) ADJUST VARIABLE SELECTION → RE-SCORE (what-if)
    # =========================================================
    st.markdown("### 2) Adjust drivers & re-score (what-if)")

    if "diag_selected_drivers_v10" not in st.session_state:
        st.session_state["diag_selected_drivers_v10"] = _all_drivers().copy()

    nice_raw_pairs = sorted(
        [(nice(c), c) for c in _all_drivers()], key=lambda t: t[0].lower()
    )
    pick_options = [nr[0] for nr in nice_raw_pairs]
    nice_to_raw = {nr[0]: nr[1] for nr in nice_raw_pairs}

    default_nice = [
        nice(c)
        for c in st.session_state["diag_selected_drivers_v10"]
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

    st.session_state["diag_selected_drivers_v10"] = [
        nice_to_raw[n] for n in sel if n in nice_to_raw
    ]
    drivers_sel = st.session_state["diag_selected_drivers_v10"]

    # Compute combined VIF on current time window (all selected countries)
    strong_to_drop, mod_to_drop, mild_to_drop = [], [], []
    if drivers_sel:
        d_comb = (
            df_r[df_r["COUNTRY"].astype(str).isin(sel_ctry)]
            if "COUNTRY" in df_r
            else df_r.copy()
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

    if drivers_sel:
        if strong_to_drop:
            if st.button(
                f"Drop suggested: Strong (≥10) — {len(set(strong_to_drop))} vars"
            ):
                st.session_state["diag_selected_drivers_v10"] = [
                    c
                    for c in st.session_state["diag_selected_drivers_v10"]
                    if c not in set(strong_to_drop)
                ]
                st.rerun()
        if strong_to_drop or mod_to_drop:
            total_sm = len(set(strong_to_drop) | set(mod_to_drop))
            if st.button(
                f"Drop suggested: Strong + Moderate (≥7.5) — {total_sm} vars"
            ):
                to_drop = set(strong_to_drop) | set(mod_to_drop)
                st.session_state["diag_selected_drivers_v10"] = [
                    c
                    for c in st.session_state["diag_selected_drivers_v10"]
                    if c not in to_drop
                ]
                st.rerun()
        if strong_to_drop or mod_to_drop or mild_to_drop:
            total_strict = len(
                set(strong_to_drop) | set(mod_to_drop) | set(mild_to_drop)
            )
            if st.button(f"Drop suggested: Strict (≥5) — {total_strict} vars"):
                to_drop = (
                    set(strong_to_drop) | set(mod_to_drop) | set(mild_to_drop)
                )
                st.session_state["diag_selected_drivers_v10"] = [
                    c
                    for c in st.session_state["diag_selected_drivers_v10"]
                    if c not in to_drop
                ]
                st.rerun()
    else:
        st.info(
            "No variables selected. Pick at least one to compute what-if scores."
        )

    # What-if re-score per country
    if drivers_sel:
        bench_rows2 = []
        for ctry in sel_ctry:
            dct = (
                df_r[df_r["COUNTRY"].astype(str).eq(ctry)]
                if "COUNTRY" in df_r
                else df_r.copy()
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
            width="stretch",
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
    # 3) COUNTRY DETAILS — mirrors Section 1, using CURRENT SELECTION
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

    detail_rows = []
    detail_vif_by_country = {}
    for ctry in sel_ctry:
        dct = (
            df_r[df_r["COUNTRY"].astype(str).eq(ctry)]
            if "COUNTRY" in df_r
            else df_r.copy()
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
        width="stretch",
    )

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
                    st.dataframe(out, hide_index=True, width="stretch")
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
    st.caption(
        "Quick, non-causal curve fit to gauge whether a driver has enough signal to justify inclusion/engineering in a future MMM."
    )

    # --- Local winsorizer (same behavior as Tab 1) ---
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

    # --- Conditioning controls (independent of other tabs) ---
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

    # --- Build driver universe (metadata-driven; uses current filtered window df_r) ---
    paid_spend_cols = [
        c for c in (mp.get("paid_media_spends") or []) if c in df_r.columns
    ]
    paid_var_cols = [
        c for c in (mp.get("paid_media_vars") or []) if c in df_r.columns
    ]
    organic_cols = [
        c for c in (mp.get("organic_vars") or []) if c in df_r.columns
    ]
    context_cols = [
        c for c in (mp.get("context_vars") or []) if c in df_r.columns
    ]
    factor_cols = [
        c for c in (mp.get("factor_vars") or []) if c in df_r.columns
    ]

    # Also include "other drivers" = numeric columns not in any metadata bucket, not goals/date/country/etc.
    EXCLUDE = set(
        paid_spend_cols
        + paid_var_cols
        + organic_cols
        + context_cols
        + factor_cols
        + (goal_cols or [])
    )
    for col in [
        "DATE",
        DATE_COL,
        "COUNTRY",
        "DATE_PERIOD",
        "PERIOD_LABEL",
        "_TOTAL_SPEND",
    ]:
        if col in df_r.columns:
            EXCLUDE.add(col)
    numeric_df = df_r.select_dtypes(include=[np.number]).copy()
    other_driver_cols = [c for c in numeric_df.columns if c not in EXCLUDE]

    # Final candidates (preserve bucket-first order, then others)
    def _dedup(seq):
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                out.append(x)
                seen.add(x)
        return out

    driver_all = _dedup(
        paid_spend_cols
        + paid_var_cols
        + organic_cols
        + context_cols
        + factor_cols
        + other_driver_cols
    )

    # Winsorize target + drivers used in this tab (on df_r)
    all_corr_cols = list(set(driver_all + (goal_cols or [])))
    df_r_w = winsorize_columns(df_r, all_corr_cols, wins_mode, wins_pct)

    if not goal_cols:
        st.info("No goals found in metadata.")
        st.stop()
    if not driver_all:
        st.info("No driver columns available.")
        st.stop()

    # --- Selections (default Goal honors sidebar GOAL) ---
    _goal_opts = [nice(g) for g in goal_cols]
    _goal_default_idx = 0
    if GOAL and GOAL in goal_cols:
        try:
            _goal_default_idx = _goal_opts.index(nice(GOAL))
        except ValueError:
            _goal_default_idx = 0

    y_goal_label = st.selectbox(
        "Goal (Y)",
        _goal_opts,
        index=_goal_default_idx,
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

    # Heuristic: treat factor/binary vars differently for the "exclude zeros" default
    is_factorish = (
        (x_col in factor_cols)
        or x_col.endswith(("_IS_ON", "_FLAG"))
        or (df_r_w[x_col].nunique(dropna=True) <= 3)
    )
    exclude_zero = st.checkbox(
        "Exclude zero values for driver",
        value=(not is_factorish),
        key="deep_exz",
        help="Helpful for sparse spends/vars; usually off for binary flags.",
    )

    outlier_method = st.selectbox(
        "Outlier handling",
        ["none", "percentile (top only)", "zscore (<3)"],
        index=0,
        key="deep_outlier",
        help="Applies after the (optional) winsorization above. Percentile drops top ~2% of X; z-score drops |z(X)| ≥ 3.",
    )

    # --- Filter + outliers (within the active window) ---
    x_raw = pd.to_numeric(df_r_w[x_col], errors="coerce")
    y_raw = pd.to_numeric(df_r_w[y_col], errors="coerce")
    mask = x_raw.notna() & y_raw.notna()
    if exclude_zero and not is_factorish:
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
        st.stop()

    # --- Quadratic fit ---
    X = np.array(x_f).reshape(-1, 1)
    poly = PolynomialFeatures(degree=2)
    Xp = poly.fit_transform(X)
    mdl = LinearRegression().fit(Xp, y_f)
    y_hat = mdl.predict(Xp)

    # --- Metrics ---
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

    def score_bar_metric_first(
        metric_title: str,
        tooltip_text: str,
        value_txt: str,
        level: str,
        percent: int,
    ):
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
        if val is None or not np.isfinite(val):
            return ("yellow", "Insufficient data — treat with caution.")
        if val >= 0.35:
            return (
                "green",
                "Promising signal — likely meaningful; candidate for MMM.",
            )
        if val >= 0.15:
            return (
                "yellow",
                "Some signal — consider with transforms/lags or as part of a bundle.",
            )
        return (
            "red",
            "Weak/noisy — unlikely to add value without re-engineering.",
        )

    def classify_mae_card(val: float):
        if val is None or not np.isfinite(val):
            return ("yellow", "Insufficient data — treat with caution.")
        if val <= 0.10:
            return (
                "green",
                "Average error small vs goal scale — usable for exploration.",
            )
        if val <= 0.30:
            return ("yellow", "Average error moderate — interpret cautiously.")
        return ("red", "Average error large — not reliable for exploration.")

    def classify_rho_card(val: float):
        if val is None or not np.isfinite(val):
            return ("yellow", "Insufficient data — treat with caution.")
        s = abs(val)
        if s >= 0.35:
            return (
                "green",
                "Clear monotonic pattern in ranks — usable signal.",
            )
        if s >= 0.15:
            return ("yellow", "Some monotonic pattern — consider with caution.")
        return ("red", "Weak/none — ranks move inconsistently.")

    TITLE_R2, TITLE_MAE, TITLE_RHO = ("R²", "MAE (relative)", "Spearman ρ")
    TIP_R2 = "Explained variance (fit strength) between driver and goal. Higher is better."
    TIP_MAE = "Average error vs goal’s typical scale (5th–95th pct). Smaller is better."
    TIP_RHO = "Monotonic rank correlation (strength & direction). Farther from 0 is stronger."

    def fill_from_r2(v):
        if v is None or not np.isfinite(v):
            return 50
        s = (v - (-0.2)) / (1.0 - (-0.2))  # map ~[-0.2..1.0] → 0..100
        return int(max(0, min(1, s)) * 100)

    def fill_from_mae(v):
        if v is None or not np.isfinite(v):
            return 50
        s = 1 - min(v / 0.30, 1.0)  # 0..0.30 → 100..0
        return int(max(0, min(1, s)) * 100)

    def fill_from_rho(v):
        if v is None or not np.isfinite(v):
            return 50
        return int(min(abs(v), 1.0) * 100)

    # Scorecards
    lvl_r2, msg_r2 = classify_r2_card(r2)
    lvl_mae, msg_mae = classify_mae_card(nmae)
    lvl_rho, msg_rho = classify_rho_card(rho if np.isfinite(rho) else np.nan)

    r2_txt = f"{r2:.2f}" if np.isfinite(r2) else "—"
    mae_txt = f"{nmae*100:.1f}%" if np.isfinite(nmae) else "—"
    rho_txt = f"{rho:+.2f}" if np.isfinite(rho) else "—"

    p_r2 = fill_from_r2(r2)
    p_mae = fill_from_mae(nmae)
    p_rho = fill_from_rho(rho if np.isfinite(rho) else np.nan)

    # One-time CSS (ok to repeat)
    st.markdown(
        """
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
    """,
        unsafe_allow_html=True,
    )

    st.markdown("#### Model Fit — Scorecards (signal for MMM)")
    c1, c2, c3 = st.columns(3)
    with c1:
        score_bar_metric_first("R²", TIP_R2, r2_txt, lvl_r2, p_r2)
        st.caption(msg_r2)
    with c2:
        score_bar_metric_first(
            "MAE (relative)", TIP_MAE, mae_txt, lvl_mae, p_mae
        )
        st.caption(msg_mae)
    with c3:
        score_bar_metric_first("Spearman ρ", TIP_RHO, rho_txt, lvl_rho, p_rho)
        if np.isfinite(rho):
            direction = (
                "positive" if rho > 0 else ("negative" if rho < 0 else "no")
            )
            st.caption(
                f"Ranks move in a {direction} monotonic pattern (ρ = {rho:+.2f}). {msg_rho}"
            )
        else:
            st.caption(msg_rho)

    # --- Fit visualization + marginal returns ---
    pcts = [10, 25, 50, 75, 90]
    x_pts = np.percentile(np.array(x_f), pcts)
    dydx = mdl.coef_[1] + 2 * mdl.coef_[2] * x_pts
    y_pts = mdl.predict(poly.transform(x_pts.reshape(-1, 1)))

    xs = np.sort(np.array(x_f)).reshape(-1, 1)
    ys = mdl.predict(poly.transform(xs))

    figfit = go.Figure()
    figfit.add_trace(
        go.Scatter(
            x=x_f.values,
            y=y_f.values,
            mode="markers",
            name="Actual",
            opacity=0.45,
        )
    )
    figfit.add_trace(
        go.Scatter(x=xs.squeeze(), y=ys, mode="lines", name="Fitted Curve")
    )
    figfit.add_trace(
        go.Scatter(
            x=x_pts,
            y=y_pts,
            mode="markers+text",
            name="Percentiles",
            text=[f"{p}%" for p in pcts],
            textposition="top center",
        )
    )
    figfit.update_layout(
        title=f"Fitted Curve for {nice(x_col)} → {nice(y_col)}",
        xaxis_title=nice(x_col),
        yaxis_title=nice(y_col),
    )
    st.plotly_chart(figfit, width="stretch")

    mr_tbl = pd.DataFrame(
        {
            "Percentile": [f"{p}%" for p in pcts],
            "Driver value": [f"{float(v):.2f}" for v in x_pts],
            "Marginal return (dy/dx)": [f"{float(v):.4f}" for v in dydx],
        }
    )
    st.dataframe(mr_tbl, hide_index=True, width="stretch")
