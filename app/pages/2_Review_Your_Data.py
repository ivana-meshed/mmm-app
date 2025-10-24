# streamlit_app_overview.py (v2.21)
import os, io, json, tempfile
from datetime import datetime
from typing import List, Dict, Tuple
from google.cloud import storage
import streamlit as st
import numpy as np
import pandas as pd
import warnings, re, io
import plotly.express as px
import plotly.graph_objects as go
from sklearn.preprocessing import PolynomialFeatures
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
from scipy import stats

st.set_page_config(page_title="Marketing Overview & Analytics", layout="wide")
st.title("Marketing Overview & Analytics")

# -----------------------------
# GCS TOOLING
# -----------------------------
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")

# Path helpers (match your layout)
def _data_root(country: str) -> str:
    return f"datasets/{country.lower().strip()}"

def _data_blob(country: str, ts: str) -> str:
    return f"{_data_root(country)}/{ts}/raw.parquet"

def _data_latest_blob(country: str) -> str:
    return f"{_data_root(country)}/latest/raw.parquet"

def _meta_blob(country: str, ts: str) -> str:
    return f"metadata/{country.lower().strip()}/{ts}/mapping.json"

def _meta_latest_blob(country: str) -> str:
    return f"metadata/{country.lower().strip()}/latest/mapping.json"

@st.cache_data(show_spinner=False)
def _list_country_versions(bucket: str, country: str) -> List[str]:
    """Return timestamp folder names available in datasets/<country>/<ts>/raw.parquet (desc)."""
    client = storage.Client()
    prefix = f"{_data_root(country)}/"
    blobs = client.list_blobs(bucket, prefix=prefix)
    ts = set()
    for b in blobs:
        parts = b.name.split("/")
        if len(parts) >= 4 and parts[-1] == "raw.parquet":
            ts.add(parts[-2])
    # Normalize: dedupe and sort newest first
    out = sorted(ts, reverse=True)
    # Ensure canonical single 'Latest' in pickers (we expose in UI later)
    return ["Latest"] + [v for v in out if str(v).lower() != "latest"]

def _download_parquet_from_gcs(bucket: str, blob_path: str) -> pd.DataFrame:
    client = storage.Client()
    blob = client.bucket(bucket).blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket}/{blob_path} not found")
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        blob.download_to_filename(tmp.name)
        df = pd.read_parquet(tmp.name)  # pyarrow / fastparquet
    return df

def _download_json_from_gcs(bucket: str, blob_path: str) -> dict:
    client = storage.Client()
    blob = client.bucket(bucket).blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket}/{blob_path} not found")
    return json.loads(blob.download_as_bytes())

@st.cache_data(show_spinner=False)
def _download_parquet_from_gcs_cached(bucket: str, blob_path: str) -> pd.DataFrame:
    return _download_parquet_from_gcs(bucket, blob_path)

@st.cache_data(show_spinner=False)
def _download_json_from_gcs_cached(bucket: str, blob_path: str) -> dict:
    return _download_json_from_gcs(bucket, blob_path)

def _parse_date(df: pd.DataFrame, meta: dict) -> Tuple[pd.DataFrame, str]:
    """Parse date field from metadata (default: 'DATE'); sort ascending."""
    date_col = str(meta.get("data", {}).get("date_field") or "DATE")
    if date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.tz_localize(None)
        df = df.sort_values(date_col).reset_index(drop=True)
    return df, date_col

def _validate_against_metadata(df: pd.DataFrame, meta: dict) -> dict:
    """
    Compare DF columns vs metadata mapping + types.
    Uses JSON's plural bucket keys and 'data_types' + 'channels'.
    Returns dict with lists/tables for display.
    """
    mapping: Dict[str, List[str]] = meta.get("mapping", {}) or {}
    data_types: Dict[str, str] = meta.get("data_types", {}) or {}

    # All variables mentioned in mapping (union across buckets)
    meta_vars = []
    for bucket, vars_ in mapping.items():
        for v in vars_ or []:
            meta_vars.append(str(v))
    meta_vars = sorted(set(meta_vars))

    df_cols = set(map(str, df.columns))

    missing_in_df = sorted([v for v in meta_vars if v not in df_cols])
    extra_in_df   = sorted([c for c in df_cols if c not in set(meta_vars)])

    # Type mismatches: compare numeric vs categorical at a coarse level
    def _is_numeric(col: str) -> str:
        if col not in df.columns: return "missing"
        return "numeric" if pd.api.types.is_numeric_dtype(df[col]) else "categorical"

    type_rows = []
    for v, t in data_types.items():
        v = str(v)
        declared = str(t or "numeric")
        observed = _is_numeric(v)
        if observed != "missing" and declared != observed:
            type_rows.append(dict(variable=v, declared=declared, observed=observed))
    type_mismatches = pd.DataFrame(type_rows)

    return {
        "missing_in_df": missing_in_df,
        "extra_in_df": extra_in_df,
        "type_mismatches": type_mismatches,
        "channels_map": meta.get("channels", {}) or {},
    }

# -----------------------------
# Paths 
# -----------------------------
st.session_state.setdefault("country", "de")
st.session_state.setdefault("picked_data_ts", "Latest")  
st.session_state.setdefault("picked_meta_ts", "Latest")

# -----------------------------
# Utilities 
# -----------------------------
def pretty(s: str) -> str:
    if s is None: return "–"
    return s if s.isupper() else s.replace("_", " ").title()

def fmt_num(x, nd=2):
    if pd.isna(x): return "–"
    a = abs(x)
    if a>=1e9: return f"{x/1e9:.{nd}f}B"
    if a>=1e6: return f"{x/1e6:.{nd}f}M"
    if a>=1e3: return f"{x/1e3:.{nd}f}k"
    return f"{x:.0f}"

def _freq_to_rule(freq: str):
    return {"D":"D","W":"W-MON","M":"MS","Q":"QS-DEC","YE":"YS"}[freq]

def period_label(series: pd.Series, freq_code: str) -> pd.Series:
    dt = pd.to_datetime(series)
    if freq_code == "YE": return dt.dt.year.astype(str)
    if freq_code == "Q":
        q = ((dt.dt.month - 1)//3 + 1).astype(str)
        return dt.dt.year.astype(str) + " Q" + q
    if freq_code == "M": return dt.dt.strftime("%b %Y")
    if freq_code in ("W-MON","W"): return dt.dt.strftime("W%U %Y")
    return dt.dt.strftime("%Y-%m-%d")

def safe_eff(frame, tgt):
    if frame is None or frame.empty or "_TOTAL_SPEND" not in frame:
        return np.nan
    s = frame["_TOTAL_SPEND"].sum()
    v = frame[tgt].sum() if tgt in frame else np.nan
    return (v/s) if s>0 else np.nan

# Simple KPI box helpers
GREEN = "#2ca02c"
RED = "#d62728"
GREY = "#777"
BORDER = "#eee"

def kpi_box(title:str, value:str, delta: str|None=None, good_when:str="up"):
    color_pos = "#2e7d32"
    color_neg = "#a94442"
    color_neu = GREY
    delta_color = ""
    if delta:
        is_up = delta.strip().startswith("+")
        if good_when == "up":
            delta_color = color_pos if is_up else color_neg
        elif good_when == "down":
            delta_color = color_pos if (not is_up) else color_neg
        else:
            delta_color = color_neu
    st.markdown(f"""
    <div style="border:1px solid {BORDER};border-radius:10px;padding:12px;">
      <div style="font-size:12px;color:{GREY};">{title}</div>
      <div style="font-size:24px;font-weight:700;">{value}</div>
      <div style="font-size:12px;color:{delta_color};">{delta or ""}</div>
    </div>
    """, unsafe_allow_html=True)

def kpi_grid(boxes:list, per_row:int=3):
    if not boxes: return
    for i in range(0, len(boxes), per_row):
        row = boxes[i:i+per_row]
        cols = st.columns(len(row))
        for c, b in zip(cols, row):
            with c:
                kpi_box(b.get("title",""), b.get("value","–"), b.get("delta"), b.get("good_when","up"))

# Colors for platforms
BASE_PLATFORM_COLORS = {
    "GA": "#1f77b4", "META": "#e377c2", "BING": "#2ca02c", "TV": "#ff7f0e",
    "PARTNERSHIP":"#17becf", "OTHER":"#7f7f7f",
}
QUAL_PALETTE = (px.colors.qualitative.D3 + px.colors.qualitative.Bold +
                px.colors.qualitative.Safe + px.colors.qualitative.Set2)

def build_platform_colors(platforms:list):
    cmap = {}
    for p in platforms:
        p_u = str(p).upper()
        for k, col in BASE_PLATFORM_COLORS.items():
            if k in p_u: cmap[p] = col; break
    i = 0
    for p in platforms:
        if p not in cmap:
            cmap[p] = QUAL_PALETTE[i % len(QUAL_PALETTE)]; i += 1
    return cmap

# -----------------------------
# Load (from GCS selections)
# -----------------------------
@st.cache_data(show_spinner=False)
def load_data_from_gcs(bucket: str, country: str, data_ts: str, meta_ts: str) -> Tuple[pd.DataFrame, dict, str]:
    """Return (df, meta, date_col) using the picker selections."""
    data_blob = _data_latest_blob(country) if data_ts == "Latest" else _data_blob(country, str(data_ts))
    meta_blob = _meta_latest_blob(country) if meta_ts == "Latest" else _meta_blob(country, str(meta_ts))
    df = _download_parquet_from_gcs(bucket, data_blob)
    meta = _download_json_from_gcs(bucket, meta_blob)
    df, date_col = _parse_date(df, meta)
    return df, meta, date_col

# If user hasn't clicked the loader yet, try to lazy-load with current session picks
if "df" not in st.session_state or "meta" not in st.session_state:
    try:
        df, meta, date_col = load_data_from_gcs(GCS_BUCKET,
                                                st.session_state["country"],
                                                st.session_state["picked_data_ts"],
                                                st.session_state["picked_meta_ts"])
        st.session_state["df"] = df
        st.session_state["meta"] = meta
        st.session_state["date_col"] = date_col
        st.session_state["channels_map"] = meta.get("channels", {}) or {}
    except Exception as e:
        st.warning(f"Data not loaded yet — use the loader above. ({e})")

# Expose for downstream code
df   = st.session_state.get("df", pd.DataFrame())
meta = st.session_state.get("meta", {}) or {}
DATE_COL = st.session_state.get("date_col", "DATE")
CHANNELS_MAP: Dict[str, str] = st.session_state.get("channels_map", {})

if df.empty or not meta:
    st.stop()

# -----------------------------
# Meta helpers  (JSON-first, with backwards-compat to m/COL/CAT/PLAT/DISP)
# -----------------------------
# 1) Display names → nice()
display_map: Dict[str, str] = meta.get("display_name_map", {}) or {}

def nice(colname: str) -> str:
    # pretty() is defined earlier in Utilities
    return display_map.get(colname, pretty(colname))

# 2) Goals (JSON-first, fallback to tabular if present)
_goals_json = meta.get("goals", []) if isinstance(meta, dict) else []
_goal_vars_json = [g.get("var") for g in _goals_json if g.get("var")]

# 3) Buckets (JSON mapping; plural keys)
_mapping = meta.get("mapping", {}) if isinstance(meta, dict) else {}
_mapping = _mapping or {}
paid_spend_cols = [c for c in _mapping.get("paid_media_spends", []) if c in df.columns]
paid_var_cols   = [c for c in _mapping.get("paid_media_vars",   []) if c in df.columns]
organic_cols    = [c for c in _mapping.get("organic_vars",      []) if c in df.columns]
# include secondary goals as drivers in context (kept behavior)
context_cols    = [c for c in _mapping.get("context_vars",      []) if c in df.columns]

# 4) Goals list used across the app
#    Prefer JSON → fall back to any tabular meta provided
goal_cols = [c for c in _goal_vars_json if c in df.columns]
if not goal_cols and isinstance(meta, pd.DataFrame) and not meta.empty:
    _mt = meta.copy()
    _mt.columns = [str(c).strip().lower() for c in _mt.columns]
    _COL = "column_name" if "column_name" in _mt.columns else None
    _CAT = "main_category" if "main_category" in _mt.columns else None
    if _COL and _CAT:
        _m_goal  = _mt.loc[_mt[_CAT].str.lower().eq("goal"), _COL].tolist()
        _m_goal2 = _mt.loc[_mt[_CAT].str.lower().eq("secondary_goal"), _COL].tolist()
        goal_cols = [c for c in (_m_goal + _m_goal2) if c in df.columns]

# 5) Build a small, compatibility "m" DataFrame so downstream code that references
#    m/COL/CAT/PLAT/DISP keeps working, even when meta is JSON.
_rows = []

# From mapping buckets (JSON-first)
_bucket_to_cat = {
    "paid_media_spends": "paid_media_spends",
    "paid_media_vars":   "paid_media_vars",
    "organic_vars":      "organic_vars",
    "context_vars":      "context_vars",
}
for k, vals in (_mapping or {}).items():
    cat = _bucket_to_cat.get(k)
    if not cat:
        continue
    for v in (vals or []):
        _rows.append(dict(column_name=str(v), main_category=cat))

# From goals (use group to tag main/secondary when provided)
for g in (_goals_json or []):
    v = g.get("var")
    if not v:
        continue
    grp = (g.get("group") or "").strip().lower()
    cat = "goal" if grp in ("primary", "main", "goal", "") else "secondary_goal"
    _rows.append(dict(column_name=str(v), main_category=cat))

# Optional platform map (JSON) + display map
_platform_map = meta.get("platform_map", {}) if isinstance(meta, dict) else {}
_platform_map = _platform_map or {}

_seen = set()
_rows_dedup = []
for r in _rows:
    key = str(r["column_name"])
    if key in _seen:
        continue
    _seen.add(key)
    r["platform"] = _platform_map.get(key)
    r["display_name"] = display_map.get(key)
    _rows_dedup.append(r)

m = pd.DataFrame(_rows_dedup, columns=["column_name","main_category","platform","display_name"])

# If meta was originally tabular, union it in — **prefer JSON fields when duplicates**
if isinstance(meta, pd.DataFrame) and not meta.empty:
    mt = meta.copy()
    mt.columns = [str(c).strip().lower() for c in mt.columns]
    for col in ["column_name","main_category","platform","display_name"]:
        if col not in mt.columns:
            mt[col] = None
    # Put JSON-derived `m` FIRST so JSON wins on duplicates
    m = (
        pd.concat([m, mt[["column_name","main_category","platform","display_name"]]], axis=0)
         .drop_duplicates(subset=["column_name"], keep="first")
         .reset_index(drop=True)
    )

# 6) Define the classic aliases used elsewhere
m.columns = [c.strip().lower() for c in m.columns]

def _pick(df_: pd.DataFrame, wanted: str, default: str | None = None) -> str | None:
    """
    Robust resolver for column aliases:
      1) exact case-sensitive
      2) exact case-insensitive
      3) case-insensitive substring match (first deterministic)
    """
    cols = list(df_.columns)
    # 1) exact case-sensitive
    if wanted in cols:
        return wanted
    # 2) exact case-insensitive
    lower_map = {c.lower(): c for c in cols}
    if wanted.lower() in lower_map:
        return lower_map[wanted.lower()]
    # 3) substring (case-insensitive), deterministic by sorted column name
    wanted_l = wanted.lower()
    subs = sorted([c for c in cols if wanted_l in c.lower()], key=str.lower)
    if subs:
        return subs[0]
    return default

COL  = _pick(m, "column_name")   or "column_name"
CAT  = _pick(m, "main_category") or "main_category"
PLAT = _pick(m, "platform")      # optional
DISP = _pick(m, "display_name")  or "display_name"

# 7) Rebuild display_map from compatibility frame if not provided in JSON
if (not display_map) and (COL in m.columns) and (DISP in m.columns):
    display_map = dict(zip(m[COL].astype(str), m[DISP].fillna("").astype(str)))

# 8) Keep context_cols behavior: include secondary goals as drivers
#    (always add, even if context_cols was initially empty; keep order & de-dup)
_sec_goals = []
if (CAT in m.columns) and (COL in m.columns):
    try:
        _sec_goals = (
            m.loc[m[CAT].astype(str).str.lower().eq("secondary_goal"), COL]
              .dropna().astype(str).tolist()
        )
    except Exception:
        _sec_goals = []

# Compose, ensure presence in df, keep first occurrence order
_context_plus = list(dict.fromkeys(list(context_cols or []) + _sec_goals))
context_cols = [c for c in _context_plus if c in df.columns]

# 9) Traffic metric helpers (unchanged; required elsewhere)
ALL_COLS_UP = {c: c.upper() for c in df.columns}
def cols_like(keyword: str):
    kw = keyword.upper()
    return [c for c, u in ALL_COLS_UP.items() if kw in u]

IMPR_COLS    = cols_like("IMPRESSION")
CLICK_COLS   = cols_like("CLICK")
SESSION_COLS = cols_like("SESSION")
INSTALL_COLS = [c for c in cols_like("INSTALL") + cols_like("APP_INSTALL")]

# -----------------------------
# Sidebar (Country → Goal → Timeframe → Aggregation)
# -----------------------------
with st.sidebar:
    # Country picker (only if present)
    if "COUNTRY" in df.columns:
        country_list = sorted(df["COUNTRY"].dropna().astype(str).unique().tolist())
        default_countries = country_list or []
        sel_countries = st.multiselect("Country (multi-select)", country_list, default=default_countries)
    else:
        sel_countries = []
        st.caption("Dataset has no COUNTRY column — showing all rows.")

    # Goal picker
    if not goal_cols:
        st.error("No goals found in metadata.")
        GOAL = None
        goal_label_to_col = {}
    else:
        # Prefer JSON 'group' when available, else tabular CAT
        _group_fallback = {}
        if isinstance(meta, dict):
            _group_fallback = {g.get("var"): (g.get("group") or "primary") for g in meta.get("goals", []) if g.get("var")}
        def _goal_tag_for(col: str) -> str:
            g = (_group_fallback.get(col, "") or "").strip().lower()
            if g in ("primary", "main", "goal", ""):
                return "Main"
            if g in ("secondary", "alt", "secondary_goal"):
                return "Secondary"
            # Fall back to tabular m[CAT]
            if (COL in m.columns) and (CAT in m.columns):
                try:
                    cat = m.loc[m[COL].eq(col), CAT].iloc[0]
                    return "Main" if str(cat).lower()=="goal" else ("Secondary" if str(cat).lower()=="secondary_goal" else "Other")
                except Exception:
                    pass
            return "Other"

        def _goal_label(col: str) -> str:
            return f"{nice(col)}  ·  {_goal_tag_for(col)}"

        goal_label_to_col = {_goal_label(c): c for c in goal_cols}
        labels_sorted = sorted(goal_label_to_col.keys(), key=lambda s: s.lower())

        # Default to GMV if available, else first
        default_label = _goal_label("GMV") if "GMV" in goal_cols else labels_sorted[0]

        GOAL = goal_label_to_col[
            st.selectbox("Goal", labels_sorted, index=labels_sorted.index(default_label))
        ]

    # Timeframe picker
    tf_label_map = {
        "LAST 6 MONTHS": "6m",
        "LAST 12 MONTHS": "12m",
        "CURRENT YEAR": "cy",
        "LAST YEAR": "ly",
        "LAST 2 YEARS": "2y",
        "ALL": "all",
    }
    _tf_labels = list(tf_label_map.keys())
    TIMEFRAME_LABEL = st.selectbox("Timeframe", _tf_labels, index=0)
    RANGE = tf_label_map[TIMEFRAME_LABEL]

    # Aggregation picker
    agg_map = {"Daily": "D", "Weekly (ISO Mon-start)": "W", "Monthly": "M", "Quarterly": "Q", "Yearly": "YE"}
    agg_label = st.selectbox("Aggregation", list(agg_map.keys()), index=2)
    FREQ = agg_map[agg_label]

# Country filter
if sel_countries and "COUNTRY" in df:
    df = df[df["COUNTRY"].astype(str).isin(sel_countries)].copy()

# -----------------------------
# Target, spend, platforms
# -----------------------------
# Choose target (goal) robustly
target = GOAL if (GOAL and GOAL in df.columns) else (goal_cols[0] if goal_cols else None)

# Spend columns present
present_spend = [c for c in (paid_spend_cols or []) if c in df.columns]

# Total spend column (exists even if no spend cols -> zeros)
df["_TOTAL_SPEND"] = df[present_spend].sum(axis=1) if present_spend else 0.0

# Frequency rule + label for plots
RULE = _freq_to_rule(FREQ)
spend_label = (meta.get("labels", {}) or {}).get("spend", "Spend") if isinstance(meta, dict) else "Spend"

# Build platform mapping with JSON-first fallbacks
# Priority:
#  1) meta["platform_map"] : {"COL":"PLATFORM", ...}
#  2) tabular m[PLAT] for present_spend
#  3) regex prefix from column names
plat_map_df = pd.DataFrame(columns=["col", "platform"])
if present_spend:
    # (1) JSON platform_map
    _plat_map_json = meta.get("platform_map", {}) if isinstance(meta, dict) else {}
    _plat_map_json = _plat_map_json or {}
    rows = []
    if _plat_map_json:
        for c in present_spend:
            p = _plat_map_json.get(c)
            if p:
                rows.append((c, str(p)))
    if rows:
        plat_map_df = pd.DataFrame(rows, columns=["col", "platform"])

    # (2) Tabular m[PLAT] as fallback/augment
    if plat_map_df.empty and (PLAT in m.columns) and (COL in m.columns):
        pm = m.loc[m[COL].isin(present_spend), [COL, PLAT]].dropna()
        if not pm.empty:
            plat_map_df = pm.rename(columns={COL: "col", PLAT: "platform"}).copy()

    # (3) Derive from column prefix if still empty
    if plat_map_df.empty:
        derived = []
        for c in present_spend:
            m0 = re.match(r"([A-Za-z0-9]+)_", c)
            plat = m0.group(1).upper() if m0 else "OTHER"
            derived.append((c, plat))
        plat_map_df = pd.DataFrame(derived, columns=["col", "platform"])

# Optional channel normalization (e.g., "Facebook" -> "META")
# CHANNELS_MAP is already exposed from loader step (meta.get("channels", {}))
if not plat_map_df.empty and CHANNELS_MAP:
    _norm = {str(k).upper(): str(v) for k, v in CHANNELS_MAP.items()}
    plat_map_df["platform"] = plat_map_df["platform"].astype(str).map(lambda x: _norm.get(x.upper(), x))

# Unique platforms + palette
platforms = plat_map_df["platform"].dropna().astype(str).unique().tolist() if not plat_map_df.empty else []
PLATFORM_COLORS = build_platform_colors(platforms)

# -----------------------------
# Timeframe & resample
# -----------------------------
date_max = df[DATE_COL].max()

def filter_range(d: pd.DataFrame) -> pd.DataFrame:
    if RANGE == "all": return d
    if RANGE == "2y":  return d[d[DATE_COL] >= (date_max - pd.DateOffset(years=2))]
    if RANGE == "ly":
        today = pd.Timestamp.today().normalize()
        start = pd.Timestamp(year=today.year-1, month=1, day=1)
        end   = pd.Timestamp(year=today.year-1, month=12, day=31, hour=23, minute=59, second=59)
        return d[(d[DATE_COL] >= start) & (d[DATE_COL] <= end)]
    if RANGE == "12m":
        today = pd.Timestamp.today().normalize()
        start_of_this_month = pd.Timestamp(year=today.year, month=today.month, day=1)
        start = start_of_this_month - pd.DateOffset(months=11)
        return d[d[DATE_COL] >= start]
    if RANGE == "1y":  # legacy
        return d[d[DATE_COL] >= (date_max - pd.DateOffset(years=1))]
    if RANGE == "cy":
        start = pd.Timestamp(year=pd.Timestamp.today().year, month=1, day=1)
        return d[d[DATE_COL] >= start]
    if RANGE == "6m":
        today = pd.Timestamp.today().normalize()
        start_of_this_month = pd.Timestamp(year=today.year, month=today.month, day=1)
        start = start_of_this_month - pd.DateOffset(months=5)
        return d[d[DATE_COL] >= start]
    return d

df_r = filter_range(df.copy())

def previous_window(full_df: pd.DataFrame, current_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the comparison window for the selected RANGE.
    - For RANGE == "cy": compare YTD this year vs YTD last year (same day-of-year).
    - Otherwise: use the immediately-preceding window of equal length.
    """
    if current_df.empty:
        return full_df.iloc[0:0]

    cur_start, cur_end = current_df[DATE_COL].min(), current_df[DATE_COL].max()
    span = (cur_end - cur_start)

    if RANGE == "cy":
        this_year = pd.Timestamp.today().year
        start_prev = pd.Timestamp(year=this_year-1, month=1, day=1)
        same_day_prev_year = pd.Timestamp(year=this_year-1, month=cur_end.month, day=cur_end.day)
        end_prev = min(same_day_prev_year, full_df[DATE_COL].max())
        return full_df[(full_df[DATE_COL] >= start_prev) & (full_df[DATE_COL] <= end_prev)].copy()

    if RANGE == "all":
        return full_df.iloc[0:0]

    prev_end = cur_start - pd.Timedelta(days=1)
    prev_start = prev_end - span
    return full_df[(full_df[DATE_COL] >= prev_start) & (full_df[DATE_COL] <= prev_end)].copy()

df_prev = previous_window(df, df_r)

num_cols = df_r.select_dtypes(include=[np.number]).columns
res = (df_r.set_index(DATE_COL)[num_cols]
       .resample(RULE).sum(min_count=1)
       .reset_index().rename(columns={DATE_COL: "DATE_PERIOD"}))
for must in [target, "_TOTAL_SPEND"]:
    if (must is not None) and (must in df_r.columns) and (must not in res.columns):
        add = (
            df_r.set_index(DATE_COL)[[must]]
                .resample(RULE).sum(min_count=1)
                .reset_index().rename(columns={DATE_COL: "DATE_PERIOD"})
        )
        res = res.merge(add, on="DATE_PERIOD", how="left")
res["PERIOD_LABEL"] = period_label(res["DATE_PERIOD"], RULE)

# Helper: totals for current vs previous window
def total_with_prev(collist):
    cur = df_r[collist].sum().sum() if collist else np.nan
    prev = (
        df_prev[collist].sum().sum()
        if (not df_prev.empty and all(c in df_prev.columns for c in collist))
        else np.nan
    )
    return cur, (cur - prev) if pd.notna(prev) else None

# -----------------------------
# Tabs
# -----------------------------
tab_load, tab_biz, tab_reg, tab_mkt, tab_rel, tab_diag = st.tabs(
    ["Data & Metadata Loader", "Business Overview", "Regional Comparison", "Marketing Overview", "Relationships", "Collinearity & PCA (by Country)"]
)

# =============================
# TAB 0 — DATA & METADATA LOADER
# =============================
with tab_load:
    st.markdown("### 📥 Load dataset & metadata from GCS")

    c1, c2, c3 = st.columns([1.2, 1, 1])

    country = c1.text_input("Country (ISO2)", value=st.session_state["country"]).strip().lower()
    if country:
        st.session_state["country"] = country

    versions = _list_country_versions(GCS_BUCKET, country) if country else ["Latest"]
    data_ts = c2.selectbox("Data version", options=versions, index=0, key="picked_data_ts")
    meta_ts = c3.selectbox("Metadata version", options=versions, index=0, key="picked_meta_ts")

    load_clicked = st.button("Load from GCS", type="primary")

    if load_clicked:
        try:
            data_blob = _data_latest_blob(country) if data_ts == "Latest" else _data_blob(country, str(data_ts))
            meta_blob = _meta_latest_blob(country) if meta_ts == "Latest" else _meta_blob(country, str(meta_ts))

            df = _download_parquet_from_gcs_cached(GCS_BUCKET, data_blob)
            meta = _download_json_from_gcs_cached(GCS_BUCKET, meta_blob)
            df, date_col = _parse_date(df, meta)

            st.session_state["df"] = df
            st.session_state["meta"] = meta
            st.session_state["date_col"] = date_col
            st.session_state["channels_map"] = meta.get("channels", {}) or {}

            report = _validate_against_metadata(df, meta)
            st.success(
                f"Loaded {len(df):,} rows from gs://{GCS_BUCKET}/{data_blob} "
                f"and metadata gs://{GCS_BUCKET}/{meta_blob}"
            )

            m1, m2 = st.columns([1, 1])
            with m1:
                st.markdown("**Columns in metadata but missing in data**")
                st.write(report["missing_in_df"] or "— none —")
            with m2:
                st.markdown("**Columns in data but not in metadata**")
                st.write(report["extra_in_df"] or "— none —")

            if not report["type_mismatches"].empty:
                st.warning("Declared vs observed type mismatches:")
                st.dataframe(report["type_mismatches"], use_container_width=True, hide_index=True)
            else:
                st.caption("No type mismatches detected (coarse check).")

        except Exception as e:
            st.error(f"Load failed: {e}")

# =============================
# TAB 1 — BUSINESS OVERVIEW
# =============================
with tab_biz:
    st.markdown("## KPI Overview")

    has_prev = not df_prev.empty
    if goal_cols:
        kpis=[]
        for g in goal_cols:
            cur = df_r[g].sum() if g in df_r else np.nan
            prev = df_prev[g].sum() if (has_prev and g in df_prev) else np.nan
            delta_txt = None
            if pd.notna(prev):
                diff = cur - prev
                delta_txt = f"{'+' if diff>=0 else ''}{fmt_num(diff)}"
            kpis.append(dict(title=f"Total {nice(g)}", value=fmt_num(cur), delta=delta_txt, good_when="up"))
        kpi_grid(kpis, per_row=5)
        st.markdown("---")

        kpis2=[]
        for g in goal_cols:
            eff_name = "ROAS" if g == "GMV" else f"{nice(g)}/Spend"
            cur_eff = safe_eff(df_r, g)
            prev_eff = safe_eff(df_prev, g) if has_prev else np.nan
            delta_txt = None
            if pd.notna(cur_eff) and pd.notna(prev_eff):
                diff = cur_eff - prev_eff
                delta_txt = f"{'+' if diff>=0 else ''}{diff:.2f}"
            kpis2.append(dict(title=f"Avg {eff_name}",
                              value=("–" if pd.isna(cur_eff) else f"{cur_eff:.2f}"),
                              delta=delta_txt, good_when="up"))
        kpi_grid(kpis2, per_row=5)
        st.markdown("---")

    st.markdown("## Goal vs Spend")
    cA, cB = st.columns(2)
    with cA:
        fig1 = go.Figure()
        if target and target in res:
            fig1.add_bar(x=res["PERIOD_LABEL"], y=res[target], name=nice(target))
        fig1.add_trace(go.Scatter(
            x=res["PERIOD_LABEL"], y=res["_TOTAL_SPEND"],
            name=f"Total {spend_label}", yaxis="y2", mode="lines+markers", line=dict(color=RED)
        ))
        fig1.update_layout(
            title=f"{nice(target) if target else 'Goal'} vs Total {spend_label} — {TIMEFRAME_LABEL}, {agg_label}",
            xaxis_title="Date", yaxis=dict(title=nice(target) if target else "Goal"),
            yaxis2=dict(title=spend_label, overlaying="y", side="right"),
            bargap=0.15, hovermode="x unified", legend=dict(orientation="h")
        )
        st.plotly_chart(fig1, use_container_width=True)

    with cB:
        eff_t = res.copy()
        label_eff = "ROAS" if target == "GMV" else "Efficiency"
        if target and target in eff_t.columns and "_TOTAL_SPEND" in eff_t:
            eff_t["EFF"] = np.where(eff_t["_TOTAL_SPEND"]>0, eff_t[target]/eff_t["_TOTAL_SPEND"], np.nan)
        else:
            eff_t["EFF"] = np.nan
        fig2e = go.Figure()
        if target and target in eff_t:
            fig2e.add_trace(go.Bar(x=eff_t["PERIOD_LABEL"], y=eff_t[target], name=nice(target)))
        fig2e.add_trace(go.Scatter(x=eff_t["PERIOD_LABEL"], y=eff_t["EFF"], name=label_eff,
                                   yaxis="y2", mode="lines+markers", line=dict(color=GREEN)))
        fig2e.update_layout(
            title=f"{nice(target) if target else 'Goal'} & {label_eff} Over Time — {TIMEFRAME_LABEL}, {agg_label}",
            xaxis_title="Date", yaxis=dict(title=nice(target) if target else "Goal"),
            yaxis2=dict(title=label_eff, overlaying="y", side="right"),
            bargap=0.15, hovermode="x unified", legend=dict(orientation="h")
        )
        st.plotly_chart(fig2e, use_container_width=True)
    st.markdown("---")

# =============================
# TAB 2 — REGIONAL COMPARISON (v2.8.2)
# =============================
with tab_reg:
    st.subheader("Regional Comparison")

    # ---- Goal by country over time (stacked) ----
    if "COUNTRY" in df_r.columns and target and target in df_r:
        agg = (df_r.set_index(DATE_COL).groupby("COUNTRY")[target]
            .resample(RULE).sum(min_count=1).reset_index().rename(columns={DATE_COL: "DATE_PERIOD"}))
        agg["PERIOD_LABEL"] = period_label(agg["DATE_PERIOD"], RULE)
        tot = agg.groupby("COUNTRY")[target].sum().sort_values(ascending=False).index.tolist()
        fig_cty = px.bar(
            agg, x="PERIOD_LABEL", y=target, color="COUNTRY",
            category_orders={"COUNTRY": tot},
            title=f"{nice(target)} by Country — Stacked Over Time",
            barmode="stack"
        )
        fig_cty.update_layout(xaxis_title="Date", yaxis_title=nice(target), legend=dict(orientation="h"))
        st.plotly_chart(fig_cty, use_container_width=True)

    # ---- Country comparison table (outcomes, spend, conversions) ----
    if "COUNTRY" in df_r:
        g = df_r.groupby("COUNTRY", dropna=False)
        spend_s  = g["_TOTAL_SPEND"].sum()
        target_s = g[target].sum() if (target in df_r.columns) else spend_s

        imps_row     = df_r[IMPR_COLS].sum(axis=1)     if IMPR_COLS else pd.Series(0.0, index=df_r.index)
        clicks_row   = df_r[CLICK_COLS].sum(axis=1)    if CLICK_COLS else pd.Series(0.0, index=df_r.index)
        sessions_row = df_r[SESSION_COLS].sum(axis=1)  if SESSION_COLS else pd.Series(0.0, index=df_r.index)

        imps_s     = imps_row.groupby(df_r["COUNTRY"]).sum()
        clicks_s   = clicks_row.groupby(df_r["COUNTRY"]).sum()
        sessions_s = sessions_row.groupby(df_r["COUNTRY"]).sum()

        by_cty = pd.DataFrame({
            "spend":    spend_s,
            "target":   target_s,
            "imps":     imps_s.reindex(spend_s.index, fill_value=0.0),
            "clicks":   clicks_s.reindex(spend_s.index, fill_value=0.0),
            "sessions": sessions_s.reindex(spend_s.index, fill_value=0.0),
        }).reset_index()

        by_cty["Impression→Click"] = by_cty["clicks"]   / by_cty["imps"].replace(0, np.nan)
        by_cty["Click→Session"]    = by_cty["sessions"] / by_cty["clicks"].replace(0, np.nan)
        by_cty["Efficiency"]       = by_cty["target"]   / by_cty["spend"].replace(0, np.nan)

        disp = by_cty.sort_values("spend", ascending=False)
        disp_fmt = disp.copy()
        disp_fmt["Impressions"] = disp["imps"].map(fmt_num)
        disp_fmt["Clicks"]      = disp["clicks"].map(fmt_num)
        disp_fmt["Sessions"]    = disp["sessions"].map(fmt_num)
        disp_fmt["Total Spend"] = disp["spend"].map(fmt_num)
        disp_fmt[f"Total {nice(target)}"] = disp["target"].map(fmt_num)
        disp_fmt["Impression→Click"] = disp["Impression→Click"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "–")
        disp_fmt["Click→Session"]    = disp["Click→Session"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "–")
        perf_col_name = "ROAS" if target=="GMV" else f"{nice(target)}/Spend"
        disp_fmt[perf_col_name] = disp["Efficiency"].map(lambda x: f"{x:.2f}" if pd.notna(x) else "–")

        st.markdown("### Country Comparison — Outcomes, Spend & Conversions")
        st.dataframe(
            disp_fmt[["COUNTRY","Impressions","Clicks","Sessions","Total Spend", f"Total {nice(target)}",
                      "Impression→Click","Click→Session", perf_col_name]].rename(columns={"COUNTRY":"Country"}),
            use_container_width=True
        )
    st.markdown("---")

    # ---- Platform KPIs table (by platform) ----
    if not plat_map_df.empty:
        def sum_like(cols, token):
            cols_tok = [c for c in cols if token in c.upper()]
            return df_r[cols_tok].sum().sum() if cols_tok else 0.0

        rows=[]
        for p in platforms:
            token = p.upper()
            spend_cols = plat_map_df.loc[plat_map_df["platform"]==p, "col"].tolist()
            spend_total = df_r[spend_cols].sum().sum() if spend_cols else 0.0
            imps = sum_like(IMPR_COLS, token); clicks = sum_like(CLICK_COLS, token); sessions = sum_like(SESSION_COLS, token)
            installs = sum_like(INSTALL_COLS, token)
            cpm = (spend_total / imps * 1000) if imps>0 else np.nan
            cpc = (spend_total / clicks) if clicks>0 else np.nan
            cps = (spend_total / sessions) if sessions>0 else np.nan
            cpi = (spend_total / installs) if installs>0 else np.nan
            rows.append([p, imps, clicks, sessions, spend_total, cpm, cpc, cps, cpi])
        plat_cmp = pd.DataFrame(rows, columns=[
            "Platform","Impressions","Clicks","Sessions","Spend",
            "Cost per 1k Impressions","Cost per Click","Cost per Session","Cost per Install"
        ]).sort_values("Spend", ascending=False)

        plat_disp = plat_cmp.copy()
        for c in ["Impressions","Clicks","Sessions","Spend"]:
            plat_disp[c] = plat_disp[c].map(fmt_num)
        for c in ["Cost per 1k Impressions","Cost per Click","Cost per Session","Cost per Install"]:
            plat_disp[c] = plat_cmp[c].map(lambda x: f"{x:.2f}" if pd.notna(x) else "–")

        st.markdown("### Platform KPIs — Outcomes & Costs")
        st.dataframe(plat_disp, use_container_width=True)
    st.markdown("---")

    # ---- Country × Platform Spend Matrix + CSV ----
    st.markdown("### Country × Platform Spend Matrix")
    if not plat_map_df.empty and "COUNTRY" in df_r.columns:
        long = (
            df_r.melt(id_vars=[DATE_COL, "COUNTRY"], value_vars=plat_map_df["col"].tolist(),
                    var_name="col", value_name="spend")
            .merge(plat_map_df, on="col", how="left")
            .dropna(subset=["spend"])
        )
        mat = (long.groupby(["COUNTRY","platform"])["spend"]
                    .sum()
                    .unstack("platform")
                    .fillna(0.0))
        mat = mat.loc[mat.sum(axis=1).sort_values(ascending=False).index]
        st.dataframe(mat.applymap(fmt_num), use_container_width=True)

        csv = mat.to_csv().encode("utf-8")
        st.download_button(
            "Download CSV",
            data=csv,
            file_name="country_platform_spend_matrix.csv",
            mime="text/csv",
            key="dl_country_platform_matrix"
        )
    else:
        st.info("Platform mapping or COUNTRY column not available.")

# =============================
# TAB 3 — MARKETING OVERVIEW
# =============================
with tab_mkt:
    st.subheader(f"Spend & Channels — {TIMEFRAME_LABEL} · {agg_label}")

    # ----- KPIs -----
    st.markdown("#### Outcomes & Spend")
    cur_imps, d_imps = total_with_prev(IMPR_COLS)
    cur_clicks, d_clicks = total_with_prev(CLICK_COLS)
    cur_sessions, d_sessions = total_with_prev(SESSION_COLS)
    kpi_grid([
        dict(title="Total Impressions", value=fmt_num(cur_imps),
             delta=(f"{'+' if (d_imps or 0)>=0 else ''}{fmt_num(d_imps)}" if d_imps is not None else None), good_when="up"),
        dict(title="Total Clicks", value=fmt_num(cur_clicks),
             delta=(f"{'+' if (d_clicks or 0)>=0 else ''}{fmt_num(d_clicks)}" if d_clicks is not None else None), good_when="up"),
        dict(title="Total Sessions", value=fmt_num(cur_sessions),
             delta=(f"{'+' if (d_sessions or 0)>=0 else ''}{fmt_num(d_sessions)}" if d_sessions is not None else None), good_when="up"),
    ], per_row=3)

    cur_spend, d_spend = total_with_prev(["_TOTAL_SPEND"])
    boxes = [dict(title="Total Spend", value=fmt_num(cur_spend),
                  delta=(f"{'+' if (d_spend or 0)>=0 else ''}{fmt_num(d_spend)}" if d_spend is not None else None),
                  good_when="down")]
    if not plat_map_df.empty and not df_r.empty:
        long_sp = (
            df_r.melt(
                id_vars=[DATE_COL],
                value_vars=plat_map_df["col"].tolist(),
                var_name="col",
                value_name="spend",
            )
            .merge(plat_map_df, on="col", how="left")
            .dropna(subset=["spend"])
        )
        plat_tot_cur = long_sp.groupby("platform")["spend"].sum().sort_values(ascending=False)

        if not df_prev.empty:
            long_prev = (
                df_prev.melt(
                    id_vars=[DATE_COL],
                    value_vars=plat_map_df["col"].tolist(),
                    var_name="col",
                    value_name="spend",
                )
                .merge(plat_map_df, on="col", how="left")
                .dropna(subset=["spend"])
            )
            plat_tot_prev = long_prev.groupby("platform")["spend"].sum()
        else:
            plat_tot_prev = pd.Series(dtype=float)

        for p, v in plat_tot_cur.items():
            delta = None
            if p in plat_tot_prev:
                dv = v - plat_tot_prev.get(p, 0.0)
                delta = f"{'+' if dv>=0 else ''}{fmt_num(dv)}"
            boxes.append(
                dict(title=f"{p} Costs", value=fmt_num(v), delta=delta, good_when="down")
            )
    kpi_grid(boxes, per_row=5)
    st.markdown("---")

    # ----- Waterfall -----
    st.markdown("#### Change vs Previous — Waterfall (Spend by Platform)")
    if not plat_map_df.empty and not df_r.empty:
        long_cur = (df_r.melt(id_vars=[DATE_COL], value_vars=plat_map_df["col"].tolist(),
                            var_name="col", value_name="spend")
                    .merge(plat_map_df, on="col", how="left").dropna(subset=["spend"]))
        cur_by_p = long_cur.groupby("platform")["spend"].sum()
        if not df_prev.empty:
            long_prev = (df_prev.melt(id_vars=[DATE_COL], value_vars=plat_map_df["col"].tolist(),
                                    var_name="col", value_name="spend")
                        .merge(plat_map_df, on="col", how="left").dropna(subset=["spend"]))
            prev_by_p = long_prev.groupby("platform")["spend"].sum()
        else:
            prev_by_p = pd.Series(dtype=float)

        all_p = sorted(set(cur_by_p.index).union(prev_by_p.index), key=lambda x: cur_by_p.get(x,0.0), reverse=True)
        steps = []
        total_delta = 0.0
        for p in all_p:
            dv = cur_by_p.get(p,0.0) - prev_by_p.get(p,0.0)
            total_delta += dv
            steps.append(dict(name=p, measure="relative", y=dv))
        steps.insert(0, dict(name="Start (Prev Total)", measure="absolute", y=float(prev_by_p.sum())))
        steps.append(dict(name="End (Current Total)", measure="total", y=float(prev_by_p.sum()+total_delta)))

        fig_w = go.Figure(go.Waterfall(
            name="Delta", orientation="v",
            measure=[s["measure"] for s in steps],
            x=[s["name"] for s in steps],
            y=[s["y"] for s in steps],
        ))
        fig_w.update_layout(title="Spend Change by Platform — Waterfall", showlegend=False)
        st.plotly_chart(fig_w, use_container_width=True)
    else:
        st.info("Platform mapping not available for waterfall.")
    st.markdown("---")

    # ----- Channel Mix -----
    st.markdown("#### Channel Mix")
    if not plat_map_df.empty and not df_r.empty:
        long = (df_r.melt(id_vars=[DATE_COL], value_vars=plat_map_df["col"].tolist(),
                        var_name="col", value_name="spend")
                .merge(plat_map_df, on="col", how="left").dropna(subset=["spend"]))
        plat_freq = (long.set_index(DATE_COL).groupby("platform")["spend"]
                        .resample(RULE).sum(min_count=1).reset_index().rename(columns={DATE_COL:"DATE_PERIOD"}))
        plat_freq["PERIOD_LABEL"] = period_label(plat_freq["DATE_PERIOD"], RULE)
        platform_order = (plat_freq.groupby("platform")["spend"].sum().sort_values(ascending=False).index.tolist())

        fig2 = px.bar(
            plat_freq, x="PERIOD_LABEL", y="spend", color="platform",
            category_orders={"platform": platform_order},
            color_discrete_map=PLATFORM_COLORS,
            title=f"{spend_label} by Platform — {TIMEFRAME_LABEL}, {agg_label}"
        )
        fig2.update_layout(barmode="stack", xaxis_title="Date", yaxis_title=spend_label, legend=dict(orientation="h"))
        st.plotly_chart(fig2, use_container_width=True)
    st.markdown("---")

    # ----- Channel Funnels -----
    st.markdown("#### Channel Funnels")
    if not plat_map_df.empty:
        def find_metric_cols(token: str, keyword: str):
            kw = keyword.upper()
            return [c for c,u in ALL_COLS_UP.items() if token in u and kw in u]

        funnels = []
        for plat in platforms:
            token = plat.upper()
            spend_cols_for_plat = plat_map_df.loc[plat_map_df["platform"]==plat, "col"].tolist()
            spend_total = df_r[spend_cols_for_plat].sum().sum() if spend_cols_for_plat else 0.0
            sess_cols = find_metric_cols(token, "SESSION")
            click_cols = find_metric_cols(token, "CLICK")
            impr_cols  = find_metric_cols(token, "IMPRESSION")
            sessions = df_r[sess_cols].sum().sum() if sess_cols else 0.0
            clicks   = df_r[click_cols].sum().sum() if click_cols else 0.0
            imps     = df_r[impr_cols].sum().sum() if impr_cols else 0.0
            installs_cols = [c for c in INSTALL_COLS if token in c.upper()]
            installs = df_r[installs_cols].sum().sum() if installs_cols else 0.0

            cpm = (spend_total / imps * 1000) if imps>0 else np.nan
            cpc = (spend_total / clicks) if clicks>0 else np.nan
            cps = (spend_total / sessions) if sessions>0 else np.nan

            funnels.append(dict(
                platform=plat, imps=imps, clicks=clicks, sessions=sessions, installs=installs,
                CPM=cpm, CPC=cpc, CPS=cps, Spend=spend_total
            ))

        for f in funnels:
            col_left, col_right = st.columns([2,1])
            with col_left:
                st.markdown(f"**{f['platform']}**")
                steps = []
                if f["imps"]>0: steps.append(("Impressions", f["imps"]))
                if f["clicks"]>0: steps.append(("Clicks", f["clicks"]))
                if f["sessions"]>0: steps.append(("Sessions", f["sessions"]))
                if f["installs"]>0: steps.append(("Installs", f["installs"]))
                if steps:
                    labels = [s[0] for s in steps]
                    values = [s[1] for s in steps]
                    figf = go.Figure(go.Funnel(
                        y=labels, x=values,
                        text=[fmt_num(v, nd=2) for v in values],
                        textinfo="text+percent previous",
                        hovertemplate="%{label}: %{value:,}"
                    ))
                    figf.update_layout(margin=dict(l=40,r=20,t=10,b=20))
                    st.plotly_chart(figf, use_container_width=True)
                else:
                    st.info("No funnel metrics found.")
            with col_right:
                tbl = pd.DataFrame({
                    "Metric": [
                        "Total Spend","Impressions","Clicks","Sessions",
                        "Cost per 1k Impressions","Cost per Click","Cost per Session",
                        "Impression→Click rate","Click→Session rate"
                    ],
                    "Value": [
                        fmt_num(f["Spend"]),
                        fmt_num(f["imps"]), fmt_num(f["clicks"]), fmt_num(f["sessions"]),
                        (f"{f['CPM']:.2f}" if pd.notna(f["CPM"]) else "–"),
                        (f"{f['CPC']:.2f}" if pd.notna(f["CPC"]) else "–"),
                        (f"{f['CPS']:.2f}" if pd.notna(f["CPS"]) else "–"),
                        (f"{(f['clicks']/f['imps']):.2%}" if f["imps"]>0 else "–"),
                        (f"{(f['sessions']/f['clicks']):.2%}" if f["clicks"]>0 else "–"),
                    ]
                })
                st.dataframe(tbl, hide_index=True, use_container_width=True)
            st.markdown("---")

# =============================
# TAB 4 — RELATIONSHIPS (v2.12 — liberal MMM exploration)
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
        help="Remove outliers"
    )
    _mode_map = {"No":"None", "Upper only":"Upper", "Upper and lower":"Both"}
    wins_mode = _mode_map[wins_mode_label]
    if wins_mode != "None":
        wins_pct_label = st.selectbox("Winsorize level (keep up to this percentile)", ["99","98","95"], index=0)
        wins_pct = int(wins_pct_label)
    else:
        wins_pct = 99  # unused when "None"

    def winsorize_columns(frame: pd.DataFrame, cols: list, mode: str, pct: int) -> pd.DataFrame:
        if mode == "None" or not cols:
            return frame
        dfw = frame.copy()
        upper_q = pct/100.0
        lower_q = 1 - upper_q if mode == "Both" else None
        for c in cols:
            if c not in dfw.columns:
                continue
            s = pd.to_numeric(dfw[c], errors="coerce")
            if s.notna().sum() == 0:
                continue
            hi = s.quantile(upper_q)
            if mode == "Upper":
                dfw[c] = np.where(s>hi, hi, s)
            else:
                lo = s.quantile(lower_q)
                dfw[c] = s.clip(lower=lo, upper=hi)
        return dfw

    # helper: correlation matrix (drivers x goals)
    def corr_matrix(frame: pd.DataFrame, drivers: list, goals: list, min_n: int = 8) -> pd.DataFrame:
        if frame.empty or not drivers or not goals:
            return pd.DataFrame(index=drivers, columns=goals, dtype=float)
        out = pd.DataFrame(index=[nice(c) for c in drivers], columns=[nice(g) for g in goals], dtype=float)
        for d in drivers:
            if d not in frame: continue
            for g in goals:
                if g not in frame: continue
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

    # buckets & columns used
    buckets = {
        "Paid Media Spend": paid_spend_cols,
        "Paid Media Variables": paid_var_cols,
        "Organic Variables": organic_cols,
        "Context Variables": context_cols,   # includes secondary goals
    }
    all_driver_cols = [c for cols in buckets.values() for c in cols]
    all_corr_cols = list(set(all_driver_cols + goal_cols))

    # Apply winsorization (current + previous independently)
    df_r_w    = winsorize_columns(df_r,   all_corr_cols, wins_mode, wins_pct)
    df_prev_w = winsorize_columns(df_prev,all_corr_cols, wins_mode, wins_pct)

    # heatmap helpers: green=+1, red=−1, % labels
    def as_pct_text(df_vals: pd.DataFrame) -> pd.DataFrame:
        return df_vals.applymap(lambda v: (f"{v*100:.1f}%" if pd.notna(v) else ""))

    def heatmap_fig_from_matrix(mat: pd.DataFrame, title=None, zmin=-1, zmax=1):
        fig = go.Figure(go.Heatmap(
            z=mat.values, x=list(mat.columns), y=list(mat.index),
            zmin=zmin, zmax=zmax, colorscale="RdYlGn",
            text=as_pct_text(mat).values, texttemplate="%{text}",
            hovertemplate="Driver: %{y}<br>Goal: %{x}<br>r: %{z:.2f}<extra></extra>",
            colorbar=dict(title="r")
        ))
        if title:
            fig.update_layout(title=title)
        fig.update_layout(xaxis=dict(side="top"))
        return fig

    # ---------- (A) Correlations vs Goals ----------
    st.markdown("### A) Correlations vs Goals")
    if not goal_cols:
        st.info("No goals found in metadata.")
    else:
        cats = [(title, cols) for title, cols in buckets.items() if cols]
        for i in range(0, len(cats), 2):
            row = cats[i:i+2]
            cols_ui = st.columns(len(row))
            for (title, cols_list), ui in zip(row, cols_ui):
                with ui:
                    mat = corr_matrix(df_r_w, cols_list, goal_cols)
                    st.markdown(f"**{title}**")
                    if mat.empty or mat.isna().all().all():
                        st.info("Not enough data to compute correlations.")
                    else:
                        st.plotly_chart(heatmap_fig_from_matrix(mat), use_container_width=True)
        st.markdown("---")

    # ---------- (B) Change in correlation — paired rows ----------
    st.markdown("### B) Change in Correlation This vs Previous Window")
    if df_prev_w.empty:
        st.info("Previous timeframe is empty — cannot compute deltas.")
    elif not goal_cols:
        st.info("No goals found in metadata.")
    else:
        goal_sel = st.selectbox("Goal for delta charts", [nice(g) for g in goal_cols], index=0)
        goal_sel_col = {nice(g): g for g in goal_cols}[goal_sel]

        def corr_with_target_safe(frame, cols, tgt, min_n=8):
            rows = []
            if frame.empty or tgt not in frame:
                return pd.DataFrame(columns=["col","corr"])
            for c in cols:
                if c not in frame: continue
                pair = (frame[[tgt, c]].replace([np.inf, -np.inf], np.nan).dropna()).copy()
                pair.columns = ["goal","drv"] if list(pair.columns)[0]==tgt else ["drv","goal"]
                if len(pair) < min_n: continue
                sx, sy = pair["goal"].std(ddof=1), pair["drv"].std(ddof=1)
                if (pd.isna(sx) or pd.isna(sy)) or (sx <= 0 or sy <= 0): continue
                r = np.corrcoef(pair["goal"].values, pair["drv"].values)[0, 1]
                if np.isfinite(r): rows.append((c, float(r)))
            return pd.DataFrame(rows, columns=["col","corr"]).set_index("col")

        cats = [(title, cols_list) for title, cols_list in buckets.items() if cols_list]
        for i in range(0, len(cats), 2):
            row = cats[i:i+2]
            cols_ui = st.columns(len(row))
            for (title, cols_list), ui in zip(row, cols_ui):
                with ui:
                    cur = corr_with_target_safe(df_r_w, cols_list, goal_sel_col)
                    prev = corr_with_target_safe(df_prev_w, cols_list, goal_sel_col)
                    joined = cur.join(prev, how="outer", lsuffix="_cur", rsuffix="_prev").fillna(np.nan)
                    joined["delta"] = joined["corr_cur"] - joined["corr_prev"]

                    st.markdown(f"**{title}**")
                    if joined.empty or joined["delta"].dropna().empty:
                        st.info("Not enough data to compute changes.")
                    else:
                        disp = joined.reset_index().rename(columns={"col":"Variable"})
                        disp["Variable_nice"] = disp["Variable"].apply(nice)
                        disp = disp.sort_values("delta", ascending=True)
                        colors = disp["delta"].apply(lambda x: "#2e7d32" if x>=0 else "#a94442")
                        figd = go.Figure(go.Bar(
                            x=disp["delta"], y=disp["Variable_nice"],
                            orientation="h",
                            marker_color=colors,
                            customdata=np.stack([disp["corr_cur"], disp["corr_prev"]], axis=1),
                            hovertemplate="Δr: %{x:.2f}<br>Current r: %{customdata[0]:.2f}<br>Prev r: %{customdata[1]:.2f}<extra></extra>"
                        ))
                        figd.update_layout(
                            xaxis=dict(title="Δr (current - previous)", range=[-1,1], zeroline=True),
                            yaxis=dict(title=""), bargap=0.2
                        )
                        st.plotly_chart(figd, use_container_width=True)
        st.markdown("---")

    # ---------- (C) Explore Driver–Goal Relationship (signal check for MMM) ----------
    st.markdown("### C) Explore Driver–Goal Relationship (signal check for MMM)")
    st.caption("Quick, non-causal curve fit to gauge whether a driver has enough signal to justify inclusion/engineering in a future MMM.")

    if not goal_cols:
        st.info("No goals found in metadata.")
    else:
        driver_all = sorted(list(dict.fromkeys([c for c in all_driver_cols if c in df_r.columns])))
        if not driver_all:
            st.info("No driver columns available.")
        else:
            y_goal_label = st.selectbox("Goal (Y)", [nice(g) for g in goal_cols], index=0, key="rel_c_goal")
            y_col = {nice(g): g for g in goal_cols}[y_goal_label]
            x_driver_label = st.selectbox("Driver (X)", [nice(c) for c in driver_all if c != y_col], index=0, key="rel_c_drv")
            x_col = {nice(c): c for c in driver_all}[x_driver_label]

            exclude_zero = st.checkbox("Exclude zero values for driver", value=(x_col != "TV_Ad_is_on"), key="rel_c_exz")
            outlier_method = st.selectbox(
                "Outlier handling",
                ["none","percentile (top only)","zscore (<3)"],
                index=0,
                help="Applies after the (optional) winsorization above. Percentile drops top ~2% of X; z-score drops |z(X)| ≥ 3.",
                key="rel_c_outlier"
            )

            x_raw = pd.to_numeric(df_r_w[x_col], errors="coerce")
            y_raw = pd.to_numeric(df_r_w[y_col], errors="coerce")
            mask = x_raw.notna() & y_raw.notna()
            if exclude_zero and x_col != "TV_Ad_is_on":
                mask &= (x_raw != 0)
            x_f = x_raw[mask].copy(); y_f = y_raw[mask].copy()

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
                # Fit quadratic
                X = np.array(x_f).reshape(-1,1)
                poly = PolynomialFeatures(degree=2)
                Xp = poly.fit_transform(X)
                mdl = LinearRegression().fit(Xp, y_f)
                y_hat = mdl.predict(Xp)

                # Metrics
                r2   = r2_score(y_f, y_hat)
                mae  = mean_absolute_error(y_f, y_hat)

                # Normalize MAE to goal scale (5th–95th pct)
                if y_f.nunique() > 1:
                    y_p5, y_p95 = np.percentile(y_f, [5, 95])
                    y_scale = max(y_p95 - y_p5, 1e-9)
                else:
                    y_scale = max(float(y_f.max() - y_f.min()), 1e-9)
                nmae = mae / y_scale

                # Spearman rank correlation (ρ)
                rho, rho_p = stats.spearmanr(x_f, y_f, nan_policy="omit")

                # ---- Scorecards (Metric → Value) ----
                SCORE_COLORS = {"green":"#2e7d32","yellow":"#f9a825","red":"#a94442","bg":"#fafafa","ink":"#222","muted":"#777"}

                def classify_r2_card(val: float):
                    if val is None or not np.isfinite(val): return "yellow","Insufficient data — treat with caution."
                    if val >= 0.35: return "green","Promising signal — relationship likely meaningful; candidate for MMM."
                    if val >= 0.15: return "yellow","Some signal — consider with transforms/lags or as part of a bundle."
                    return "red","Weak/noisy — unlikely to add value without re-engineering."

                def classify_mae_card(val: float):
                    if val is None or not np.isfinite(val): return "yellow","Insufficient data — treat with caution."
                    if val <= 0.10: return "green","Average error is small vs goal scale — usable for exploration."
                    if val <= 0.30: return "yellow","Average error is moderate — interpret cautiously."
                    return "red","Average error is large — not reliable for exploration."

                def classify_rho_card(val: float):
                    if val is None or not np.isfinite(val): return "yellow","Insufficient data — treat with caution."
                    strength = abs(val)
                    if strength >= 0.35: return "green","Clear monotonic pattern in ranks — usable signal."
                    if strength >= 0.15: return "yellow","Some monotonic pattern — consider with caution."
                    return "red","Weak/none — ranks move inconsistently."

                TITLE_R2, TITLE_MAE, TITLE_RHO = "R²", "MAE (relative)", "Spearman ρ"
                TIP_R2  = "Explained variance (fit strength) between driver and goal. Higher is better."
                TIP_MAE = "Average error vs goal’s typical scale (5th–95th pct). Smaller is better."
                TIP_RHO = "Monotonic rank correlation (strength & direction). Farther from 0 is stronger."

                def fill_from_r2(v):
                    if v is None or not np.isfinite(v): return 50
                    s = (v - (-0.2)) / (1.0 - (-0.2))
                    return int(max(0, min(1, s)) * 100)

                def fill_from_mae(v):
                    if v is None or not np.isfinite(v): return 50
                    s = 1 - min(v/0.30, 1.0)
                    return int(max(0, min(1, s)) * 100)

                def fill_from_rho(v):
                    if v is None or not np.isfinite(v): return 50
                    s = min(abs(v), 1.0)
                    return int(s * 100)

                def hex_to_rgba(hex_color: str, alpha: float) -> str:
                    h = hex_color.lstrip("#")
                    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                    return f"rgba({r},{g},{b},{alpha})"

                def colored_note(level: str, text: str):
                    base = SCORE_COLORS[level]; bg = hex_to_rgba(base, 0.10); brd = hex_to_rgba(base, 0.35)
                    html = f'<div style="margin-top:8px;border:1px solid {brd};background:{bg};color:{SCORE_COLORS["ink"]};border-radius:10px;padding:10px 12px;font-size:12px;">{text}</div>'
                    st.markdown(html, unsafe_allow_html=True)

                st.markdown("""
    <style>
    .tooltip-wrap { position: relative; display: inline-flex; align-items: center; cursor: help; }
    .tooltip-wrap .i-dot {
      font-size: 13px; border:1px solid #999; border-radius:50%;
      width:16px; height:16px; display:inline-flex;
      align-items:center; justify-content:center; color:#666; margin-left:6px;
      line-height:1;
    }
    .tooltip-wrap .tooltip-text {
      visibility: hidden; opacity: 0; transition: opacity 0.08s ease-in-out;
      position: absolute; bottom: 125%; left: 50%; transform: translateX(-50%);
      background: #111; color: #fff; padding: 8px 10px; border-radius: 6px;
      font-size: 12px; z-index: 9999; box-shadow: 0 2px 8px rgba(0,0,0,0.25);
      max-width: 520px; width: max-content; white-space: normal;
      line-height: 1.35; overflow-wrap: anywhere; text-align: center;
    }
    .tooltip-wrap:hover .tooltip-text { visibility: visible; opacity: 1; }
    .tooltip-wrap .tooltip-text::after {
      content: ""; position: absolute; top: 100%; left: 50%;
      transform: translateX(-50%); border-width: 6px; border-style: solid;
      border-color: #111 transparent transparent transparent;
    }
    </style>
                """, unsafe_allow_html=True)

                def score_bar_metric_first(metric_title: str, tooltip_text: str, value_txt: str, level: str, percent: int):
                pct_css = max(percent, 6)
                color = SCORE_COLORS[level]
                title_html = (
                    f'<span style="font-size:18px;font-weight:700;color:{SCORE_COLORS["ink"]};display:inline-flex;align-items:center;">'
                    f'{metric_title}'
                    f'<span class="tooltip-wrap"><span class="i-dot">i</span>'
                    f'<span class="tooltip-text">{tooltip_text}</span></span></span>'
                )
                html = (
                    f'<div style="border:1px solid #eee;border-radius:12px;padding:14px;background:{SCORE_COLORS["bg"]};">'
                    f'<div style="display:flex;justify-content:space-between;align-items:baseline;">'
                    f'{title_html}'
                    f'<div style="font-size:18px;font-weight:700;color:{SCORE_COLORS["ink"]};">{value_txt}</div>'
                    f'</div>'
                    f'<div style="margin-top:10px;height:16px;border-radius:999px;background:#eee;overflow:hidden;">'
                    f'<div style="width:{pct_css}%;height:100%;background:{color};"></div>'
                    f'</div></div>'
                )
                st.markdown(html, unsafe_allow_html=True)

                # Classify & format values
                lvl_r2,  msg_r2  = classify_r2_card(r2)
                lvl_mae, msg_mae = classify_mae_card(nmae)
                lvl_rho, msg_rho = classify_rho_card(rho if np.isfinite(rho) else np.nan)

                r2_txt  = f"{r2:.2f}" if np.isfinite(r2) else "—"
                mae_txt = f"{nmae*100:.1f}%" if np.isfinite(nmae) else "—"
                rho_txt = f"{rho:+.2f}" if np.isfinite(rho) else "—"  # keep sign

                p_r2   = fill_from_r2(r2)
                p_mae  = fill_from_mae(nmae)
                p_rho  = fill_from_rho(rho if np.isfinite(rho) else np.nan)

                st.markdown("#### Model Fit — Scorecards (signal for MMM)")
                c1, c2, c3 = st.columns(3)
                with c1:
                    score_bar_metric_first("R²", TIP_R2, r2_txt, lvl_r2, p_r2)
                    colored_note(lvl_r2, msg_r2)
                with c2:
                    score_bar_metric_first("MAE (relative)", TIP_MAE, mae_txt, lvl_mae, p_mae)
                    colored_note(lvl_mae, msg_mae)
                with c3:
                    score_bar_metric_first("Spearman ρ", TIP_RHO, rho_txt, lvl_rho, p_rho)
                    if np.isfinite(rho):
                        direction = "positive" if rho > 0 else ("negative" if rho < 0 else "no")
                        colored_note(lvl_rho, f"Ranks move in a {direction} monotonic pattern (ρ = {rho:+.2f}). {msg_rho}")
                    else:
                        colored_note(lvl_rho, msg_rho)

                # ---- Fit visualization + marginal returns ----
                pcts = [10,25,50,75,90]
                x_pts = np.percentile(np.array(x_f), pcts)
                dydx = mdl.coef_[1] + 2*mdl.coef_[2]*x_pts
                y_pts = mdl.predict(poly.transform(x_pts.reshape(-1,1)))

                xs = np.sort(np.array(x_f)).reshape(-1,1)
                ys = mdl.predict(poly.transform(xs))
                figfit = go.Figure()
                figfit.add_trace(go.Scatter(x=x_f.values, y=y_f.values, mode="markers", name="Actual", opacity=0.45))
                figfit.add_trace(go.Scatter(x=xs.squeeze(), y=ys, mode="lines", name="Fitted Curve", line=dict(color="red")))
                figfit.add_trace(go.Scatter(
                    x=x_pts, y=y_pts, mode="markers+text", name="Percentiles",
                    text=[f"{p}%" for p in pcts], textposition="top center", marker=dict(color="black", size=8)
                ))
                figfit.update_layout(
                    title=f"Fitted Curve for {nice(x_col)} → {nice(y_col)}",
                    xaxis_title=nice(x_col), yaxis_title=nice(y_col)
                )
                st.plotly_chart(figfit, use_container_width=True)

                mr_tbl = pd.DataFrame({
                    "Percentile": [f"{p}%" for p in pcts],
                    "Driver value": [f"{float(v):.2f}" for v in x_pts],
                    "Marginal return (dy/dx)": [f"{float(v):.4f}" for v in dydx],
                })
                st.dataframe(mr_tbl, hide_index=True, use_container_width=True)

# =============================
# TAB 5 — COLLINEARITY & PCA (by Country) · MMM readiness (v9 — overall→adjust→mirrored details; leaner UI)
# =============================
with tab_diag:
    # Local imports
    try:
        from sklearn.decomposition import PCA
    except Exception:
        st.error("Missing scikit-learn component: `sklearn.decomposition.PCA`.")

    st.subheader("Collinearity & PCA — Overall → Adjust → Details")

    # ---------- UI CSS: tooltips; scope wide dropdown ONLY to adjust section ----------
    st.markdown("""
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
    """, unsafe_allow_html=True)

    def htip(label, text):
        st.markdown(f"""<span class="hbadge" data-tip="{text}">{label}<span class="idot">i</span></span>""",
                    unsafe_allow_html=True)

    # ---------- Exclude MAIN goals via metadata (no other functional changes) ----------
    try:
        goals_to_exclude = set(
            m.loc[m[CAT].str.lower().eq("goal"), COL].dropna().astype(str).tolist()
        ) if (CAT in m.columns and COL in m.columns) else set()
    except Exception:
        goals_to_exclude = set(meta_goals_main) if "meta_goals_main" in locals() else set()

    # ---------- Helpers ----------
    def _prepare_X(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
        if not cols: return pd.DataFrame()
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
        if X.shape[1] < 2: return np.nan
        Xs = (X - X.mean(0)) / X.std(0).replace(0, 1)
        Xs = Xs.replace([np.inf, -np.inf], 0).values
        try:
            s = np.linalg.svd(Xs, compute_uv=False)
            s = s[s > 1e-12]
            if len(s) < 2: return np.nan
            return float(s.max() / s.min())
        except Exception:
            return np.nan

    def _vif_table(X: pd.DataFrame):
        vars_ = X.columns.tolist()
        if len(vars_) < 2:
            return pd.DataFrame({"variable": vars_, "VIF": [np.nan]*len(vars_)})
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
        return pd.DataFrame(out, columns=["variable","VIF"]).sort_values("VIF", ascending=False)

    def _pca_summary(X: pd.DataFrame, var_target: float = 0.80):
        if X.shape[1] < 2:
            return dict(n_components=0, var_ratio=[], cum_ratio=[], loadings=pd.DataFrame())
        Xs = (X - X.mean(0)) / X.std(0).replace(0, 1)
        Xs = Xs.replace([np.inf, -np.inf], 0.0).values
        try:
            pca = PCA().fit(Xs)
            vr = pca.explained_variance_ratio_.tolist()
            cum = np.cumsum(vr).tolist()
            k = next((i + 1 for i, c in enumerate(cum) if c >= var_target), len(cum))
            load = pd.DataFrame(pca.components_, columns=list(X.columns))
            load.index = [f"PC{i+1}" for i in range(load.shape[0])]
            return dict(n_components=k, var_ratio=vr, cum_ratio=cum, loadings=load)
        except Exception:
            return dict(n_components=np.nan, var_ratio=[], cum_ratio=[], loadings=pd.DataFrame())

    def _bucket_map():
        return {
            "Paid Spend":       [c for c in (paid_spend_cols or []) if c in df.columns and c not in goals_to_exclude],
            "Paid Media Vars":  [c for c in (paid_var_cols   or []) if c in df.columns and c not in goals_to_exclude],
            "Organic Vars":     [c for c in (organic_cols    or []) if c in df.columns and c not in goals_to_exclude],
            "Context Vars":     [c for c in (context_cols    or []) if c in df.columns and c not in goals_to_exclude],
        }

    def _all_drivers():
        b = _bucket_map()
        return [c for c in dict.fromkeys(sum(b.values(), []))]

    def _vif_band(v: float):
        if not np.isfinite(v): return "–"
        return "🟢" if v < 5 else ("🟡" if v < 7.5 else "🔴")

    def _pca_band_wholeset(pcs_needed: int, n_vars: int):
        """
        PCA band for the country-level whole set: PCs@80% relative to #Vars.
        ≤30% → 🟢 ; 30–60% → 🟡 ; >60% → 🔴
        """
        if n_vars <= 0 or not np.isfinite(pcs_needed): return "–"
        r = pcs_needed / max(1, n_vars)
        return "🟢" if r <= 0.30 else ("🟡" if r <= 0.60 else "🔴")

    # ------------------- CONFIGURATIONS (collapsible) -------------------
    with st.expander("Configurations", expanded=False):
        countries_all = sorted(df["COUNTRY"].dropna().astype(str).unique()) if "COUNTRY" in df else []
        c1, c2, c3 = st.columns([1.6, 1.0, 1.0])
        with c1:
            sel_ctry = st.multiselect("Countries", options=countries_all, default=countries_all, help="Per-country analysis.")
        with c2:
            vif_label_to_val = {
                "5.0 — strict / low tolerance": 5.0,
                "7.5 — balanced / medium": 7.5,
                "10.0 — lenient / high tolerance": 10.0
            }
            sel_vif_label = st.selectbox("VIF flag threshold", list(vif_label_to_val.keys()), index=1)
            vif_thr = float(vif_label_to_val[sel_vif_label])
        with c3:
            st.caption("PCA coverage fixed at **80%** for comparability.")

    if not sel_ctry:
        st.info("Select at least one country to run diagnostics.")
        st.stop()

    # ---- Inline help (horizontal) ----
    st.markdown('<div class="hintrow">', unsafe_allow_html=True)
    htip("Cond#", "Computed on standardized X as σ_max/σ_min (SVD). Not a count; lower is better. Rough bands: <15 low, 15–30 medium, >30 high.")
    htip(f"VIF>{vif_thr}", "How many variables in that country are highly redundant with the rest.")
    htip("PCs @ 80%", "How many latent patterns are needed to capture ~80% of movement across drivers.")
    htip("#Vars", "Columns that survived basic cleaning (constant/empty dropped).")
    st.markdown('</div>', unsafe_allow_html=True)

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
        dct = df[df["COUNTRY"].astype(str).eq(ctry)] if "COUNTRY" in df else df.copy()
        X_all = _prepare_X(dct, all_drivers)
        cn = _condition_number(X_all)
        vif_df = _vif_table(X_all)
        overall_vif_by_country[ctry] = vif_df.copy() if not vif_df.empty else pd.DataFrame(columns=["variable","VIF"])
        high_vif = int((vif_df["VIF"] > float(vif_thr)).sum()) if not vif_df.empty else 0
        pca_s_all = _pca_summary(X_all, var_target=var_target_num)
        bench_rows.append(dict(
            Country=ctry,
            Vars=X_all.shape[1],
            ConditionNo=(round(cn, 1) if np.isfinite(cn) else np.nan),
            VIF_Flags=high_vif,
            PCA_k=pca_s_all["n_components"],
            PCA_Band=_pca_band_wholeset(pca_s_all["n_components"], X_all.shape[1]),
            CondBand=("🟢 Low" if (np.isfinite(cn) and cn < 15) else ("🟡 Medium" if (np.isfinite(cn) and cn < 30) else ("🔴 High" if np.isfinite(cn) else "–")))
        ))

    bench_all = pd.DataFrame(bench_rows).sort_values(["VIF_Flags","ConditionNo"], ascending=[False, False])
    disp_all = bench_all.rename(columns={"Vars":"#Vars","ConditionNo":"Cond#","VIF_Flags":f"VIF>{vif_thr}","PCA_k":"PCs @ 80%","PCA_Band":"PCA Band"})
    st.dataframe(disp_all[["Country","#Vars","Cond#","CondBand",f"VIF>{vif_thr}","PCs @ 80%","PCA Band"]], use_container_width=True)

    # Bucket variable tables (overall run) — COLLAPSIBLE
    with st.expander("Bucket view — variables & VIF (overall run)", expanded=False):
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
                    subset = vdf[vdf["variable"].isin(buckets[bucket_name])].copy()
                    if subset.empty:
                        continue
                    subset["Country"] = ctry
                    subset["Variable"] = subset["variable"].map(nice)
                    subset["VIF Band"] = subset["VIF"].apply(_vif_band)
                    subset["VIF"] = subset["VIF"].map(lambda v: f"{v:.2f}" if np.isfinite(v) else "–")
                    rows.append(subset[["Country","Variable","VIF","VIF Band"]])
                if rows:
                    out = pd.concat(rows, axis=0).sort_values(["Country","Variable"]).copy()
                    st.dataframe(out, hide_index=True, use_container_width=True)
                else:
                    st.info("No variables available in this bucket for the selected countries.")
        st.caption("Legend: VIF bands — 🟢 <5 (OK), 🟡 5–7.5 (Watch), 🔴 ≥7.5 (Flag). Spend variables are **not** auto-suggested for removal.")

    # Suggestions: group by severity (spend excluded) — COLLAPSIBLE
    with st.expander("Suggested removals (grouped by severity; spend excluded)", expanded=False):
        for ctry in sel_ctry:
            st.markdown(f"**{ctry}**")
            vdf = overall_vif_by_country.get(ctry)
            if vdf is None or vdf.empty:
                st.info("No suggestion (not enough variables).")
                continue
            spend_set = set(paid_spend_cols or [])
            pruned = vdf[~vdf["variable"].isin(spend_set)].copy()
            if pruned.empty:
                st.info("All top offenders are spend variables. Keep them, or consider bundling correlated spends.")
                continue
            pruned["Variable"] = pruned["variable"].map(nice)
            pruned["Band"] = pruned["VIF"].apply(lambda v: "Strong (🔴 ≥10)" if v >= 10 else ("Moderate (🟡 7.5–10)" if v >= 7.5 else ("Mild (🟢 5–7.5)" if v >= 5 else "OK (<5)")))
            pruned["VIF"] = pruned["VIF"].map(lambda v: f"{v:.2f}" if np.isfinite(v) else "–")
            for band in ["Strong (🔴 ≥10)", "Moderate (🟡 7.5–10)", "Mild (🟢 5–7.5)"]:
                seg = pruned[pruned["Band"] == band][["Variable","VIF","Band"]]
                if seg.empty:
                    continue
                st.markdown(f"*{band}*")
                st.dataframe(seg.sort_values("VIF", ascending=False), hide_index=True, use_container_width=True)

    st.markdown("---")

    # =========================================================
    # 2) ADJUST VARIABLE SELECTION → RE-SCORE (sorted picker + drop suggested + download)
    # =========================================================
    st.markdown("### 2) Adjust drivers & re-score (what-if)")

    # Persist manual selection; default to all drivers
    if "diag_selected_drivers_v9" not in st.session_state:
        st.session_state["diag_selected_drivers_v9"] = _all_drivers().copy()

    # Build (nice, raw) map sorted by nice
    nice_raw_pairs = sorted([(nice(c), c) for c in _all_drivers()], key=lambda t: t[0].lower())
    pick_options = [nr[0] for nr in nice_raw_pairs]
    nice_to_raw = {nr[0]: nr[1] for nr in nice_raw_pairs}

    default_nice = [nice(c) for c in st.session_state["diag_selected_drivers_v9"] if c in nice_to_raw.values()]
    default_nice = sorted(default_nice, key=lambda s: s.lower())

    st.markdown('<div id="adjust-picker">', unsafe_allow_html=True)
    sel = st.multiselect(
        "Include these variables",
        options=pick_options,
        default=default_nice,
        help="Refine columns for a MMM-ready set. Alphabetically sorted; long names fully visible."
    )
    st.markdown('</div>', unsafe_allow_html=True)

    st.session_state["diag_selected_drivers_v9"] = [nice_to_raw[n] for n in sel if n in nice_to_raw]
    drivers_sel = st.session_state["diag_selected_drivers_v9"]

    # Compute combined VIF (across selected countries) for action buttons
    strong_to_drop, mod_to_drop, mild_to_drop = [], [], []
    if drivers_sel:
        d_comb = df[df["COUNTRY"].astype(str).isin(sel_ctry)] if "COUNTRY" in df else df.copy()
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
            if st.button(f"Drop suggested: Strong (≥10) — {len(set(strong_to_drop))} vars"):
                st.session_state["diag_selected_drivers_v9"] = [c for c in st.session_state["diag_selected_drivers_v9"] if c not in set(strong_to_drop)]
                st.rerun()
        if strong_to_drop or mod_to_drop:
            total_sm = len(set(strong_to_drop) | set(mod_to_drop))
            if st.button(f"Drop suggested: Strong + Moderate (≥7.5) — {total_sm} vars"):
                to_drop = set(strong_to_drop) | set(mod_to_drop)
                st.session_state["diag_selected_drivers_v9"] = [c for c in st.session_state["diag_selected_drivers_v9"] if c not in to_drop]
                st.rerun()
        # NEW: Strict option (drops ≥5, i.e., strong + moderate + mild)
        if strong_to_drop or mod_to_drop or mild_to_drop:
            total_strict = len(set(strong_to_drop) | set(mod_to_drop) | set(mild_to_drop))
            if st.button(f"Drop suggested: Strict (≥5) — {total_strict} vars"):
                to_drop = set(strong_to_drop) | set(mod_to_drop) | set(mild_to_drop)
                st.session_state["diag_selected_drivers_v9"] = [c for c in st.session_state["diag_selected_drivers_v9"] if c not in to_drop]
                st.rerun()
    else:
        st.info("No variables selected. Pick at least one to compute what-if scores.")

    # What-if re-score per country (auto refresh on change)
    bench2 = None
    if drivers_sel:
        bench_rows2 = []
        for ctry in sel_ctry:
            dct = df[df["COUNTRY"].astype(str).eq(ctry)] if "COUNTRY" in df else df.copy()
            Xs = _prepare_X(dct, drivers_sel)
            cn2 = _condition_number(Xs)
            vif2 = _vif_table(Xs)
            flags2 = int((vif2["VIF"] > float(vif_thr)).sum()) if not vif2.empty else 0
            pca2  = _pca_summary(Xs, var_target=var_target_num)
            bench_rows2.append(dict(
                Country=ctry,
                Vars=Xs.shape[1],
                ConditionNo=(round(cn2,1) if np.isfinite(cn2) else np.nan),
                VIF_Flags=flags2,
                PCA_k=pca2["n_components"],
                PCA_Band=_pca_band_wholeset(pca2["n_components"], Xs.shape[1]),
                CondBand=("🟢 Low" if (np.isfinite(cn2) and cn2 < 15) else ("🟡 Medium" if (np.isfinite(cn2) and cn2 < 30) else ("🔴 High" if np.isfinite(cn2) else "–")))
            ))
        bench2 = pd.DataFrame(bench_rows2).sort_values(["VIF_Flags","ConditionNo"], ascending=[False, False])
        disp2 = bench2.rename(columns={"Vars":"#Vars","ConditionNo":"Cond#","VIF_Flags":f"VIF>{vif_thr}","PCA_k":"PCs @ 80%","PCA_Band":"PCA Band"})
        st.dataframe(disp2[["Country","#Vars","Cond#","CondBand",f"VIF>{vif_thr}","PCs @ 80%","PCA Band"]], use_container_width=True)

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
            help="Exports the current feature set you'll take into Robyn / experiments."
        )

    st.markdown("---")

    # =========================================================
    # 3) COUNTRY DETAILS — MIRRORS SECTION 1, uses CURRENT SELECTION
    # =========================================================
    mode_note = "Adjusted selection from Section 2" if drivers_sel else "All drivers (no adjusted selection)"
    st.markdown(f"### 3) Country details — mirrored tables (using: **{mode_note}**)")

    active_cols = (drivers_sel if drivers_sel else all_drivers)

    # Build mirrored summary first (country-level)
    detail_rows = []
    detail_vif_by_country = {}

    for ctry in sel_ctry:
        dct = df[df["COUNTRY"].astype(str).eq(ctry)] if "COUNTRY" in df else df.copy()
        Xd = _prepare_X(dct, active_cols)
        cn = _condition_number(Xd)
        vif_df = _vif_table(Xd)
        detail_vif_by_country[ctry] = vif_df.copy() if not vif_df.empty else pd.DataFrame(columns=["variable","VIF"])
        high_vif = int((vif_df["VIF"] > float(vif_thr)).sum()) if not vif_df.empty else 0
        pca_s = _pca_summary(Xd, var_target=var_target_num)
        detail_rows.append(dict(
            Country=ctry,
            Vars=Xd.shape[1],
            ConditionNo=(round(cn, 1) if np.isfinite(cn) else np.nan),
            VIF_Flags=high_vif,
            PCA_k=pca_s["n_components"],
            PCA_Band=_pca_band_wholeset(pca_s["n_components"], Xd.shape[1]),
            CondBand=("🟢 Low" if (np.isfinite(cn) and cn < 15) else ("🟡 Medium" if (np.isfinite(cn) and cn < 30) else ("🔴 High" if np.isfinite(cn) else "–")))
        ))

    detail_sum = pd.DataFrame(detail_rows).sort_values(["VIF_Flags","ConditionNo"], ascending=[False, False])
    detail_disp = detail_sum.rename(columns={"Vars":"#Vars","ConditionNo":"Cond#","VIF_Flags":f"VIF>{vif_thr}","PCA_k":"PCs @ 80%","PCA_Band":"PCA Band"})
    st.dataframe(detail_disp[["Country","#Vars","Cond#","CondBand",f"VIF>{vif_thr}","PCs @ 80%","PCA Band"]], use_container_width=True)

    # Bucket variable tables (current selection) — COLLAPSIBLE
    with st.expander("Bucket view — variables & VIF (current selection)", expanded=False):
        cols_pair2 = st.columns(2)
        for i, bucket_name in enumerate(buckets.keys()):
            with cols_pair2[i % 2]:
                st.markdown(f"**{bucket_name}**")
                rows = []
                for ctry in sel_ctry:
                    vdf = detail_vif_by_country.get(ctry)
                    if vdf is None or vdf.empty:
                        continue
                    subset = vdf[vdf["variable"].isin(buckets[bucket_name])].copy()
                    if subset.empty:
                        continue
                    subset["Country"] = ctry
                    subset["Variable"] = subset["variable"].map(nice)
                    subset["VIF Band"] = subset["VIF"].apply(_vif_band)
                    subset["VIF"] = subset["VIF"].map(lambda v: f"{v:.2f}" if np.isfinite(v) else "–")
                    rows.append(subset[["Country","Variable","VIF","VIF Band"]])
                if rows:
                    out = pd.concat(rows, axis=0).sort_values(["Country","Variable"]).copy()
                    st.dataframe(out, hide_index=True, use_container_width=True)
                else:
                    st.info("No variables available in this bucket for the current selection.")
            
# -----------------------------
# Metadata QA
# -----------------------------
with st.expander("Metadata QA — columns present in data but missing in metadata"):
    if isinstance(meta, dict):
        mapping = meta.get("mapping") or {}
        meta_known = set()
        for vals in mapping.values():
            meta_known |= set(map(str, (vals or [])))
        goals_list = meta.get("goals") or []
        meta_known |= set(g.get("var") for g in goals_list if isinstance(g, dict) and g.get("var"))
    else:
        meta_known = set(meta["column_name"].astype(str).str.strip().unique()) if "column_name" in meta else set()

    data_cols  = set(df.columns.astype(str))
    missing = sorted(list(data_cols - meta_known))
    st.write("Add these to metadata (at minimum `column_name`, `display_name`, `main_category`, and optional `platform`):")
    st.code(", ".join(missing) or "None — looks good!")