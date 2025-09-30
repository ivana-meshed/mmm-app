# Data Analysis (pre-training)
import math
import itertools
import numpy as np
import pandas as pd
import streamlit as st

try:
    import altair as alt
except Exception:
    alt = None

from sklearn.linear_model import LinearRegression
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

try:
    # Nice-to-have; we'll gracefully fallback if not installed
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    import statsmodels.api as sm

    _HAS_STATSMODELS = True
except Exception:
    _HAS_STATSMODELS = False

from app_shared import effective_sql, run_sql, ensure_sf_conn

st.set_page_config(page_title="Robyn — Data Analysis", layout="wide")

st.title("📈 Data Analysis (pre-training)")

# ─────────────────────────────────────────────────────────────
# 0) Connection guard
# ─────────────────────────────────────────────────────────────
if not st.session_state.get("sf_connected", False):
    st.warning(
        "Please connect to Snowflake in **1) Snowflake Connection** first."
    )
    st.stop()

# ─────────────────────────────────────────────────────────────
# 1) Data selection & load
# ─────────────────────────────────────────────────────────────
with st.expander("Data selection", expanded=True):
    c1, c2 = st.columns([2, 1])
    with c1:
        table = st.text_input("Table (DB.SCHEMA.TABLE)", value="")
        query = st.text_area("Custom SQL (optional)", value="", height=120)
    with c2:
        sample_rows = st.number_input(
            "Sample rows (for analysis)", min_value=200, value=5000, step=100
        )
        normalize_timeseries = st.toggle(
            "Normalize time-series per variable (z-score)", value=True
        )

    sql_eff = effective_sql(table, query)
    load_clicked = st.button("🔄 Load sample")


@st.cache_data(show_spinner=True)
def _load_sample(sql_eff: str, n: int) -> pd.DataFrame:
    # Wrap the effective SQL to apply LIMIT safely
    sql = f"SELECT * FROM ({sql_eff}) t LIMIT {int(n)}"
    df = run_sql(sql)
    # Make lower-case column names for consistency
    df.columns = [str(c) for c in df.columns]
    return df


if not sql_eff or not load_clicked:
    st.info("Enter a table or SQL and click **Load sample**.")
    st.stop()

df = _load_sample(sql_eff, sample_rows)
if df.empty:
    st.warning("No rows returned.")
    st.stop()

# ─────────────────────────────────────────────────────────────
# 2) Column picking
# ─────────────────────────────────────────────────────────────
all_cols = list(df.columns)
num_cols = [c for c in all_cols if pd.api.types.is_numeric_dtype(df[c])]
dt_guess = [c for c in all_cols if "date" in c.lower()] or [
    c for c in all_cols if np.issubdtype(df[c].dtype, np.datetime64)
]

c1, c2, c3 = st.columns([1.2, 1, 2])
with c1:
    date_var = st.selectbox(
        "Date column",
        options=["(none)"] + all_cols,
        index=(all_cols.index(dt_guess[0]) + 1) if dt_guess else 0,
    )
with c2:
    dep_var = st.selectbox(
        "Target (dep_var)",
        options=num_cols,
        index=(
            num_cols.index("UPLOAD_VALUE") if "UPLOAD_VALUE" in num_cols else 0
        ),
    )
with c3:
    default_drivers = [c for c in num_cols if c not in {dep_var}]
    drivers = st.multiselect(
        "Driver/features to analyze",
        options=default_drivers,
        default=default_drivers[: min(12, len(default_drivers))],
    )

if not drivers:
    st.warning("Pick at least one driver.")
    st.stop()

# Ensure proper datetime if selected
if date_var != "(none)" and not pd.api.types.is_datetime64_any_dtype(
    df[date_var]
):
    with st.spinner("Parsing date column…"):
        df[date_var] = pd.to_datetime(df[date_var], errors="coerce")

# Drop rows with missing in required columns
need = [dep_var] + drivers + ([date_var] if date_var != "(none)" else [])
df_use = df.dropna(subset=[c for c in need if c in df.columns]).copy()

# ─────────────────────────────────────────────────────────────
# 3) Correlations, univariate R², spend variation (CV)
# ─────────────────────────────────────────────────────────────
st.subheader("① Correlations, R², spend variation")


def _univariate_stats(df_x: pd.DataFrame, y: pd.Series) -> pd.DataFrame:
    rows = []
    y = y.astype(float)
    for col in df_x.columns:
        x = df_x[col].astype(float)
        # Pearson correlation (robust to constant via try/except)
        try:
            r = np.corrcoef(x, y)[0, 1]
        except Exception:
            r = np.nan
        # R² of y ~ 1 + x (for a single x, R² = r² when both finite)
        r2 = r**2 if pd.notna(r) else np.nan
        mean = float(x.mean())
        std = float(x.std(ddof=1))
        cv = (std / mean) if (mean != 0 and np.isfinite(mean)) else np.nan
        rows.append(
            dict(variable=col, corr_to_y=r, R2=r2, mean=mean, std=std, cv=cv)
        )
    out = pd.DataFrame(rows).sort_values("R2", ascending=False)
    return out


stats_df = _univariate_stats(df_use[drivers], df_use[dep_var])
st.dataframe(stats_df, use_container_width=True, hide_index=True)

if alt:
    # Correlation bar
    corr_bar = (
        alt.Chart(stats_df)
        .mark_bar()
        .encode(
            x=alt.X("variable:N", sort="-y"),
            y=alt.Y("corr_to_y:Q", title="Pearson r"),
            tooltip=["variable", "corr_to_y", "R2", "cv", "mean", "std"],
        )
        .properties(height=240)
    )
    st.altair_chart(corr_bar, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# 4) Time-series on drivers
# ─────────────────────────────────────────────────────────────
st.subheader("② Time-series on drivers")

if date_var == "(none)":
    st.info("Pick a date column to see time-series.")
else:
    ts_cols = drivers + [dep_var]
    ts = df_use[[date_var] + ts_cols].sort_values(date_var).copy()
    if normalize_timeseries:
        for c in ts_cols:
            s = ts[c].astype(float)
            ts[c] = (s - s.mean()) / (s.std(ddof=1) if s.std(ddof=1) else 1.0)

    long = ts.melt(
        id_vars=[date_var],
        value_vars=ts_cols,
        var_name="series",
        value_name="value",
    )

    if alt:
        line = (
            alt.Chart(long)
            .mark_line()
            .encode(
                x=alt.X(f"{date_var}:T", title="Date"),
                y=alt.Y("value:Q"),
                color="series:N",
                tooltip=[date_var, "series", "value"],
            )
            .properties(height=340)
        )
        st.altair_chart(line, use_container_width=True)
    else:
        st.dataframe(long.head(20))

# ─────────────────────────────────────────────────────────────
# 5) Redundancy tests
# ─────────────────────────────────────────────────────────────
st.subheader(
    "③ Redundancy tests: collinearity, variance, correlation, PCA, interactions"
)

X = df_use[drivers].astype(float)
# (a) Low variance flags
low_var_eps = st.number_input(
    "Near-zero variance threshold (std <)", value=1e-6, format="%.0e"
)
low_var = (X.std(ddof=1) < low_var_eps).sort_values(ascending=False)
low_var_tbl = pd.DataFrame(
    {"std": X.std(ddof=1), "near_zero": X.std(ddof=1) < low_var_eps}
).reset_index(names="variable")
st.caption("Near-zero variance features")
st.dataframe(
    low_var_tbl.sort_values("std"), use_container_width=True, hide_index=True
)

# (b) High correlation pairs among drivers
corr = X.corr(method="pearson")
thr = st.slider(
    "Flag driver-driver |r| >",
    min_value=0.5,
    max_value=0.99,
    value=0.9,
    step=0.01,
)
pairs = []
for i, j in itertools.combinations(corr.columns, 2):
    r = corr.loc[i, j]
    if pd.notna(r) and abs(r) >= thr:
        pairs.append((i, j, r))
pair_df = pd.DataFrame(pairs, columns=["var1", "var2", "r"]).sort_values(
    by="r", key=lambda s: s.abs(), ascending=False
)
st.caption("Highly correlated driver pairs")
st.dataframe(pair_df, use_container_width=True, hide_index=True)

if alt:
    # Heatmap (drivers only)
    corr_long = corr.reset_index(names="row").melt(
        "row", var_name="col", value_name="r"
    )
    heat = (
        alt.Chart(corr_long)
        .mark_rect()
        .encode(
            x=alt.X("row:N", sort=list(corr.columns)),
            y=alt.Y("col:N", sort=list(corr.columns)),
            color=alt.Color(
                "r:Q", scale=alt.Scale(scheme="redblue"), title="r"
            ),
            tooltip=["row", "col", "r"],
        )
        .properties(height=360)
    )
    st.altair_chart(heat, use_container_width=True)

# (c) VIF
st.markdown("**VIF (Variance Inflation Factor)**")


def _vif_stats(X: pd.DataFrame) -> pd.DataFrame:
    cols = list(X.columns)
    out = []
    if _HAS_STATSMODELS:
        Xc = sm.add_constant(X, has_constant="add")
        for i, col in enumerate(cols):
            try:
                out.append(
                    {
                        "variable": col,
                        "VIF": float(
                            variance_inflation_factor(Xc.values, i + 1)
                        ),
                    }
                )
            except Exception:
                out.append({"variable": col, "VIF": np.nan})
    else:
        # Fallback: regress each feature on the rest using OLS and compute R², VIF = 1 / (1 - R²)
        for col in cols:
            y = X[col].values
            X_others = X.drop(columns=[col]).values
            if X_others.shape[1] == 0:
                out.append({"variable": col, "VIF": 1.0})
                continue
            try:
                lr = LinearRegression().fit(X_others, y)
                r2 = lr.score(X_others, y)
                vif = np.inf if (1 - r2) <= 1e-12 else 1.0 / (1.0 - r2)
                out.append({"variable": col, "VIF": float(vif)})
            except Exception:
                out.append({"variable": col, "VIF": np.nan})
    return pd.DataFrame(out).sort_values("VIF", ascending=False)


vif_df = _vif_stats(X)
if not _HAS_STATSMODELS:
    st.info(
        "Statsmodels not found — using fallback VIF. For exact VIF, add `statsmodels` to requirements."
    )
st.dataframe(vif_df, use_container_width=True, hide_index=True)

# (d) PCA
st.markdown("**PCA on standardized drivers**")
scaler = StandardScaler()
Xs = scaler.fit_transform(X.values)
n_comp = st.slider(
    "Components to compute",
    min_value=2,
    max_value=min(12, X.shape[1]),
    value=min(6, X.shape[1]),
)
pca = PCA(n_components=n_comp, random_state=42).fit(Xs)

expl_var = pd.DataFrame(
    {
        "component": [f"PC{i+1}" for i in range(n_comp)],
        "explained_variance_ratio": pca.explained_variance_ratio_,
    }
)

st.dataframe(expl_var, use_container_width=True, hide_index=True)

if alt:
    ev_bar = (
        alt.Chart(expl_var)
        .mark_bar()
        .encode(
            x=alt.X("component:N", sort=None),
            y=alt.Y(
                "explained_variance_ratio:Q", title="Explained variance ratio"
            ),
            tooltip=["component", "explained_variance_ratio"],
        )
        .properties(height=240)
    )
    st.altair_chart(ev_bar, use_container_width=True)

# PCA loadings (feature -> component weights)
loadings = pd.DataFrame(
    pca.components_,
    columns=X.columns,
    index=[f"PC{i+1}" for i in range(n_comp)],
).T
st.caption("PCA loadings (feature weights)")
st.dataframe(loadings, use_container_width=True)

# (e) Simple interaction discovery: corr(y, Xi*Xj) for a small set of top features
st.markdown("**Interaction candidates (corr with target of Xi×Xj)**")
top_k = st.slider(
    "Limit base features to top-k by |corr(y, x)|",
    min_value=3,
    max_value=min(15, len(drivers)),
    value=min(8, len(drivers)),
)
ranked = (
    stats_df.reindex(columns=["variable", "corr_to_y"])
    .assign(absr=lambda d: d["corr_to_y"].abs())
    .sort_values("absr", ascending=False)
)
base_feats = ranked["variable"].head(top_k).tolist()

pairs2 = []
y = df_use[dep_var].astype(float).values
for a, b in itertools.combinations(base_feats, 2):
    prod = (df_use[a].astype(float).values) * (df_use[b].astype(float).values)
    if np.std(prod) == 0:
        r = np.nan
    else:
        r = np.corrcoef(prod, y)[0, 1]
    pairs2.append((a, b, r, abs(r) if pd.notna(r) else np.nan))
int_df = pd.DataFrame(
    pairs2, columns=["feat_a", "feat_b", "corr_prod_to_y", "abs_corr"]
).sort_values("abs_corr", ascending=False)
st.dataframe(
    int_df.drop(columns="abs_corr"), use_container_width=True, hide_index=True
)
