# app/pages/1_Results.py
import datetime
import hashlib
import io
import os
import re
from urllib.parse import quote

import pandas as pd
import streamlit as st
from google.cloud import storage

st.set_page_config(page_title="Results: Robyn MMM", layout="wide")
st.title("📦 Results browser (GCS)")

# ---------- Settings / Auth ----------
DEFAULT_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
DEFAULT_PREFIX = "robyn/"


@st.cache_resource
def gcs_client():
    return storage.Client()


client = gcs_client()


def blob_key(prefix: str, name: str) -> str:
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{h}"


def list_blobs(bucket_name: str, prefix: str):
    # Recursive listing (includes files in subfolders)
    bucket = client.bucket(bucket_name)
    return list(client.list_blobs(bucket_or_name=bucket, prefix=prefix))


def parse_path(name: str):
    # Expected: robyn/<rev>/<country>/<stamp>/file...
    parts = name.split("/")
    if len(parts) >= 5 and parts[0] == "robyn":
        return {
            "rev": parts[1],
            "country": parts[2],
            "stamp": parts[3],
            "file": "/".join(parts[4:]),
        }
    return None


def group_runs(blobs):
    runs = {}  # (rev, country, stamp) -> [blob...]
    for b in blobs:
        info = parse_path(b.name)
        if not info or not info["file"]:  # skip folder markers
            continue
        key = (info["rev"], info["country"], info["stamp"])
        runs.setdefault(key, []).append(b)
    return runs


def parse_stamp(stamp: str):
    # "MMDD_HHMMSS" -> datetime; fallback to lexical order if parse fails
    try:
        return datetime.datetime.strptime(stamp, "%m%d_%H%M%S")
    except Exception:
        return stamp  # lexical


def parse_best_meta(blobs):
    """Return (best_id, iterations, trials) from best_model_id.txt if present."""
    b = find_blob(blobs, "/best_model_id.txt") or find_blob(
        blobs, "best_model_id.txt"
    )
    best_id, iters, trials = None, None, None
    if not b:
        return best_id, iters, trials
    txt = read_text_blob(b)
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
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
    # Sort by parsed timestamp descending
    keys.sort(key=lambda k: parse_stamp(k[2]), reverse=True)
    return keys[0]


def try_signed_url(blob, minutes=60):
    try:
        return blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=minutes),
            method="GET",
        )
    except Exception:
        return None


def download_bytes(blob):
    return blob.download_as_bytes()


def find_blob(blobs, endswith: str):
    for b in blobs:
        if b.name.endswith(endswith):
            return b
    # also allow exact base-name match
    for b in blobs:
        if os.path.basename(b.name) == os.path.basename(endswith):
            return b
    return None


def find_all(blobs, pattern):
    r = re.compile(pattern, re.IGNORECASE)
    return [b for b in blobs if r.search(b.name)]


def read_text_blob(blob) -> str:
    return blob.download_as_bytes().decode("utf-8", errors="replace")


def find_onepager_blob(blobs, best_id: str):
    # Prefer {id}.png, fallback {id}.pdf anywhere under the run
    for ext in (".png", ".pdf"):
        b = find_blob(blobs, f"/{best_id}{ext}")
        if b:
            return b
        # also check root-level file names
        for bb in blobs:
            if os.path.basename(bb.name).lower() == f"{best_id}{ext}":
                return bb
    return None


# ---------- Sidebar (filters + refresh) ----------
with st.sidebar:
    bucket_name = st.text_input("GCS bucket", value=DEFAULT_BUCKET)
    prefix = st.text_input(
        "Root prefix",
        value=DEFAULT_PREFIX,
        help="Usually 'robyn/' or narrower like 'robyn/r100/it/'",
    )
    rev_filter = st.text_input("(Optional) limit to revision", value="") or None
    country_filter = (
        st.text_input("(Optional) limit to country", value="") or None
    )
    do_scan = st.button("🔄 Refresh listing")

# Cache & refresh
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
    st.info("No runs found under this prefix.")
    st.stop()

# ---------- Auto-pick latest run (respecting optional filters) ----------
key = latest_run_key(runs, rev_filter=rev_filter, country_filter=country_filter)
if not key:
    st.warning("No runs match the selected filters.")
    st.stop()

rev, country, stamp = key
selected = runs[key]

best_id, iters, trials = parse_best_meta(selected)

country_disp = country.upper()
extra = ""
if iters is not None:
    extra += f" · iterations={iters}"
if trials is not None:
    extra += f" · trials={trials}"

st.caption(
    f"Auto-selected latest run: **revision={rev} · country={country_disp} · stamp={stamp}{extra}**"
)
# Build folder path and a Console URL
prefix_path = f"robyn/{rev}/{country}/{stamp}/"  # keep actual case used in GCS

gcs_url = f"https://console.cloud.google.com/storage/browser/{bucket_name}/{quote(prefix_path)}"

# Clickable path that opens in a new tab
st.markdown(
    f'**Path:** <a href="{gcs_url}" target="_blank">gs://{bucket_name}/{prefix_path}</a>',
    unsafe_allow_html=True,
)

# ---------- Inline displays ----------
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("📈 Allocator plot")
    # Match any .../allocator_*.png (subfolders included)
    alloc_pngs = find_all(selected, r"(?:^|/)allocator_.*\.png$")
    if alloc_pngs:
        for b in alloc_pngs:
            st.image(
                download_bytes(b),
                caption=os.path.basename(b.name),
                use_container_width=True,
            )
    else:
        st.info("No allocator PNG found (expecting `allocator_*.png`).")

    st.subheader("🧾 Onepager")

    if best_id:
        op_blob = find_onepager_blob(selected, best_id)
        if op_blob:
            name = os.path.basename(op_blob.name)
            data = download_bytes(op_blob)
            if name.lower().endswith(".png"):
                st.image(data, caption="onepager", use_container_width=True)
                st.download_button(
                    "Download onepager",
                    data=data,
                    file_name=name,
                    mime="image/png",
                    key=blob_key("dl_onepager_png", op_blob.name),
                )
            else:
                st.info("Onepager is a PDF.")
                st.download_button(
                    "Download onepager",
                    data=data,
                    file_name=name,
                    mime="application/pdf",
                    key=blob_key("dl_onepager_pdf", op_blob.name),
                )
        else:
            st.warning(
                f"No onepager found for best model id '{best_id}' (expected {best_id}.png or .pdf)."
            )
    else:
        st.warning("best_model_id.txt not found; cannot locate onepager.")

with col2:
    st.subheader("📊 Allocator metrics")
    metrics_csv = find_blob(selected, "/allocator_metrics.csv")
    metrics_txt = find_blob(selected, "/allocator_metrics.txt")
    if metrics_csv:
        df = pd.read_csv(io.BytesIO(download_bytes(metrics_csv)))
        st.dataframe(df, use_container_width=True)
        url = try_signed_url(metrics_csv)
        if url:
            st.markdown(
                f"⬇️ [Download {os.path.basename(metrics_csv.name)}]({url})"
            )
        else:
            st.download_button(
                "Download allocator_metrics.csv",
                data=download_bytes(metrics_csv),
                file_name="allocator_metrics.csv",
                mime="text/csv",
                key=blob_key("dl_metrics_csv", metrics_csv.name),
            )
    elif metrics_txt:
        raw = download_bytes(metrics_txt).decode("utf-8", errors="ignore")
        rows = []
        for line in raw.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                rows.append({"metric": k.strip(), "value": v.strip()})
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        else:
            st.code(raw)
        url = try_signed_url(metrics_txt)
        if url:
            st.markdown(
                f"⬇️ [Download {os.path.basename(metrics_txt.name)}]({url})"
            )
        else:
            st.download_button(
                "Download allocator_metrics.txt",
                data=raw.encode("utf-8"),
                file_name="allocator_metrics.txt",
                mime="text/plain",
                key=blob_key("dl_metrics_txt", metrics_txt.name),
            )
    else:
        st.info("No allocator metrics found (allocator_metrics.csv/txt).")

st.divider()
st.subheader("📁 All files in this run")
for b in sorted(selected, key=lambda x: x.name):
    fn = os.path.basename(b.name)
    url = try_signed_url(b)
    with st.container(border=True):
        st.write(f"`{fn}` — {b.size:,} bytes")
        if url:
            st.markdown(f"[Open / Download (signed URL)]({url})")
        else:
            mime = "application/octet-stream"
            if fn.lower().endswith(".csv"):
                mime = "text/csv"
            elif fn.lower().endswith(".txt"):
                mime = "text/plain"
            elif fn.lower().endswith(".png"):
                mime = "image/png"
            elif fn.lower().endswith(".pdf"):
                mime = "application/pdf"
            st.download_button(
                f"Download {fn}",
                data=download_bytes(b),
                file_name=fn,
                mime=mime,
                key=blob_key("dl_any", b.name),
            )
