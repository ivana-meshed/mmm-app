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
    default=[default_country_in_rev],
)
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

# Single timestamp selection - if not selected, will show latest for each country
stamp_sel = st.selectbox(
    "Timestamp (optional - select one or leave blank to show latest per country)",
    [""] + all_stamps,
    index=0,
)


# ---------- Main renderer ----------
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

    # Render sections in the specified order
    render_metrics_section(blobs, country, stamp)
    render_onepager_section(blobs, best_id, country, stamp)
    render_allocator_section(blobs, country, stamp)
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
