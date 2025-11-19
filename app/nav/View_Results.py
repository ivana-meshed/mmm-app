# app/pages/1_Results.py

import base64
import datetime as dt
import hashlib
import io
import os
import re
from urllib.parse import quote

import pandas as pd
import streamlit as st
from google.auth import default as google_auth_default
from google.auth.iam import Signer as IAMSigner
from google.auth.transport.requests import Request
from google.cloud import storage

try:
    from app_shared import (
        _sf_params_from_env,
        ensure_sf_conn,
        keepalive_ping,
        require_login_and_domain,
    )
except Exception:
    ensure_sf_conn = None
    keepalive_ping = None
    _sf_params_from_env = None

require_login_and_domain()

# Show loading spinner while page initializes
with st.spinner("Loading page..."):
    # Initialize session state defaults
    try:
        from app_split_helpers import ensure_session_defaults

        ensure_session_defaults()
    except ImportError:
        # Fallback if app_split_helpers is not available
        st.session_state.setdefault(
            "gcs_bucket", os.getenv("GCS_BUCKET", "mmm-app-output")
        )

    # ---------- Page ----------
    st.title("Results browser (GCS)")

# ---------- Settings ----------
DEFAULT_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
DEFAULT_PREFIX = "robyn/"
DATA_URI_MAX_BYTES = int(os.getenv("DATA_URI_MAX_BYTES", str(8 * 1024 * 1024)))
IS_CLOUDRUN = bool(os.getenv("K_SERVICE"))


def _try_keep_sf_alive():
    if ensure_sf_conn is None or keepalive_ping is None:
        return
    try:
        # Reuse an existing session if present; if not configured, this may raise — we swallow it.
        conn = ensure_sf_conn()
        if conn:
            keepalive_ping(
                conn
            )  # cheap ping so switching pages doesn't let SF idle out
            # Optional: reflect status for a tiny sidebar badge if you want
            st.session_state.setdefault("sf_connected", True)
    except Exception:
        # Do not surface errors here; Results should work fine without Snowflake.
        pass


_try_keep_sf_alive()


# ---------- Clients / cached ----------
@st.cache_resource
def gcs_client():
    return storage.Client()


client = gcs_client()


@st.cache_resource
def _iam_signer_cached():
    """Build an IAM Signer once (works on Cloud Run)."""
    try:
        creds, _ = google_auth_default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        sa_email = os.getenv("RUN_SERVICE_ACCOUNT_EMAIL") or getattr(
            creds, "service_account_email", None
        )
        if not sa_email:
            return None, None
        signer = IAMSigner(Request(), creds, sa_email)
        return signer, sa_email
    except Exception:
        return None, None


# ---------- Helpers ----------
def gcs_console_url(bucket: str, prefix: str) -> str:
    return (
        "https://console.cloud.google.com/storage/browser/"
        f"{bucket}/{quote(prefix, safe='/')}"
    )


def run_has_allocator_plot(blobs) -> bool:
    return len(find_allocator_plots(blobs)) > 0


def gcs_object_details_url(blob) -> str:
    return (
        "https://console.cloud.google.com/storage/browser/_details/"
        f"{blob.bucket.name}/{quote(blob.name, safe='/')}"
    )


def blob_key(prefix: str, name: str, suffix: str = "") -> str:
    h = hashlib.sha1((name + suffix).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{h}"


def list_blobs(bucket_name: str, prefix: str):
    try:
        bucket = client.bucket(bucket_name)
        return list(client.list_blobs(bucket_or_name=bucket, prefix=prefix))
    except Exception as e:
        st.error(f"❌ Failed to list gs://{bucket_name}/{prefix} — {e}")
        return []


def parse_path(name: str):
    # Expected new format: robyn/<TAG_NUMBER>/<country>/<stamp>/file...
    # Also support old format: robyn/<rev>/<country>/<stamp>/file... for backward compatibility
    parts = name.split("/")
    if len(parts) >= 5 and parts[0] == "robyn":
        # Parse revision (could be old format like "r100" or new format like "myname_1")
        rev_part = parts[1]
        if "_" in rev_part:
            # New format with TAG_NUMBER
            tag_num_parts = rev_part.rsplit("_", 1)
            rev = rev_part  # Keep full TAG_NUMBER as rev
            tag = tag_num_parts[0]
            try:
                number = int(tag_num_parts[1])
            except (ValueError, IndexError):
                number = None
        else:
            # Old format (e.g., "r100")
            rev = rev_part
            tag = rev_part
            number = None

        return {
            "rev": rev,
            "tag": tag,
            "number": number,
            "country": parts[2],
            "stamp": parts[3],
            "file": "/".join(parts[4:]),
        }
    return None


def group_runs(blobs):
    runs = {}
    for b in blobs:
        info = parse_path(b.name)
        if not info or not info["file"]:
            continue
        # Group by (revision, country, stamp) as before
        key = (info["rev"], info["country"], info["stamp"])
        runs.setdefault(key, []).append(b)
    return runs


def parse_stamp(stamp: str):
    try:
        return dt.datetime.strptime(stamp, "%m%d_%H%M%S")
    except Exception:
        return stamp


def parse_rev_key(rev: str):
    m = re.search(r"(\d+)$", (rev or "").strip())
    if m:
        return (0, int(m.group(1)))
    return (1, (rev or "").lower())


def read_text_blob(blob) -> str:
    try:
        return blob.download_as_bytes().decode("utf-8", errors="replace")
    except Exception as e:
        st.error(f"Failed to read text from {blob.name}: {e}")
        return ""


def find_blob(blobs, endswith: str):
    basename = os.path.basename(endswith).lower()
    for b in blobs:
        if b.name.endswith(endswith):
            return b
    for b in blobs:
        if os.path.basename(b.name).lower() == basename:
            return b
    return None


def find_all(blobs, pattern):
    r = re.compile(pattern, re.IGNORECASE)
    return [b for b in blobs if r.search(b.name)]


def parse_best_meta(blobs):
    """Return (best_id, iterations, trials) from best_model_id.txt if present."""
    b = find_blob(blobs, "/best_model_id.txt") or find_blob(
        blobs, "best_model_id.txt"
    )
    best_id, iters, trials = None, None, None
    if not b:
        return best_id, iters, trials
    lines = [ln.strip() for ln in read_text_blob(b).splitlines() if ln.strip()]
    if lines:
        best_id = lines[0].split()[0]
    for ln in lines[1:]:
        m = re.search(r"Iterations:\s*(\d+)", ln, re.I)
        if m:
            iters = int(m.group(1))
        m = re.search(r"Trials:\s*(\d+)", ln, re.I)
        if m:
            trials = int(m.group(1))
    return best_id, iters, trials


def latest_run_key(runs, rev_filter=None, country_filter=None):
    keys = list(runs.keys())
    if rev_filter:
        keys = [k for k in keys if k[0] == rev_filter]
    if country_filter:
        keys = [k for k in keys if k[1] == country_filter]
    if not keys:
        return None
    keys.sort(key=lambda k: parse_stamp(k[2]), reverse=True)
    return keys[0]


def download_bytes_safe(blob):
    try:
        data = blob.download_as_bytes()
        if not data:
            st.warning(f"Downloaded file is empty: {blob.name}")
            return None
        return data
    except Exception as e:
        st.error(f"Download failed for {blob.name}: {e}")
        return None


def signed_url_or_none(blob, minutes: int = 60):
    try:
        signer, sa_email = _iam_signer_cached()
        if not signer or not sa_email:
            return None
        return blob.generate_signed_url(
            version="v4",
            expiration=dt.timedelta(minutes=minutes),
            method="GET",
            signer=signer,
            service_account_email=sa_email,
        )
    except Exception:
        return None


def download_link_for_blob(
    blob, label=None, mime_hint=None, minutes: int = 60, key_suffix=None
):
    """Prefer signed URL; fall back to data URI; else link to GCS Console."""
    name = os.path.basename(blob.name)
    label = label or f"⬇️ Download {name}"
    mime = mime_hint or "application/octet-stream"

    # 1) Signed URL
    url = signed_url_or_none(blob, minutes)
    if url:
        st.markdown(
            f'<a href="{url}" download="{name}">{label}</a>',
            unsafe_allow_html=True,
        )
        return

    # 2) Data URI (small files)
    data = download_bytes_safe(blob)
    if data is None:
        st.warning(f"Couldn't fetch {name} for download.")
        return
    if len(data) <= DATA_URI_MAX_BYTES:
        b64 = base64.b64encode(data).decode()
        href = f"data:{mime};base64,{b64}"
        st.markdown(
            f'<a href="{href}" download="{name}">{label}</a>',
            unsafe_allow_html=True,
        )
        return

    # 3) GCS Console fallback
    st.info(
        "File is large and no signed URL is available. Open in the GCS Console."
    )
    st.markdown(
        f"[Open **{name}** in GCS Console]({gcs_object_details_url(blob)})"
    )


# ---------- Discovery helpers ----------
def find_onepager_blob(blobs, best_id: str):
    """Try canonical <best_id>.png/.pdf; else largest non-allocator PNG/PDF."""
    if not best_id:
        return None

    for ext in (".png", ".pdf"):
        target = f"{best_id}{ext}".lower()
        for b in blobs:
            if os.path.basename(b.name).lower() == target:
                return b

    candidates = []
    for b in blobs:
        fn = os.path.basename(b.name).lower()
        if not (fn.endswith(".png") or fn.endswith(".pdf")):
            continue
        if fn.startswith(("allocator", "response", "saturation")):
            continue
        if getattr(b, "size", 0) > 50_000:
            candidates.append(b)
    if candidates:
        candidates.sort(key=lambda x: x.size, reverse=True)
        return candidates[0]
    return None


def read_csv_blob_to_df(blob) -> pd.DataFrame | None:  # type: ignore
    try:
        data = download_bytes_safe(blob)
        if data is None:
            return None
        return pd.read_csv(io.BytesIO(data))
    except Exception as e:
        st.warning(f"Couldn't parse CSV from {blob.name}: {e}")
        return None


def find_pred_allocator_plots(blobs):
    """
    Collect prediction allocator plots (PNG) generated by run_all.R in
    allocator_pred_plots_<timestamp>/allocator_pred_YYYY-MM.png
    """
    plots, seen = [], set()
    for b in blobs:
        name_l = b.name.lower()
        base = os.path.basename(name_l)
        if not name_l.endswith(".png"):
            continue
        # Only prediction plots
        is_pred_dir = "allocator_pred_plots_" in name_l
        is_pred_name = base.startswith("allocator_pred_") and base.endswith(
            ".png"
        )
        if (is_pred_dir or is_pred_name) and getattr(b, "size", 0) > 1000:
            if b.name not in seen:
                plots.append(b)
                seen.add(b.name)

    # Sort by month if possible (allocator_pred_YYYY-MM.png)
    def month_key(blob):
        m = re.search(r"allocator_pred_(\d{4}-\d{2})\.png$", blob.name, re.I)
        return m.group(1) if m else blob.name.lower()

    plots.sort(key=month_key)
    return plots


def find_allocator_plots(blobs):
    """Collect *historical* allocator plots (PNG) without duplicates."""
    plots, seen = [], set()
    for b in blobs:
        name_l = b.name.lower()
        base = os.path.basename(name_l)
        if not name_l.endswith(".png"):
            continue
        # historical = in allocator_plots_<timestamp>/..., often "..._365d.png"
        is_hist_dir = "allocator_plots_" in name_l
        is_hist_name = base.startswith("allocator_") and "365d" in base
        # exclude prediction folder/files
        is_predicted = "allocator_pred_plots_" in name_l or base.startswith(
            "allocator_pred_"
        )
        if (
            (is_hist_dir or is_hist_name)
            and not is_predicted
            and getattr(b, "size", 0) > 1000
        ):
            if b.name not in seen:
                plots.append(b)
                seen.add(b.name)
    return plots


# --- METRICS EXTRACTION HELPERS ---------------------------------------------
_METRIC_ALIASES = {
    "r.squared": "r2",
    "rsq": "r2",
    "r2": "r2",
    "nrmse": "nrmse",
    "nrmsd": "nrmse",
    "decomp_rssd": "decomp_rssd",
    "decomprssd": "decomp_rssd",
    "decomp.rssd": "decomp_rssd",
    "rssd": "decomp_rssd",
}
_SPLIT_ALIASES = {
    "train": "train",
    "training": "train",
    "val": "val",
    "valid": "val",
    "validation": "val",
    "test": "test",
    "holdout": "test",
}


def _lower_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def _extract_from_long(df: pd.DataFrame) -> dict:
    """
    Long format example:
    split | r2 | nrmse | decomp_rssd
    train | .. |  ...  | ...
    val   | .. |  ...  | ...
    test  | .. |  ...  | ...
    """
    df = _lower_cols(df)
    split_col = next(
        (c for c in df.columns if c in ("split", "set", "phase")), None
    )
    if not split_col:
        return {}
    df["_split"] = df[split_col].map(
        lambda s: _SPLIT_ALIASES.get(str(s).strip().lower(), None)
    )
    df = df[df["_split"].notna()].copy()
    out = {}
    for metric_col in df.columns:
        if metric_col in (split_col, "_split"):
            continue
        # normalize punctuation to underscores before alias lookup
        norm = re.sub(r"[()\[\]{}:.\s\-]+", "_", str(metric_col).lower()).strip(
            "_"
        )
        m_std = _METRIC_ALIASES.get(norm, None)
        if not m_std:
            continue
        for sp, val in df.groupby("_split")[metric_col].first().items():
            out[f"{m_std}_{sp}"] = pd.to_numeric(val, errors="coerce")
    return out


def _extract_from_wide(df: pd.DataFrame) -> dict:
    """
    Wide format examples:
      r2_train, r2_val, r2_test, nrmse_train, ...
    or train_r2, validation_nrmse, etc.
    """
    df = _lower_cols(df)
    if len(df) == 0:
        return {}
    row = df.iloc[0]
    out = {}
    for col, val in row.items():
        col_l = str(col).lower()
        # normalize parens/colons into underscores first
        clean = re.sub(r"[()\[\]{}:]+", "_", col_l)
        # split on underscore, dot, hyphen or whitespace
        parts = re.split(r"[ _.\-]+", clean)
        parts = [p for p in parts if p]
        metric = None
        split = None
        for p in parts:
            if p in _METRIC_ALIASES:
                metric = _METRIC_ALIASES[p]
            if p in _SPLIT_ALIASES:
                split = _SPLIT_ALIASES[p]
        if metric and split:
            out[f"{metric}_{split}"] = pd.to_numeric(val, errors="coerce")
        # Optional fallback: if a "metric with no split" column exists, copy to all splits
        if metric and not split and metric not in ("r2",):  # keep r2 strict
            v = pd.to_numeric(val, errors="coerce")
            for sp in ("train", "val", "test"):
                out.setdefault(f"{metric}_{sp}", v)
    return out


def _try_read_csv(blob) -> pd.DataFrame | None:  # type: ignore
    try:
        data = download_bytes_safe(blob)
        if data is None:
            return None
        return pd.read_csv(io.BytesIO(data))
    except Exception:
        return None


def extract_core_metrics_from_blobs(blobs: list) -> dict:
    """
    Try to find a CSV that contains r2 / nrmse / decomp_rssd across train/val/test.
    We scan likely metric/summary CSVs and fall back to anything that looks right.
    Returns dict like:
      {'r2_train':..., 'r2_val':..., 'r2_test':..., 'nrmse_train':..., ..., 'decomp_rssd_test':...}
    Missing keys are OK.
    """
    csvs = [b for b in blobs if b.name.lower().endswith(".csv")]
    preferred = [
        b
        for b in csvs
        if re.search(r"(metrics|summary|performance)", b.name.lower())
    ]
    candidates = preferred + [b for b in csvs if b not in preferred]

    for b in candidates:
        df = _try_read_csv(b)
        if df is None:
            continue
        extracted = {}
        cols_l = [c.lower() for c in df.columns]
        if any(c in cols_l for c in ("split", "set", "phase")):
            extracted = _extract_from_long(df)
        else:
            extracted = _extract_from_wide(df)
        if any(k.startswith("r2_") for k in extracted.keys()) or any(
            k.startswith("nrmse_") for k in extracted.keys()
        ):
            return extracted

    # Fallback: allocator_metrics.csv
    alloc = find_blob(blobs, "/allocator_metrics.csv")
    if alloc:
        df = _try_read_csv(alloc)
        if df is not None:
            e = _extract_from_wide(df)
            if e:
                return e
    return {}


# ---------- Renderers ----------
@st.cache_data(ttl=3600, show_spinner="Loading model configuration...")
def _fetch_model_config(bucket_name: str, stamp: str):
    """Fetch and parse model configuration. Cached for performance."""
    try:
        config_path = f"training-configs/{stamp}/job_config.json"
        bucket = client.bucket(bucket_name)
        config_blob = bucket.blob(config_path)

        if config_blob.exists():
            config_data = config_blob.download_as_bytes()
            if config_data:
                import json

                return json.loads(config_data.decode("utf-8"))
    except Exception:
        pass
    return None


def render_model_config_section(blobs, country, stamp, bucket_name):
    """Render model configuration from training-configs/{stamp}/job_config.json"""
    # Model Configuration now has its own expander (no subheader needed here)

    # Try to fetch config from cache first
    config = _fetch_model_config(bucket_name, stamp)

    # Fallback to debug path if not found in training-configs
    if not config:
        try:
            config_blob = find_blob(
                blobs, "/debug/job_config.copy.json"
            ) or find_blob(blobs, "job_config.copy.json")

            if not config_blob:
                st.info(
                    f"No model configuration found (tried training-configs/{stamp}/job_config.json)."
                )
                return

            config_data = download_bytes_safe(config_blob)
            if not config_data:
                st.warning("Could not read model configuration.")
                return

            import json

            config = json.loads(config_data.decode("utf-8"))
        except Exception as e:
            st.warning(f"Couldn't parse model configuration: {e}")
            return

    # Display configuration parameters in a single column
    if config:
        with st.container(border=True):
            # Helper function to format list values
            def format_list(val):
                if isinstance(val, list):
                    return ", ".join(str(v) for v in val)
                return str(val)

            # 1. Iterations
            if "iterations" in config:
                st.markdown(f"**Iterations:** {config['iterations']}")

            # 2. Trials
            if "trials" in config:
                st.markdown(f"**Trials:** {config['trials']}")

            # 3. Train Size
            if "train_size" in config:
                train_size = format_list(config["train_size"])
                st.markdown(f"**Train Size:** [{train_size}]")

            # 4. Adstock
            if "adstock" in config:
                st.markdown(f"**Adstock:** {config['adstock']}")

            # 5. Goal Variable / Goal Type
            goal_var = config.get("dep_var", "N/A")
            goal_type = config.get("dep_var_type", "N/A")
            st.markdown(
                f"**Goal Variable / Goal Type:** {goal_var} / {goal_type}"
            )

            # 6. Paid Media Spends
            if "paid_media_spends" in config:
                spends = format_list(config["paid_media_spends"])
                st.markdown(f"**Paid Media Spends:** {spends}")

            # 7. Paid Media Variables
            if "paid_media_vars" in config:
                vars = format_list(config["paid_media_vars"])
                st.markdown(f"**Paid Media Variables:** {vars}")

            # 8. Context Variables
            if "context_vars" in config:
                ctx = format_list(config["context_vars"])
                st.markdown(f"**Context Variables:** {ctx}")

            # 9. Factor Variables
            if "factor_vars" in config:
                factors = format_list(config["factor_vars"])
                st.markdown(f"**Factor Variables:** {factors}")

            # 10. Organic Variables
            if "organic_vars" in config:
                organic = format_list(config["organic_vars"])
                st.markdown(f"**Organic Variables:** {organic}")

            # Display additional parameters in an expander
            with st.expander("View full configuration", expanded=False):
                st.json(config)


def render_model_metrics_table(blobs, country, stamp):
    """Render model metrics in a formatted table with color coding"""
    st.subheader("Model Performance Metrics")

    # Create a cache key from blob names
    blob_names = tuple(sorted([b.name for b in blobs]))

    # Extract metrics from blobs (with caching)
    @st.cache_data(ttl=3600, show_spinner="Loading metrics...")
    def _extract_cached_metrics(blob_names_key):
        # Re-extract metrics (blobs aren't directly cacheable, but results are)
        return extract_core_metrics_from_blobs(blobs)

    metrics = _extract_cached_metrics(blob_names)

    if not metrics:
        st.info("No model metrics found.")
        return

    # Define thresholds based on Robyn documentation
    # R2: higher is better (0-1 scale)
    r2_thresholds = {"good": 0.7, "acceptable": 0.5}
    # NRMSE: lower is better (percentage)
    nrmse_thresholds = {"good": 0.15, "acceptable": 0.25}
    # DECOMP.RSSD: lower is better
    decomp_thresholds = {"good": 0.1, "acceptable": 0.2}

    def get_color(value, metric_type):
        """Return color based on value and metric type"""
        if pd.isna(value):
            return "background-color: #f0f0f0"  # gray for missing

        if metric_type == "r2":
            # Higher is better
            if value >= r2_thresholds["good"]:
                return "background-color: #d4edda; color: #155724"  # green
            elif value >= r2_thresholds["acceptable"]:
                return "background-color: #fff3cd; color: #856404"  # yellow
            else:
                return "background-color: #f8d7da; color: #721c24"  # red

        elif metric_type == "nrmse":
            # Lower is better
            if value <= nrmse_thresholds["good"]:
                return "background-color: #d4edda; color: #155724"  # green
            elif value <= nrmse_thresholds["acceptable"]:
                return "background-color: #fff3cd; color: #856404"  # yellow
            else:
                return "background-color: #f8d7da; color: #721c24"  # red

        elif metric_type == "decomp_rssd":
            # Lower is better
            if value <= decomp_thresholds["good"]:
                return "background-color: #d4edda; color: #155724"  # green
            elif value <= decomp_thresholds["acceptable"]:
                return "background-color: #fff3cd; color: #856404"  # yellow
            else:
                return "background-color: #f8d7da; color: #721c24"  # red

        return ""

    # Build the metrics table
    table_data = {
        "Split": ["Train", "Validation", "Test"],
        "Predictive Power (R²)": [
            metrics.get("r2_train"),
            metrics.get("r2_val"),
            metrics.get("r2_test"),
        ],
        "Prediction Accuracy (NRMSE)": [
            metrics.get("nrmse_train"),
            metrics.get("nrmse_val"),
            metrics.get("nrmse_test"),
        ],
        "Business Error (DECOMP.RSSD)": [
            metrics.get("decomp_rssd_train"),
            metrics.get("decomp_rssd_val"),
            metrics.get("decomp_rssd_test"),
        ],
    }

    df = pd.DataFrame(table_data)

    # Apply styling
    def style_metrics(row):
        styles = [""] * len(row)
        if row.name == "Split":
            return styles

        idx = row.name
        styles[1] = get_color(table_data["Predictive Power (R²)"][idx], "r2")
        styles[2] = get_color(
            table_data["Prediction Accuracy (NRMSE)"][idx], "nrmse"
        )
        styles[3] = get_color(
            table_data["Business Error (DECOMP.RSSD)"][idx], "decomp_rssd"
        )
        return styles

    # Format the values
    def format_value(val):
        if pd.isna(val):
            return "N/A"
        return f"{val:.4f}"

    # Create HTML table with styling
    html = "<table style='width:100%; border-collapse: collapse;'>"
    html += "<thead><tr style='background-color: #f8f9fa;'>"
    for col in df.columns:
        html += f"<th style='padding: 12px; text-align: left; border: 1px solid #dee2e6;'>{col}</th>"
    html += "</tr></thead><tbody>"

    for i, row in df.iterrows():
        html += "<tr>"
        html += f"<td style='padding: 12px; border: 1px solid #dee2e6; font-weight: bold;'>{row['Split']}</td>"
        html += f"<td style='padding: 12px; border: 1px solid #dee2e6; {get_color(row['Predictive Power (R²)'], 'r2')}'>{format_value(row['Predictive Power (R²)'])}</td>"
        html += f"<td style='padding: 12px; border: 1px solid #dee2e6; {get_color(row['Prediction Accuracy (NRMSE)'], 'nrmse')}'>{format_value(row['Prediction Accuracy (NRMSE)'])}</td>"
        html += f"<td style='padding: 12px; border: 1px solid #dee2e6; {get_color(row['Business Error (DECOMP.RSSD)'], 'decomp_rssd')}'>{format_value(row['Business Error (DECOMP.RSSD)'])}</td>"
        html += "</tr>"

    html += "</tbody></table>"

    st.markdown(html, unsafe_allow_html=True)

    # Add legend
    st.caption(
        "🟢 Green = Good | 🟡 Yellow = Acceptable | 🔴 Red = Needs Improvement"
    )

    # Display threshold information in an expander
    with st.expander("View metric thresholds", expanded=False):
        st.markdown(
            f"""
        **R² (Predictive Power)** - Higher is better:
        - Good: ≥ {r2_thresholds['good']}
        - Acceptable: ≥ {r2_thresholds['acceptable']}
        - Poor: < {r2_thresholds['acceptable']}

        **NRMSE (Prediction Accuracy)** - Lower is better:
        - Good: ≤ {nrmse_thresholds['good']}
        - Acceptable: ≤ {nrmse_thresholds['acceptable']}
        - Poor: > {nrmse_thresholds['acceptable']}

        **DECOMP.RSSD (Business Error)** - Lower is better:
        - Good: ≤ {decomp_thresholds['good']}
        - Acceptable: ≤ {decomp_thresholds['acceptable']}
        - Poor: > {decomp_thresholds['acceptable']}
        """
        )


def render_metrics_section(blobs, country, stamp):
    st.subheader("Allocator Metrics")
    metrics_csv = find_blob(blobs, "/allocator_metrics.csv")
    metrics_txt = find_blob(blobs, "/allocator_metrics.txt")

    if metrics_csv:
        fn = os.path.basename(metrics_csv.name)
        try:
            csv_data = download_bytes_safe(metrics_csv)
            if csv_data:
                df = pd.read_csv(io.BytesIO(csv_data))
                st.markdown("**Metrics Preview:**")
                st.markdown(
                    df.to_html(escape=False, index=False),
                    unsafe_allow_html=True,
                )
                download_link_for_blob(
                    metrics_csv,
                    label=f"📥 Download {fn}",
                    mime_hint="text/csv",
                    key_suffix=f"metrics_csv|{country}|{stamp}",
                )
            else:
                st.warning(f"Could not read {fn}")
        except Exception as e:
            st.warning(f"Couldn't preview `{fn}` as a table: {e}")
        return

    if metrics_txt:
        fn = os.path.basename(metrics_txt.name)
        try:
            txt_data = download_bytes_safe(metrics_txt)
            if txt_data:
                raw = txt_data.decode("utf-8", errors="ignore")
                rows = []
                for line in raw.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        rows.append({"metric": k.strip(), "value": v.strip()})
                if rows:
                    df = pd.DataFrame(rows)
                    st.markdown("**Metrics:**")
                    st.markdown(
                        df.to_html(escape=False, index=False),
                        unsafe_allow_html=True,
                    )
                else:
                    st.code(raw)
                download_link_for_blob(
                    metrics_txt,
                    label=f"📥 Download {fn}",
                    mime_hint="text/plain",
                    key_suffix=f"metrics_txt|{country}|{stamp}",
                )
            else:
                st.warning(f"Could not read {fn}")
        except Exception as e:
            st.warning(f"Couldn't preview `{fn}`: {e}")
        return

    st.info("No allocator metrics found (allocator_metrics.csv/txt).")


def render_forecast_allocator_section(blobs, country, stamp):
    # Check if there are any forecast allocator plots before showing the section
    idx_blob = find_blob(blobs, "/forecast_allocator_index.csv") or find_blob(
        blobs, "forecast_allocator_index.csv"
    )
    pred_plots = find_pred_allocator_plots(blobs)

    # If no index and no plots, don't render the section at all
    if not idx_blob and not pred_plots:
        return

    st.subheader("🗓️ Forecast allocations (next 3 months)")

    # Prefer the index CSV for metadata + deterministic ordering
    if idx_blob:
        df_idx = read_csv_blob_to_df(idx_blob)
        if df_idx is not None and not df_idx.empty:
            # Gentle normalization
            cols = {c.lower(): c for c in df_idx.columns}
            month_col = cols.get("month") or "month"
            # Sort by month if it looks like YYYY-MM
            try:
                df_idx["_sort"] = pd.to_datetime(
                    df_idx[month_col] + "-01", errors="coerce"
                )
                df_idx = df_idx.sort_values("_sort", kind="mergesort")
            except Exception:
                pass

            st.caption("Plan summary from `forecast_allocator_index.csv`")
            preview_cols = [
                c
                for c in df_idx.columns
                if c.lower()
                in {
                    "month",
                    "budget",
                    "baseline",
                    "incremental",
                    "forecast_total",
                }
            ]
            if preview_cols:
                st.dataframe(df_idx[preview_cols], use_container_width=True)

            download_link_for_blob(
                idx_blob,
                label="📥 Download forecast index (CSV)",
                mime_hint="text/csv",
                key_suffix=f"forecast_idx|{country}|{stamp}",
            )

            # Display images with captions
            st.markdown("---")
            for i, row in df_idx.iterrows():
                image_key = row.get("image_key") or row.get("image_gs") or ""
                image_fn = os.path.basename(str(image_key))
                b = (
                    find_blob(blobs, f"/{image_key}")
                    or find_blob(blobs, image_key)
                    or find_blob(blobs, image_fn)
                )
                if not b:
                    st.warning(
                        f"Image not found for month {row.get('month', '?')}: `{image_key}`"
                    )
                    continue

                try:
                    img = download_bytes_safe(b)
                    if not img:
                        st.warning(f"Empty image data for {image_fn}")
                        continue
                    b64 = base64.b64encode(img).decode()
                    caption_bits = []
                    if "month" in row:
                        caption_bits.append(f"**{row['month']}**")
                    if "budget" in row:
                        caption_bits.append(f"budget={row['budget']:,}")
                    if "incremental" in row:
                        caption_bits.append(
                            f"incremental={row['incremental']:,}"
                        )
                    if "forecast_total" in row:
                        caption_bits.append(f"total={row['forecast_total']:,}")
                    caption = (
                        " · ".join(caption_bits) if caption_bits else image_fn
                    )

                    with st.container(border=True):
                        st.markdown(
                            f'<img src="data:image/png;base64,{b64}" '
                            f'style="width: 100%; height: auto;" alt="{image_fn}">',
                            unsafe_allow_html=True,
                        )
                        st.caption(caption)
                        download_link_for_blob(
                            b,
                            label=f"Download {image_fn}",
                            mime_hint="image/png",
                            key_suffix=f"pred_alloc|{country}|{stamp}|{i}",
                        )
                except Exception as e:
                    st.error(f"Could not display {image_fn}: {e}")
            return

    # Fallback: no index → try to list pred plots directly (already checked above)
    if not pred_plots:
        st.info("No forecast allocator plots found.")
        return

    st.success(f"Found {len(pred_plots)} forecast allocator plot(s)")
    for i, b in enumerate(pred_plots):
        fn = os.path.basename(b.name)
        img = download_bytes_safe(b)
        if not img:
            st.warning(f"Empty image data for {fn}")
            continue
        b64 = base64.b64encode(img).decode()
        with st.container(border=True):
            st.markdown(
                f'<img src="data:image/png;base64,{b64}" '
                f'style="width: 100%; height: auto;" alt="{fn}">',
                unsafe_allow_html=True,
            )
            download_link_for_blob(
                b,
                label=f"Download {fn}",
                mime_hint="image/png",
                key_suffix=f"pred_alloc|{country}|{stamp}|{i}",
            )


def render_allocator_section(blobs, country, stamp):
    # Budget Allocator section - no subheader needed, will be in tab
    alloc_plots = find_allocator_plots(blobs)

    if not alloc_plots:
        st.info("No allocator plots found using standard patterns.")
        with st.expander("Show all PNG files"):
            png_files = [b for b in blobs if b.name.lower().endswith(".png")]
            if not png_files:
                st.write("No PNG files found at all.")
                return
            st.write("Found PNG files:")
            for i, b in enumerate(png_files):
                fn = os.path.basename(b.name)
                st.write(f"- `{fn}` ({b.size:,} bytes)")
                if st.button(
                    f"Display {fn}",
                    key=blob_key("try_png", f"{country}_{stamp}_{i}_{fn}"),
                ):
                    image_data = download_bytes_safe(b)
                    if image_data:
                        b64 = base64.b64encode(image_data).decode()
                        st.markdown(
                            f'<img src="data:image/png;base64,{b64}" style="width: 100%; height: auto;" alt="{fn}">',
                            unsafe_allow_html=True,
                        )
        return

    st.success(f"Found {len(alloc_plots)} allocator plot(s)")
    for i, b in enumerate(alloc_plots):
        try:
            fn = os.path.basename(b.name)
            st.write(f"**{fn}** ({b.size:,} bytes)")
            image_data = download_bytes_safe(b)
            if not image_data:
                st.error("Could not load image data")
                continue
            b64 = base64.b64encode(image_data).decode()
            st.markdown(
                f'<img src="data:image/png;base64,{b64}" style="width: 100%; height: auto;" alt="{fn}">',
                unsafe_allow_html=True,
            )
            download_link_for_blob(
                b,
                label=f"Download {fn}",
                mime_hint="image/png",
                key_suffix=(f"alloc|{country}|{stamp}|{i}"),
            )
        except Exception as e:
            st.error(f"Error with {os.path.basename(b.name)}: {e}")


def render_onepager_section(blobs, best_id, country, stamp):
    # Executive Summary section - no subheader needed, will be in tab
    if not best_id:
        st.warning(
            "best_model_id.txt not found; cannot locate executive summary."
        )
        return

    op_blob = find_onepager_blob(blobs, best_id)
    if not op_blob:
        st.warning(
            f"No executive summary found for best model id '{best_id}' using standard patterns."
        )
        return

    name = os.path.basename(op_blob.name)
    lower = name.lower()
    st.success(f"Found executive summary: **{name}** ({op_blob.size:,} bytes)")

    if lower.endswith(".png"):
        try:
            image_data = download_bytes_safe(op_blob)
            if not image_data:
                st.warning("Image data is empty")
                return
            b64 = base64.b64encode(image_data).decode()
            st.markdown(
                f'<img src="data:image/png;base64,{b64}" style="width: 100%; height: auto;" alt="Executive Summary">',
                unsafe_allow_html=True,
            )
            download_link_for_blob(
                op_blob,
                label=f"Download {name}",
                mime_hint="image/png",
                key_suffix=(f"onepager|{country}|{stamp}"),
            )
        except Exception as e:
            st.error(f"Couldn't preview `{name}`: {e}")
    elif lower.endswith(".pdf"):
        st.info("Executive summary available as PDF (preview not supported).")
        download_link_for_blob(
            op_blob,
            label=f"Download {name}",
            mime_hint="application/pdf",
            key_suffix=(f"onepager|{country}|{stamp}"),
        )


def render_all_files_section(blobs, bucket_name, country, stamp):
    def guess_mime(name: str) -> str:
        n = name.lower()
        if n.endswith(".csv"):
            return "text/csv"
        if n.endswith(".txt") or n.endswith(".log"):
            return "text/plain"
        if n.endswith(".png"):
            return "image/png"
        if n.endswith(".pdf"):
            return "application/pdf"
        if n.endswith(".rds"):
            return "application/octet-stream"
        return "application/octet-stream"

    with st.expander("**All Files (Detailed Analysis)**", expanded=False):
        for i, b in enumerate(sorted(blobs, key=lambda x: x.name)):
            fn = os.path.basename(b.name)
            download_link_for_blob(
                b,
                label=f"⬇️ {fn}",
                mime_hint=guess_mime(fn),
                key_suffix=f"all|{country}|{stamp}|{i}",
            )


# ---------- Sidebar / controls ----------
# Initialize session state for filter persistence
if "view_results_bucket" not in st.session_state:
    st.session_state["view_results_bucket"] = DEFAULT_BUCKET
if "view_results_prefix" not in st.session_state:
    st.session_state["view_results_prefix"] = DEFAULT_PREFIX

with st.sidebar:
    bucket_name = st.text_input("GCS bucket", key="view_results_bucket")
    prefix = st.text_input(
        "Root prefix",
        help="Usually 'robyn/' or narrower like 'robyn/r100/'",
        key="view_results_prefix",
    )

    if prefix and not prefix.endswith("/"):
        prefix = prefix + "/"

    do_scan = st.button("🔄 Refresh listing")

    # Storage self-test
    if st.button("Run storage self-test"):
        try:
            test_blobs = list_blobs(bucket_name, prefix)
            st.write(
                f"Found {len(test_blobs)} blobs under gs://{bucket_name}/{prefix}"
            )
            if test_blobs:
                test_data = download_bytes_safe(test_blobs[0])
                if test_data is not None:
                    st.success("✅ Download OK (storage.objects.get works).")
                else:
                    st.error("❌ Download failed - got empty data")
            else:
                st.info(
                    "Listing returned 0 objects. If you expect data here, check the bucket/prefix and IAM."
                )
        except Exception as e:
            st.error(f"Test failed: {e}")

# ---------- Cache / refresh ----------
if (
    do_scan
    or "runs_cache" not in st.session_state
    or st.session_state.get("last_bucket") != bucket_name
    or st.session_state.get("last_prefix") != prefix
):
    with st.spinner("Loading runs from GCS..."):
        blobs = list_blobs(bucket_name, prefix)
        runs = group_runs(blobs)
        st.session_state["runs_cache"] = runs
        st.session_state["last_bucket"] = bucket_name
        st.session_state["last_prefix"] = prefix
else:
    runs = st.session_state["runs_cache"]

if not runs:
    st.info(
        f"No runs found under gs://{bucket_name}/{prefix}. "
        "If you're on Cloud Run, make sure the service account has at least roles/storage.objectViewer."
    )
    st.stop()

# ---------- Defaults / filters (prefer runs with allocator plots) ----------
# Sort newest-first across all runs (by rev, then stamp)
keys_sorted = sorted(
    runs.keys(),
    key=lambda k: (parse_rev_key(k[0]), parse_stamp(k[2])),
    reverse=True,
)

# Pick the newest (rev,country,stamp) that HAS an allocator plot; fallback to absolute newest
latest_with_alloc = next(
    (k for k in keys_sorted if run_has_allocator_plot(runs[k])), None
)
latest_any = keys_sorted[0]
seed_key = latest_with_alloc or latest_any

default_rev = seed_key[0]

# UI: revision choices
all_revs = sorted({k[0] for k in runs.keys()}, key=parse_rev_key, reverse=True)

# Determine the index for the selectbox (preserve user selection or use default)
# Use separate session state key that persists across navigation
if (
    "view_results_revision_value" in st.session_state
    and st.session_state["view_results_revision_value"] in all_revs
):
    # User has a valid saved selection - use it
    default_rev_index = all_revs.index(
        st.session_state["view_results_revision_value"]
    )
else:
    # First time or invalid selection - use default
    default_rev_index = (
        all_revs.index(default_rev) if default_rev in all_revs else 0
    )

rev = st.selectbox(
    "Revision",
    all_revs,
    index=default_rev_index,
)

# Store selection in persistent session state key (not widget key)
if rev != st.session_state.get("view_results_revision_value"):
    st.session_state["view_results_revision_value"] = rev

# Countries available in this revision
rev_keys = [k for k in runs.keys() if k[0] == rev]
rev_countries = sorted({k[1] for k in rev_keys})

# Default country = newest run WITH allocator plot in this revision (fallback to newest run)
rev_keys_sorted = sorted(
    rev_keys, key=lambda k: parse_stamp(k[2]), reverse=True
)
best_country_key = next(
    (k for k in rev_keys_sorted if run_has_allocator_plot(runs[k])),
    rev_keys_sorted[0],
)
default_country_in_rev = best_country_key[1]

# Determine default countries for multiselect
# Use separate session state key that persists across navigation
if "view_results_countries_value" in st.session_state:
    # User has saved selections - validate and preserve
    current_countries = st.session_state["view_results_countries_value"]
    valid_countries = [c for c in current_countries if c in rev_countries]
    if valid_countries:
        # Has valid selections - use them
        default_countries = valid_countries
    else:
        # All selections are invalid - use default
        default_countries = (
            [default_country_in_rev]
            if default_country_in_rev in rev_countries
            else []
        )
else:
    # First time - use default
    default_countries = [default_country_in_rev]

countries_sel = st.multiselect(
    "Countries",
    rev_countries,
    default=default_countries,
)

# Store selection in persistent session state key (not widget key)
if countries_sel != st.session_state.get("view_results_countries_value"):
    st.session_state["view_results_countries_value"] = countries_sel
if not countries_sel:
    st.info("Select at least one country.")
    st.stop()

# Timestamps available for selected revision and countries
rev_country_keys = [
    k for k in runs.keys() if k[0] == rev and k[1] in countries_sel
]
all_stamps = sorted(
    {k[2] for k in rev_country_keys}, key=parse_stamp, reverse=True
)

# Determine default timestamp for selectbox
# Use separate session state key that persists across navigation
stamp_options = [""] + all_stamps
if "view_results_timestamp_value" in st.session_state:
    # User has a saved selection
    saved_timestamp = st.session_state["view_results_timestamp_value"]
    if saved_timestamp in stamp_options:
        # Valid saved selection - use it
        default_stamp_index = stamp_options.index(saved_timestamp)
    else:
        # Invalid saved selection - use default (empty)
        default_stamp_index = 0
else:
    # First time - use default (empty)
    default_stamp_index = 0

stamp_sel = st.selectbox(
    "Timestamp (optional - select one or leave blank to show latest per country)",
    stamp_options,
    index=default_stamp_index,
)

# Store selection in persistent session state key (not widget key)
if stamp_sel != st.session_state.get("view_results_timestamp_value"):
    st.session_state["view_results_timestamp_value"] = stamp_sel


# ---------- Main renderer ----------
@st.cache_data(ttl=3600, show_spinner=False)
def _get_cached_run_data(
    bucket_name: str, rev: str, country: str, stamp: str, run_key: tuple
):
    """Cache the heavy data operations for a run. Returns cached blobs metadata."""
    # This function caches the fact that we've processed this run
    # The actual blob objects can't be cached (not serializable), but we cache the metadata
    return {
        "bucket": bucket_name,
        "rev": rev,
        "country": country,
        "stamp": stamp,
        "key": run_key,
        "cached_at": dt.datetime.now().isoformat(),
    }


def render_run_for_country(
    bucket_name: str, rev: str, country: str, stamp: str
):
    # Find the specific run
    key = (rev, country, stamp)
    if key not in runs:
        st.warning(f"Run not found for {rev}/{country}/{stamp}.")
        return

    blobs = runs[key]
    best_id, iters, trials = parse_best_meta(blobs)

    # Try to use cached data
    _get_cached_run_data(bucket_name, rev, country, stamp, key)

    # Render model metrics first
    render_model_metrics_table(blobs, country, stamp)

    # Create tabs for Executive Summary and Budget Allocator
    tab1, tab2 = st.tabs(["Executive Summary", "Budget Allocator"])

    with tab1:
        render_onepager_section(blobs, best_id, country, stamp)

    with tab2:
        render_allocator_section(blobs, country, stamp)

    # Model Configuration in its own expander (just above All Files)
    with st.expander("**Model Configuration**", expanded=False):
        render_model_config_section(blobs, country, stamp, bucket_name)

    # All Files at the end
    render_all_files_section(blobs, bucket_name, country, stamp)


def build_run_title(country: str, stamp: str, iters, trials):
    """Build title with country, timestamp, iterations and trials."""
    meta_bits = []
    if iters is not None:
        meta_bits.append(f"iterations={iters}")
    if trials is not None:
        meta_bits.append(f"trials={trials}")
    meta_str = (" · " + " · ".join(meta_bits)) if meta_bits else ""
    return f"{country.upper()} — `{stamp}`{meta_str}"


# ---------- Render selected countries ----------
for ctry in countries_sel:
    # Find the appropriate run for this country
    if stamp_sel:
        # User selected a specific timestamp - use it if it exists for this country
        key = (rev, ctry, stamp_sel)
        if key not in runs:
            st.warning(f"No run found for {ctry} with timestamp {stamp_sel}")
            continue
        country_run = key
    else:
        # No timestamp selected - find the latest run for this country
        candidates = sorted(
            [k for k in runs.keys() if k[0] == rev and k[1] == ctry],
            key=lambda k: parse_stamp(k[2]),
            reverse=True,
        )
        if not candidates:
            st.warning(f"No runs found for {ctry}")
            continue
        # Prefer run with allocator plot, fallback to newest
        country_run = next(
            (k for k in candidates if run_has_allocator_plot(runs[k])),
            candidates[0],
        )

    # Extract stamp from the selected run
    _, _, stamp = country_run
    blobs = runs[country_run]
    _, iters, trials = parse_best_meta(blobs)

    # If multiple countries, use expander; otherwise render directly
    if len(countries_sel) > 1:
        title = build_run_title(ctry, stamp, iters, trials)
        with st.expander(f"**{title}**", expanded=True):
            render_run_for_country(bucket_name, rev, ctry, stamp)
    else:
        render_run_for_country(bucket_name, rev, ctry, stamp)
