# pages/01_Analysis.py
import json

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st
from app_shared import (
    _sf_params_from_env,
    effective_sql,
    ensure_sf_conn,
    require_login_and_domain,
    run_sql,
)

require_login_and_domain()

st.set_page_config(page_title="Exploratory Analysis", layout="wide")
st.title("📈 Exploratory Analysis")


# ------------------ utils ------------------

try:
    conn = ensure_sf_conn()  # creates/reuses and pings
except Exception as e:
    st.error(f"Snowflake connection not available: {e}")
    st.stop()


def _conn_fp() -> str:
    p = (st.session_state.get("sf_params") or {}) or _sf_params_from_env()
    keep = {
        k: p.get(k)
        for k in ("account", "warehouse", "database", "schema", "role", "user")
    }
    return json.dumps(keep, sort_keys=True)


@st.cache_data(show_spinner=False)
def load_data(
    sql: str, conn_fingerprint: str, sample_n: int | None
) -> pd.DataFrame:
    # NOTE: 'conn_fingerprint' is a cache key only; you don't use it in the body
    df = run_sql(sql)  # <-- this is the central query path
    if sample_n and sample_n > 0 and len(df) > sample_n:
        df = df.sample(sample_n, random_state=42).reset_index(drop=True)
    return df


def _to_float32(df: pd.DataFrame) -> pd.DataFrame:
    num = df.select_dtypes(include=np.number).columns
    df[num] = df[num].astype("float32")
    return df


def _safe_corr(df: pd.DataFrame, method: str = "pearson") -> pd.DataFrame:
    # Pandas corr handles pearson/spearman; fill NaNs for stability
    return df.corr(method=method).fillna(0.0)


def _ols_r2_univariate(x: np.ndarray, y: np.ndarray) -> float:
    # OLS with intercept via closed form
    X = np.c_[np.ones(len(x)), x]
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    y_hat = X @ beta
    ss_res = np.sum((y - y_hat) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    return float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def _coef_var(v: pd.Series) -> float:
    m = float(v.mean())
    s = float(v.std(ddof=0))
    return float(s / m) if m not in (0.0, np.nan) else np.nan


def _normalize_cols(df: pd.DataFrame, how: str) -> pd.DataFrame:
    if how == "None":
        return df
    out = df.copy()
    for c in out.columns:
        s = out[c].astype(float)
        if how == "z-score":
            mu, sd = s.mean(), s.std(ddof=0) or 1.0
            out[c] = (s - mu) / sd
        elif how == "min-max":
            mn, mx = s.min(), s.max()
            out[c] = (s - mn) / (mx - mn) if mx > mn else 0.0
        elif how == "mean=100":
            mu = s.mean() or 1.0
            out[c] = s / mu * 100.0
    return out


def _high_corr_pairs(
    corrM: pd.DataFrame, cols: list[str], thr: float
) -> list[tuple[str, str, float]]:
    out = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            r = float(corrM.loc[a, b])
            if abs(r) >= thr:
                out.append((a, b, r))
    return out


def _corr_pairs_df(
    CM: pd.DataFrame, cols: list[str], thr: float
) -> pd.DataFrame:
    rows = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            r = float(CM.loc[a, b])
            if pd.notna(r) and abs(r) >= thr:
                rows.append(
                    {
                        "feature_1": a,
                        "feature_2": b,
                        "corr": r,
                        "abs_corr": abs(r),
                    }
                )
    df = pd.DataFrame(rows)
    if df.empty:
        # return an empty frame with expected columns so downstream code is happy
        return pd.DataFrame(
            columns=["feature_1", "feature_2", "corr", "abs_corr"]
        )
    return df.sort_values(
        "abs_corr", ascending=False, kind="mergesort"
    ).reset_index(drop=True)


# Try optional deps
try:
    from sklearn.decomposition import PCA

    _HAS_SK = True
except Exception:
    _HAS_SK = False

try:
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    _HAS_SM = True
except Exception:
    _HAS_SM = False

# ------------------ 1) Data source ------------------
with st.expander("1) Pick data & load (cached)", expanded=True):
    table = st.text_input("Table (DB.SCHEMA.TABLE)", key="an_table")
    query = st.text_area("Custom SQL (optional)", key="an_query")
    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    sample_n = c1.number_input(
        "Sample rows (optional)", min_value=0, value=0, help="0 = use all rows"
    )
    load_clicked = c2.button("📥 Load / Refresh", type="primary")
    clear_cache_clicked = c3.button("♻️ Clear cache")
    show_preview = c4.checkbox("Show 100-row preview", value=True)

    if clear_cache_clicked:
        load_data.clear()
        st.session_state.pop("an_sql", None)
        st.session_state.pop("an_shape", None)
        st.success("Cleared cached data.")

    if load_clicked:
        sql_eff = effective_sql(table, query)
        if not sql_eff:
            st.warning("Provide a table or SQL query.")
        else:
            df = load_data(sql_eff, _conn_fp(), int(sample_n) or None)
            st.session_state["an_sql"] = sql_eff
            st.session_state["an_shape"] = df.shape
            st.success(
                f"Loaded {df.shape[0]:,} rows × {df.shape[1]} cols (cached)."
            )

if "an_sql" not in st.session_state:
    st.info(
        "Load data first—it will be cached and **not** re-queried when you change controls."
    )
    st.stop()

df = load_data(st.session_state["an_sql"], _conn_fp(), int(sample_n) or None)
df = _to_float32(df)

if show_preview:
    st.caption("Data preview")
    st.dataframe(df.head(100), use_container_width=True, hide_index=True)

# ------------------ 2) Controls (form -> compute only on click) ------------------
all_cols = df.columns.tolist()
num_cols = df.select_dtypes(include=np.number).columns.tolist()
date_guess = next((c for c in all_cols if "date" in c.lower()), None)
dep_guess = next(
    (
        c
        for c in all_cols
        if c.lower()
        in ("revenue", "sales", "y", "upload_value", "target", "uplift")
    ),
    None,
)

with st.form("analysis_controls"):
    st.subheader("2) Analysis controls")
    a1, a2, a3 = st.columns(3)
    date_col = a1.selectbox(
        "Date column",
        [None] + all_cols,
        index=(all_cols.index(date_guess) + 1 if date_guess in all_cols else 0),
    )
    dep_var = a2.selectbox(
        "Target (dependent) column",
        [None] + all_cols,
        index=(all_cols.index(dep_guess) + 1 if dep_guess in all_cols else 0),
    )
    corr_method = a3.radio(
        "Correlation method", ["pearson", "spearman"], horizontal=True
    )

    b1, b2, b3 = st.columns(3)
    var_thresh = b1.slider(
        "Drop near-constant features (relative variance ≥)", 0.0, 1.0, 0.0, 0.01
    )
    corr_thresh = b2.slider(
        "High-correlation flag (|ρ| ≥)", 0.5, 0.99, 0.85, 0.01
    )
    max_feats_for_interactions = b3.slider(
        "Interaction search pool (top N by variance)", 4, 30, 12, 1
    )

    # Channel candidates = numeric except date/target
    default_channels = [c for c in num_cols if c not in {date_col, dep_var}]
    channels = st.multiselect(
        "Channel/driver columns", options=num_cols, default=default_channels
    )

    plot_drivers = st.multiselect(
        "Drivers to plot (time-series)",
        options=channels,
        default=channels,  # default = all selected channels
        help="This only affects the time-series chart; calculations still use the selected channels above.",
    )

    c1, c2, c3, c4 = st.columns(4)
    resample = c1.selectbox(
        "Resample", ["None", "W (weekly)", "M (monthly)"], index=0
    )
    smooth_k = c2.slider(
        "Rolling window (for plots)", 1, 12, 1, help="1 = no smoothing"
    )
    norm_mode = c3.selectbox(
        "Normalize drivers (plots)",
        ["None", "z-score", "min-max", "mean=100"],
        index=0,
    )
    topN_inter = c4.slider("Top N interactions to surface", 0, 20, 8)

    run_btn = st.form_submit_button("▶️ Run analysis")

# Persist controls once; reuse on future reruns (e.g., when 5) Run checks is clicked)
if run_btn:
    st.session_state["an_cfg"] = dict(
        date_col=date_col,
        dep_var=dep_var,
        corr_method=corr_method,
        var_thresh=float(var_thresh),
        corr_thresh=float(corr_thresh),
        max_feats_for_interactions=int(max_feats_for_interactions),
        channels=channels,
        plot_drivers=plot_drivers,
        resample=resample,
        smooth_k=int(smooth_k),
        norm_mode=norm_mode,
        topN_inter=int(topN_inter),
    )

cfg = st.session_state.get("an_cfg")
if not cfg:
    st.stop()  # require one initial run of the main form

# Rebind locals from persisted config so other forms can rerun safely
date_col = cfg["date_col"]
dep_var = cfg["dep_var"]
corr_method = cfg["corr_method"]
var_thresh = cfg["var_thresh"]
corr_thresh = cfg["corr_thresh"]
max_feats_for_interactions = cfg["max_feats_for_interactions"]
channels = cfg["channels"]
plot_drivers = cfg["plot_drivers"]
resample = cfg["resample"]
smooth_k = cfg["smooth_k"]
norm_mode = cfg["norm_mode"]
topN_inter = cfg["topN_inter"]


# ------------------ 3) Prepare working frame ------------------
work = df.copy()

# Parse date if chosen
if date_col:
    if not pd.api.types.is_datetime64_any_dtype(work[date_col]):
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce")

# Keep only needed cols
keep = [c for c in [date_col, dep_var] + channels if c and c in work.columns]
work = work[keep].dropna()
if len(work) == 0:
    st.warning("No rows left after filtering NA.")
    st.stop()

# Variance filter among channels (relative to max var)
ch = [c for c in channels if c in work.columns]
if ch:
    v = work[ch].var(numeric_only=True)
    rel = (v / (v.max() if v.max() > 0 else 1)).fillna(0.0)
    pass_cols = [c for c in ch if rel.get(c, 0.0) >= var_thresh]
    dropped_low_var = sorted(set(ch) - set(pass_cols))
    if dropped_low_var:
        st.caption(
            f"Dropped {len(dropped_low_var)} low-variance feature(s): {', '.join(dropped_low_var)}"
        )
    channels = pass_cols

if not channels:
    st.warning("No channel columns selected.")
    st.stop()

# ------------------ 4) Correlations, R², spend variation ------------------
st.subheader("3) Correlations, R² (univariate), and spend variation")

corr_cols = channels + ([dep_var] if dep_var else [])
corrM = _safe_corr(work[corr_cols], method=corr_method)
high_corr_pairs = _high_corr_pairs(
    corrM.loc[channels, channels], channels, corr_thresh
)

# === Bigger, readable heatmap: dynamic width/height, no label truncation ===
n = len(corr_cols)
cell = 30  # cell size (px). Increase if you want even larger squares.
heat_width = max(600, cell * n)
heat_height = max(600, cell * n)

corr_long = corrM.reset_index().melt(
    "index", var_name="col2", value_name="corr"
)
heat = (
    alt.Chart(corr_long)
    .mark_rect()
    .encode(
        x=alt.X(
            "index:N",
            title="",
            sort=None,
            axis=alt.Axis(labelAngle=-45, labelLimit=0, labelFontSize=12),
        ),
        y=alt.Y(
            "col2:N",
            title="",
            sort=None,
            axis=alt.Axis(labelLimit=0, labelFontSize=12),
        ),
        color=alt.Color(
            "corr:Q", scale=alt.Scale(scheme="redblue", domain=(-1, 1))
        ),
        tooltip=["index", "col2", alt.Tooltip("corr:Q", format=".2f")],
    )
    .properties(width=heat_width, height=heat_height)
    .configure_axis(labelLimit=0)
)
st.altair_chart(heat, use_container_width=True)
st.download_button(
    "Download correlation matrix (CSV)",
    corrM.to_csv().encode(),
    "correlations.csv",
    "text/csv",
)

# Stats table: univariate R² + coefficient of variation
stats_rows = []
if dep_var:
    y = work[dep_var].astype(float).to_numpy()
for c in channels:
    cv = _coef_var(work[c])
    r2 = np.nan
    if dep_var:
        x = work[c].astype(float).to_numpy()
        r2 = _ols_r2_univariate(x, y)
    stats_rows.append({"feature": c, "R2 (uni)": r2, "coef_var (std/mean)": cv})
stats_df = pd.DataFrame(stats_rows).sort_values(
    by=["R2 (uni)"], ascending=False, na_position="last"
)
st.dataframe(stats_df, use_container_width=True, hide_index=True)

# ------------------ 4) Time series on drivers (FIXED) ------------------
st.subheader("4) Time series on drivers")

if date_col:
    # Keep only the columns we actually need
    cols_needed = [date_col] + channels + ([dep_var] if dep_var else [])
    ts_df = work[cols_needed].copy().sort_values(date_col)

    # Optional resample AFTER subsetting to avoid pulling non-selected cols
    if resample != "None":
        rule = "W" if resample.startswith("W") else "M"
        ts_df = (
            ts_df.set_index(date_col)
            .resample(rule)
            .sum(
                numeric_only=True
            )  # sums are typical for spend/sales; switch to .mean if needed
            .reset_index()
        )

    # Only plot drivers that are still in the filtered channel set
    drivers_to_plot = [d for d in (plot_drivers or channels) if d in channels]
    if not drivers_to_plot:
        st.info(
            "No drivers selected to plot (after variance/correlation filtering)."
        )
    else:
        # Build plotting frame with just date, the selected drivers, and (optionally) the target
        plot_df = ts_df[
            [date_col] + drivers_to_plot + ([dep_var] if dep_var else [])
        ].copy()

        # Normalize & smooth drivers *only* (do not touch the target)
        plot_df[drivers_to_plot] = _normalize_cols(
            plot_df[drivers_to_plot], norm_mode
        )
        if smooth_k > 1:
            plot_df[drivers_to_plot] = (
                plot_df[drivers_to_plot].rolling(smooth_k, min_periods=1).mean()
            )

        long = plot_df.melt(
            id_vars=[date_col], var_name="series", value_name="value"
        )

        # Bigger chart so legends/lines are readable
        line = (
            alt.Chart(long)
            .mark_line()
            .encode(
                x=alt.X(f"{date_col}:T", title="Date"),
                y=alt.Y("value:Q", title=""),
                color=alt.Color("series:N", legend=alt.Legend(columns=1)),
                tooltip=[
                    date_col,
                    "series",
                    alt.Tooltip("value:Q", format=".2f"),
                ],
            )
            .properties(height=480)
            .interactive()
        )
        st.altair_chart(line, use_container_width=True)
else:
    st.info("Pick a date column to see time series.")

# ------------------ 6) Redundancy: collinearity, VIF, PCA ------------------
st.subheader("5) Redundancy checks")
with st.container(border=True):
    st.caption(
        "Identify collinearity, low-variation features, strong correlations, PCA structure, and potential interactions."
    )

    # --- Controls just for this section (no global reruns until submit) ---
    with st.form("redundancy_controls"):
        c1, c2, c3, c4 = st.columns(4)
        corr_thr = c1.slider("Corr |X| >=", 0.60, 0.99, 0.85, 0.01)
        cv_thr = c2.slider("Low variation: CoefVar <", 0.00, 0.50, 0.05, 0.01)
        max_pcs = c3.slider(
            "Max PCs",
            2,
            max(2, min(12, len(channels))),
            min(6, len(channels)),
            1,
        )
        top_inter = c4.slider(
            "Top interactions (by |corr with target|)", 5, 100, 20, 5
        )
        run_redundancy = st.form_submit_button("Run checks")

    if run_redundancy:
        # ---- Prepare data once
        X = work[channels].astype(float).copy()

        # A) Low variation (Coefficient of Variation)
        means = X.mean()
        stds = X.std(ddof=0).replace(0, np.nan)
        cv = (stds / means.abs()).rename("coef_var")
        low_var = cv[cv < cv_thr].sort_values()

        # B) High correlation pairs (safe, no KeyError when empty)
        CM = _safe_corr(X, method=corr_method)
        corr_pairs_df = _corr_pairs_df(CM, channels, corr_thr)

        # C) VIF (no extra deps): regress each feature on the others
        def _vif_series(Xv: pd.DataFrame) -> pd.Series:
            arr = Xv.to_numpy()
            out = []
            for j in range(arr.shape[1]):
                yj = arr[:, j]
                Xj = np.delete(arr, j, axis=1)
                Xj = np.column_stack([np.ones(len(Xj)), Xj])
                beta, *_ = np.linalg.lstsq(Xj, yj, rcond=None)
                yhat = Xj @ beta
                rss = np.sum((yj - yhat) ** 2)
                tss = np.sum((yj - yj.mean()) ** 2)
                r2 = 0.0 if tss == 0 else 1 - rss / tss
                vif = np.inf if (1 - r2) == 0 else 1 / (1 - r2)
                out.append(vif)
            return pd.Series(out, index=Xv.columns, name="VIF")

        vif = _vif_series(X)

        # D) PCA (NumPy SVD on standardized X)
        Xz = (X - X.mean()) / X.std(ddof=0).replace(0, np.nan)
        Xz = Xz.fillna(0).to_numpy()
        U, S, VT = np.linalg.svd(Xz, full_matrices=False)
        expl = (S**2) / np.sum(S**2)
        pcs = pd.DataFrame(
            {"PC": [f"PC{i+1}" for i in range(len(expl))], "ExplainedVar": expl}
        )
        loadings = pd.DataFrame(
            VT.T,
            index=channels,
            columns=[f"PC{i+1}" for i in range(VT.shape[0])],
        )

        # E) Simple interaction discovery (corr of Xi*Xj with target) — robust to empty
        interactions_df = pd.DataFrame()
        if dep_var:
            y = work[dep_var].astype(float).to_numpy()
            rows = []
            for i in range(len(channels)):
                xi = work[channels[i]].astype(float).to_numpy()
                for j in range(i + 1, len(channels)):
                    xj = work[channels[j]].astype(float).to_numpy()
                    prod = xi * xj
                    if np.nanstd(prod) == 0 or np.nanstd(y) == 0:
                        corr = np.nan
                    else:
                        corr = np.corrcoef(
                            np.nan_to_num(prod), np.nan_to_num(y)
                        )[0, 1]
                    rows.append(
                        {
                            "interaction": f"{channels[i]} × {channels[j]}",
                            "corr_with_target": corr,
                            "abs_corr": abs(corr) if pd.notna(corr) else np.nan,
                        }
                    )
            tmp = pd.DataFrame(rows)
            if not tmp.empty:
                interactions_df = (
                    tmp.dropna()
                    .sort_values("abs_corr", ascending=False)
                    .head(top_inter)
                    .drop(columns="abs_corr", errors="ignore")
                )

        # ---- Nicely formatted tabs & visuals
        t1, t2, t3, t4, t5 = st.tabs(
            ["High correlation", "Low variation", "VIF", "PCA", "Interactions"]
        )

        with t1:
            st.caption(f"Pairs with |corr| ≥ **{corr_thr:.2f}**")
            if corr_pairs_df.empty:
                st.success("No pairs exceed the threshold.")
            else:
                st.dataframe(
                    corr_pairs_df.drop(columns=["abs_corr"], errors="ignore"),
                    use_container_width=True,
                    hide_index=True,
                )
                keep = sorted(
                    set(corr_pairs_df["feature_1"]).union(
                        corr_pairs_df["feature_2"]
                    )
                )
                if keep:
                    sub = CM.loc[keep, keep]
                    n = len(keep)
                    cell = 28
                    W, H = max(600, cell * n), max(600, cell * n)
                    m = sub.reset_index().melt(
                        "index", var_name="var2", value_name="corr"
                    )
                    heat = (
                        alt.Chart(m)
                        .mark_rect()
                        .encode(
                            x=alt.X(
                                "index:N",
                                axis=alt.Axis(labelAngle=-45, labelLimit=0),
                            ),
                            y=alt.Y("var2:N", axis=alt.Axis(labelLimit=0)),
                            color=alt.Color(
                                "corr:Q",
                                scale=alt.Scale(
                                    scheme="redblue", domain=(-1, 1)
                                ),
                            ),
                            tooltip=[
                                "index",
                                "var2",
                                alt.Tooltip("corr:Q", format=".2f"),
                            ],
                        )
                        .properties(width=W, height=H)
                    )
                    st.altair_chart(heat, use_container_width=True)

        with t2:
            st.caption(
                f"Features flagged with **coef_var < {cv_thr:.2f}** (std/|mean|)."
            )
            if low_var.empty:
                st.success("No low-variation features.")
            else:
                st.dataframe(
                    low_var.reset_index().rename(columns={"index": "feature"}),
                    use_container_width=True,
                    hide_index=True,
                )

        with t3:
            st.caption(
                "Variance Inflation Factor (rule-of-thumb: >5 or >10 indicates strong multicollinearity)."
            )
            vif_df = (
                vif.sort_values(ascending=False)
                .rename_axis("feature")
                .reset_index(name="VIF")
            )
            st.dataframe(vif_df, use_container_width=True, hide_index=True)

        with t4:
            st.caption("PCA on standardized features")
            # Scree (bigger)
            scree = (
                alt.Chart(pcs.iloc[:max_pcs])
                .mark_bar()
                .encode(
                    x=alt.X("PC:N", sort=None),
                    y=alt.Y("ExplainedVar:Q", axis=alt.Axis(format="%")),
                    tooltip=[alt.Tooltip("ExplainedVar:Q", format=".2%")],
                )
                .properties(height=320)
            )
            st.altair_chart(scree, use_container_width=True)

            # Loadings heatmap (bigger)
            pcs_to_show = [
                f"PC{i+1}" for i in range(min(max_pcs, loadings.shape[1]))
            ]
            L = (
                loadings[pcs_to_show]
                .reset_index()
                .melt("index", var_name="PC", value_name="loading")
                .rename(columns={"index": "feature"})
            )
            n_r = len(channels)
            n_c = len(pcs_to_show)
            cell = 26
            W, H = max(700, cell * n_c), max(520, cell * n_r)
            load_heat = (
                alt.Chart(L)
                .mark_rect()
                .encode(
                    x=alt.X("PC:N", sort=None),
                    y=alt.Y(
                        "feature:N", sort=None, axis=alt.Axis(labelLimit=0)
                    ),
                    color=alt.Color(
                        "loading:Q",
                        scale=alt.Scale(scheme="blueorange", domain=(-1, 1)),
                    ),
                    tooltip=[
                        "feature",
                        "PC",
                        alt.Tooltip("loading:Q", format=".2f"),
                    ],
                )
                .properties(width=W, height=H)
            )
            st.altair_chart(load_heat, use_container_width=True)

        with t5:
            if not dep_var:
                st.info("Provide a dependent variable to score interactions.")
            elif interactions_df.empty:
                st.info(
                    "No interactions surfaced (increase Top interactions or check data)."
                )
            else:
                st.caption(
                    "Top interactions by absolute correlation with the target (using Xi × Xj)."
                )
                st.dataframe(
                    interactions_df, use_container_width=True, hide_index=True
                )


# ------------------ 7) Interaction discovery (pairwise products) ------------------
st.subheader("6) Interaction discovery (pairwise products ranked by R²)")

if dep_var and topN_inter > 0:
    # restrict search pool by highest variance features to keep O(n^2) reasonable
    var_rank = work[channels].var().sort_values(ascending=False).index.tolist()
    pool = var_rank[:max_feats_for_interactions]
    y = work[dep_var].astype(float).to_numpy()
    rows = []
    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            a, b = pool[i], pool[j]
            prod = (
                work[a].astype(float).to_numpy()
                * work[b].astype(float).to_numpy()
            ).reshape(-1, 1)
            r2 = _ols_r2_univariate(prod.ravel(), y)
            rows.append(
                {"interaction": f"{a}*{b}", "R2 (uni)": r2, "a": a, "b": b}
            )
    inter_df = (
        pd.DataFrame(rows)
        .sort_values("R2 (uni)", ascending=False)
        .head(topN_inter)
    )
    if inter_df.empty:
        st.info("No interactions surfaced (check data or increase pool size).")
    else:
        st.dataframe(
            inter_df[["interaction", "R2 (uni)"]],
            use_container_width=True,
            hide_index=True,
        )
else:
    st.caption("Choose a target and set Top N > 0 to surface interactions.")

# ------------------ 8) Export current selection ------------------
st.subheader("7) Export selections")
export = {
    "selected_channels": channels,
    "dropped_low_variance": (
        dropped_low_var if "dropped_low_var" in locals() else []
    ),
    "high_corr_pairs": [(a, b, round(v, 3)) for a, b, v in high_corr_pairs],
    "suggested_interactions_topN": (
        inter_df["interaction"].tolist()
        if dep_var and topN_inter > 0 and "inter_df" in locals()
        else []
    ),
    "target": dep_var,
    "date_col": date_col,
    "corr_method": corr_method,
    "resample": resample,
    "smoothing_window": smooth_k,
    "normalization": norm_mode,
}
st.code(json.dumps(export, indent=2), language="json")
st.download_button(
    "Download selections as JSON",
    data=json.dumps(export, indent=2),
    file_name="analysis_selection.json",
    mime="application/json",
)
