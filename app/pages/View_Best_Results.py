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
        ensure_sf_conn,
        keepalive_ping,
        require_login_and_domain,
    )
except Exception:
    ensure_sf_conn = None
    keepalive_ping = None

require_login_and_domain()

# ---------- Page ----------
st.title("Best results browser (GCS)")

# ---------- Settings ----------
DEFAULT_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
DEFAULT_PREFIX = "robyn/"
DATA_URI_MAX_BYTES = int(os.getenv("DATA_URI_MAX_BYTES", str(8 * 1024 * 1024)))
IS_CLOUDRUN = bool(os.getenv("K_SERVICE"))

# ---------- Global defaults for sliders / scoring (persisted) ----------
DEFAULT_WEIGHTS = (0.2, 0.5, 0.3)  # train, val, test
DEFAULT_ALPHA = 1.0
DEFAULT_BETA = 1.0
st.session_state.setdefault("weights", DEFAULT_WEIGHTS)
st.session_state.setdefault("alpha", DEFAULT_ALPHA)
st.session_state.setdefault("beta", DEFAULT_BETA)


def _sf_keepalive(throttle_sec: int = 60) -> None:
    """Ping the shared Snowflake session so it stays warm while users browse."""
    if ensure_sf_conn is None or keepalive_ping is None:
        return
    try:
        import time

        now = time.time()
        last = st.session_state.get("_sf_last_ping", 0)
        if now - last < throttle_sec:
            return
        conn = ensure_sf_conn()  # reuses st.session_state["sf_conn"] if present
        if conn:
            keepalive_ping(conn)  # cheap SELECT 1 / ping
            st.session_state["_sf_last_ping"] = now
            st.session_state["sf_connected"] = True
    except Exception:
        # Never block/break this page if Snowflake isn't configured/available
        pass


_sf_keepalive()


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
    # Accept both:
    #  robyn/<rev>/<country>/<stamp>/file...
    #  robyn/<rev>/<country>/file...               (no explicit stamp dir)
    parts = name.split("/")
    if len(parts) >= 5 and parts[0] == "robyn":
        return {
            "rev": parts[1],
            "country": parts[2],
            "stamp": parts[3],
            "file": "/".join(parts[4:]),
        }
    if len(parts) == 4 and parts[0] == "robyn":
        # Synthesize a stamp so it still groups as a run; label shows as “_root”
        return {
            "rev": parts[1],
            "country": parts[2],
            "stamp": "_root",
            "file": parts[3],
        }
    return None


def group_runs(blobs):
    runs = {}
    for b in blobs:
        info = parse_path(b.name)
        if not info or not info["file"]:
            continue
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


def find_allocator_plots(blobs):
    """Collect allocator plots (PNG) without duplicates."""
    plots, seen = [], set()
    for b in blobs:
        name_l = b.name.lower()
        if name_l.endswith(".png") and (
            "allocator_plots_" in name_l
            or "allocator" in os.path.basename(name_l)
        ):
            if getattr(b, "size", 0) > 1000 and b.name not in seen:
                plots.append(b)
                seen.add(b.name)
    return plots


# ---------- Renderers ----------
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


def render_allocator_section(blobs, country, stamp):
    st.subheader("Allocator Plot")
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
    st.subheader("Onepager")
    if not best_id:
        st.warning("best_model_id.txt not found; cannot locate onepager.")
        return

    op_blob = find_onepager_blob(blobs, best_id)
    if not op_blob:
        st.warning(
            f"No onepager found for best model id '{best_id}' using standard patterns."
        )
        return

    name = os.path.basename(op_blob.name)
    lower = name.lower()
    st.success(f"Found onepager: **{name}** ({op_blob.size:,} bytes)")

    if lower.endswith(".png"):
        try:
            image_data = download_bytes_safe(op_blob)
            if not image_data:
                st.warning("Image data is empty")
                return
            b64 = base64.b64encode(image_data).decode()
            st.markdown(
                f'<img src="data:image/png;base64,{b64}" style="width: 100%; height: auto;" alt="Onepager">',
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
        st.info("Onepager available as PDF (preview not supported).")
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


# --- BEST-MODEL DISCOVERY HELPERS ---------------------------------------------
_METRIC_ALIASES = {
    "r.squared": "r2",
    "rsq": "r2",
    "r2": "r2",
    "nrmse": "nrmse",
    "nrmsd": "nrmse",
    "decomp_rssd": "decomp_rssd",
    "decomprssd": "decomp_rssd",
    "decomp.rssd": "decomp_rssd",  #
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


def _weighted_avg(values, weights):
    pairs = [(v, w) for v, w in zip(values, weights) if pd.notna(v)]
    if not pairs:
        return None
    sw = sum(w for _, w in pairs)
    if sw == 0:
        return None
    return sum(v * w for v, w in pairs) / sw


def _minmax_norm(s: pd.Series) -> pd.Series:
    s = s.copy()
    mask = s.notna()
    if mask.sum() == 0:
        s[:] = 0.5
        return s
    if mask.sum() == 1:
        s.loc[mask] = 0.5
        return s
    v = s[mask]
    lo, hi = v.min(), v.max()
    s.loc[mask] = 0.5 if hi == lo else (v - lo) / (hi - lo)
    return s


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
        # Optional fallback: if a “metric with no split” column exists, copy to all splits
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


def rank_runs_for_country(
    runs: dict, country: str, weights=(0.2, 0.5, 0.3), alpha=1.0, beta=1.0
) -> tuple[tuple, pd.DataFrame]:
    """
    Build a summary table for all (rev, country, stamp) runs, compute a score:
      score = weighted_r2 - alpha*norm_weighted_nrmse - beta*norm_weighted_drssd
    Return (best_key, dataframe_sorted_desc_by_score).
    """
    rows = []
    for (rev, ctry, stamp), blobs in runs.items():
        if ctry != country:
            continue
        metrics = extract_core_metrics_from_blobs(blobs) or {}
        best_id, iters, trials = parse_best_meta(blobs)
        rows.append(
            {
                "rev": rev,
                "country": ctry,
                "stamp": stamp,
                "best_id": best_id,
                "iters": iters,
                "trials": trials,
                "has_alloc": run_has_allocator_plot(blobs),
                "missing_metrics": (len(metrics) == 0),  # NEW
                **metrics,
            }
        )

    if not rows:
        return None, pd.DataFrame()  # type: ignore

    df = pd.DataFrame(rows)

    # Compute weighted aggregates (ignore missing splits)
    w_train, w_val, w_test = weights

    def wavg_row(row, base):
        return _weighted_avg(
            [
                row.get(f"{base}_train"),
                row.get(f"{base}_val"),
                row.get(f"{base}_test"),
            ],
            [w_train, w_val, w_test],
        )

    df["r2_w"] = df.apply(lambda r: wavg_row(r, "r2"), axis=1)  # type: ignore
    df["nrmse_w"] = df.apply(lambda r: wavg_row(r, "nrmse"), axis=1)  # type: ignore
    df["drssd_w"] = df.apply(lambda r: wavg_row(r, "decomp_rssd"), axis=1)  # type: ignore

    # Normalize "lower is better" terms across candidates to [0,1]
    df["nrmse_w_norm"] = _minmax_norm(df["nrmse_w"])
    df["drssd_w_norm"] = _minmax_norm(df["drssd_w"])

    # Final score
    df["score"] = (
        df["r2_w"].fillna(-1e9)
        - alpha * df["nrmse_w_norm"]
        - beta * df["drssd_w_norm"]
    )

    df_sorted = df.sort_values(
        ["score", "r2_w"], ascending=[False, False]
    ).reset_index(drop=True)
    top = df_sorted.iloc[0]
    best_key = (top["rev"], country, top["stamp"])
    return best_key, df_sorted


def render_run_from_key(runs: dict, key: tuple, bucket_name: str):
    rev, country, stamp = key
    blobs = runs[key]
    best_id, iters, trials = parse_best_meta(blobs)

    # Render sections in the specified order
    render_metrics_section(blobs, country, stamp)
    render_onepager_section(blobs, best_id, country, stamp)
    render_allocator_section(blobs, country, stamp)
    render_all_files_section(blobs, bucket_name, country, stamp)


# ---------- Sidebar / controls ----------
with st.sidebar:
    bucket_name = st.text_input("GCS bucket", value=DEFAULT_BUCKET)
    prefix = st.text_input(
        "Root prefix",
        value=DEFAULT_PREFIX,
        help="Usually 'robyn/' or narrower like 'robyn/r100/'",
    )
    if prefix and not prefix.endswith("/"):
        prefix = prefix + "/"

    do_scan = st.button("🔄 Refresh listing")

    force_rescan = st.button("🧹 Force re-scan (clear cache)")
    if force_rescan:
        st.session_state.pop("runs_cache", None)

    st.subheader("Best-model scoring")
    auto_best = st.checkbox(
        "Auto-pick best across ALL revisions per country", value=True
    )

    # Sliders with persistent defaults
    w_train = st.slider(
        "Train weight", 0.0, 1.0, st.session_state["weights"][0], 0.05
    )
    w_val = st.slider(
        "Validation weight", 0.0, 1.0, st.session_state["weights"][1], 0.05
    )
    w_test = st.slider(
        "Test weight", 0.0, 1.0, st.session_state["weights"][2], 0.05
    )
    ws = [w_train, w_val, w_test]
    s = sum(ws) or 1.0
    st.session_state["weights"] = (ws[0] / s, ws[1] / s, ws[2] / s)

    st.session_state["alpha"] = st.slider(
        "Penalty α for NRMSE (lower is better)",
        0.0,
        3.0,
        st.session_state["alpha"],
        0.1,
    )
    st.session_state["beta"] = st.slider(
        "Penalty β for Decomp RSSD (lower is better)",
        0.0,
        3.0,
        st.session_state["beta"],
        0.1,
    )

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


# ---------- Manual browse helpers (for non-auto mode) ----------
def render_run_for_country(bucket_name: str, rev: str, country: str):
    # All candidate runs for this (rev, country), newest first
    candidates = sorted(
        [k for k in runs.keys() if k[0] == rev and k[1] == country],
        key=lambda k: parse_stamp(k[2]),
        reverse=True,
    )
    if not candidates:
        st.warning(f"No runs found for {rev}/{country}.")
        return

    # Prefer the newest run that HAS an allocator plot; fallback to newest
    key = next(
        (k for k in candidates if run_has_allocator_plot(runs[k])),
        candidates[0],
    )

    _, _, stamp = key
    blobs = runs[key]
    best_id, iters, trials = parse_best_meta(blobs)

    # Render sections in the specified order
    render_metrics_section(blobs, country, stamp)
    render_onepager_section(blobs, best_id, country, stamp)
    render_allocator_section(blobs, country, stamp)
    render_all_files_section(blobs, bucket_name, country, stamp)


# ---------- Mode: manual browse by revision ----------
if not auto_best:
    # Sort newest-first across all runs (by rev key, then stamp)
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
    all_revs = sorted(
        {k[0] for k in runs.keys()}, key=parse_rev_key, reverse=True
    )
    rev = st.selectbox("Revision", all_revs, index=all_revs.index(default_rev))

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

    countries_sel = st.multiselect(
        "Countries",
        rev_countries,
        default=(
            [default_country_in_rev]
            if default_country_in_rev in rev_countries
            else []
        ),
    )
    if not countries_sel:
        st.info("Select at least one country.")
        st.stop()

    for ctry in countries_sel:
        # Use expander if multiple countries
        if len(countries_sel) > 1:
            with st.expander(f"**{ctry.upper()}**", expanded=True):
                render_run_for_country(bucket_name, rev, ctry)  # type: ignore
        else:
            render_run_for_country(bucket_name, rev, ctry)  # type: ignore

# ---------- Mode: auto best across all revisions ----------
else:
    # Countries available across ALL revisions
    all_countries = sorted({k[1] for k in runs.keys()})
    if not all_countries:
        st.info("No countries found in the provided prefix.")
        st.stop()

    countries_sel = st.multiselect(
        "Countries",
        all_countries,
        default=[all_countries[0]],
    )

    if not countries_sel:
        st.info("Select at least one country.")
        st.stop()

    for ctry in countries_sel:
        best_key, table = rank_runs_for_country(
            runs,
            ctry,
            weights=st.session_state["weights"],
            alpha=st.session_state["alpha"],
            beta=st.session_state["beta"],
        )
        if best_key is None:
            st.warning(
                f"No metric-bearing runs found for {ctry}. Showing newest run instead."
            )
            candidates = sorted(
                [k for k in runs.keys() if k[1] == ctry],
                key=lambda k: parse_stamp(k[2]),
                reverse=True,
            )
            if not candidates:
                st.info(f"No runs at all for {ctry}.")
                continue
            best_key = candidates[0]

            # Use expander if multiple countries
            if len(countries_sel) > 1:
                with st.expander(f"**{ctry.upper()}**", expanded=True):
                    render_run_from_key(runs, best_key, bucket_name)
            else:
                render_run_from_key(runs, best_key, bucket_name)
            continue

        # Use expander if multiple countries
        if len(countries_sel) > 1:
            with st.expander(f"**{ctry.upper()}**", expanded=True):
                # Show ranking table
                with st.expander(
                    f"Ranking table (higher score is better)",
                    expanded=False,
                ):
                    cols = [
                        "score",
                        "r2_w",
                        "nrmse_w",
                        "drssd_w",
                        "rev",
                        "stamp",
                        "best_id",
                        "has_alloc",
                        "r2_train",
                        "r2_val",
                        "r2_test",
                        "nrmse_train",
                        "nrmse_val",
                        "nrmse_test",
                        "decomp_rssd_train",
                        "decomp_rssd_val",
                        "decomp_rssd_test",
                    ]
                    display = table[
                        [c for c in cols if c in table.columns]
                    ].copy()
                    st.dataframe(display, use_container_width=True)

                render_run_from_key(runs, best_key, bucket_name)
        else:
            # Show ranking table
            with st.expander(
                f"Ranking table for {ctry.upper()} (higher score is better)",
                expanded=False,
            ):
                cols = [
                    "score",
                    "r2_w",
                    "nrmse_w",
                    "drssd_w",
                    "rev",
                    "stamp",
                    "best_id",
                    "has_alloc",
                    "r2_train",
                    "r2_val",
                    "r2_test",
                    "nrmse_train",
                    "nrmse_val",
                    "nrmse_test",
                    "decomp_rssd_train",
                    "decomp_rssd_val",
                    "decomp_rssd_test",
                ]
                display = table[[c for c in cols if c in table.columns]].copy()
                st.dataframe(display, use_container_width=True)

            render_run_from_key(runs, best_key, bucket_name)
