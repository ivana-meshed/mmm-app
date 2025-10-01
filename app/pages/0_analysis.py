# pages/01_Analysis.py
import json
import math
import numpy as np
import pandas as pd
import streamlit as st
import altair as alt

from app_shared import effective_sql, ensure_sf_conn, run_sql

st.set_page_config(page_title="Exploratory Analysis", layout="wide")
st.title("📈 Exploratory Analysis")


# ------------------ utils ------------------
def _conn_fp() -> str:
    p = st.session_state.get("sf_params") or {}
    keep = {
        k: p.get(k)
        for k in ("account", "warehouse", "database", "schema", "role", "user")
    }
    return json.dumps(keep, sort_keys=True)


@st.cache_data(show_spinner=False)
def load_data(sql: str, conn_fp: str, sample_n: int | None) -> pd.DataFrame:
    ensure_sf_conn()
    df = run_sql(sql)
    # Optional downsampling (deterministic)
    if sample_n and sample_n > 0 and len(df) > sample_n:
        return df.sample(sample_n, random_state=42).reset_index(drop=True)
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

if not run_btn:
    st.stop()

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

# ------------------ 5) Time series on drivers ------------------
# ------------------ 5) Time series on drivers ------------------
st.subheader("4) Time series on drivers")
if date_col:
    ts_df = (
        work[[date_col] + channels + ([dep_var] if dep_var else [])]
        .copy()
        .sort_values(date_col)
    )
    if resample != "None":
        rule = "W" if resample.startswith("W") else "M"
        ts_df = (
            ts_df.set_index(date_col)
            .resample(rule)
            .sum(numeric_only=True)
            .reset_index()
        )

    # Which drivers to plot (target is optional overlay; not normalized)
    drivers_to_plot = plot_drivers if plot_drivers else channels

    # Normalize & smooth drivers *only*
    plot_df = ts_df.copy()
    plot_df[drivers_to_plot] = _normalize_cols(
        plot_df[drivers_to_plot], norm_mode
    )
    if smooth_k > 1:
        plot_df[drivers_to_plot] = (
            plot_df[drivers_to_plot].rolling(smooth_k, min_periods=1).mean()
        )

    plot_cols = drivers_to_plot + ([dep_var] if dep_var else [])
    long = plot_df[[date_col] + plot_cols].melt(
        id_vars=[date_col], var_name="series", value_name="value"
    )

    # Bigger chart height
    base_height = 420  # bump this if you want even taller
    line = (
        alt.Chart(long)
        .mark_line()
        .encode(
            x=alt.X(f"{date_col}:T", title="Date"),
            y=alt.Y("value:Q", title=""),
            color=alt.Color("series:N", legend=alt.Legend(columns=1)),
            tooltip=[date_col, "series", alt.Tooltip("value:Q", format=".2f")],
        )
        .properties(height=base_height)
        .interactive()
    )
    st.altair_chart(line, use_container_width=True)
else:
    st.info("Pick a date column to see time series.")

# ------------------ 6) Redundancy: collinearity, VIF, PCA ------------------
st.subheader("5) Redundancy checks")
col2 = work[channels]

# High-correlation pairs
Cabs = col2.corr(method=corr_method).abs().fillna(0.0)
upper = Cabs.where(np.triu(np.ones(Cabs.shape), k=1).astype(bool))
pairs = [
    (col2.columns[i], col2.columns[j], float(upper.iloc[i, j]))
    for i in range(len(col2.columns))
    for j in range(i + 1, len(col2.columns))
    if upper.iloc[i, j] >= corr_thresh
]
if pairs:
    st.warning(
        "Highly correlated pairs (|ρ| ≥ threshold): "
        + ", ".join([f"{a}–{b} ({v:.2f})" for a, b, v in pairs[:15]])
    )
else:
    st.success("No pairs above the high-correlation threshold.")

# VIF (if available)
if _HAS_SM and col2.shape[1] >= 2:
    X = col2.to_numpy()
    vif = []
    for i, name in enumerate(col2.columns):
        try:
            vif_val = variance_inflation_factor(X, i)
        except Exception:
            vif_val = np.nan
        vif.append({"feature": name, "VIF": float(vif_val)})
    vif_df = pd.DataFrame(vif).sort_values("VIF", ascending=False)
    st.caption("Variance Inflation Factor (VIF)")
    st.dataframe(vif_df, use_container_width=True, hide_index=True)
else:
    st.caption("VIF requires `statsmodels`; install if you want this table.")

# PCA quick look
try:
    if _HAS_SK:
        k = min(len(channels), 8)
        pca = PCA(n_components=k).fit(col2.to_numpy())
        exp = pd.DataFrame(
            {
                "component": [f"PC{i+1}" for i in range(k)],
                "explained_var": pca.explained_variance_ratio_,
            }
        )
    else:
        # NumPy fallback: SVD
        X = col2.to_numpy() - col2.to_numpy().mean(axis=0)
        U, S, Vt = np.linalg.svd(X, full_matrices=False)
        var = (S**2) / (X.shape[0] - 1) if X.shape[0] > 1 else S**2
        ratio = var / var.sum() if var.sum() > 0 else np.zeros_like(var)
        k = min(len(ratio), 8)
        exp = pd.DataFrame(
            {
                "component": [f"PC{i+1}" for i in range(k)],
                "explained_var": ratio[:k],
            }
        )
    st.caption("Explained variance (PCA)")
    st.bar_chart(exp.set_index("component"))
except Exception as e:
    st.info(f"PCA step skipped ({e}).")

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
    "high_corr_pairs": [(a, b, round(v, 3)) for a, b, v in pairs],
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
