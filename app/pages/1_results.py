# app/pages/1_Results.py
import os, io, re, datetime, hashlib
import pandas as pd
import streamlit as st
from google.cloud import storage
from urllib.parse import quote

st.set_page_config(page_title="Results: Robyn MMM", layout="wide")
st.title("📦 Results browser (GCS)")

# ---------- Settings / Auth ----------
DEFAULT_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
DEFAULT_PREFIX = "robyn/"

@st.cache_resource
def gcs_client():
    return storage.Client()

client = gcs_client()

# ---------- Helpers ----------

def gcs_console_url(bucket: str, prefix: str) -> str:
    # keep "/" unencoded so the Console URL resolves correctly
    return f"https://console.cloud.google.com/storage/browser/{bucket}/{quote(prefix, safe='/')}"

def blob_key(prefix: str, name: str) -> str:
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{h}"

def list_blobs(bucket_name: str, prefix: str):
    # Safer listing with visible errors if IAM is missing
    try:
        bucket = client.bucket(bucket_name)
        return list(client.list_blobs(bucket_or_name=bucket, prefix=prefix))
    except Exception as e:
        st.error(f"❌ Failed to list gs://{bucket_name}/{prefix} — {e}")
        return []


def parse_path(name: str):
    # Expected: robyn/<rev>/<country>/<stamp>/file...
    parts = name.split("/")
    if len(parts) >= 5 and parts[0] == "robyn":
        return {"rev": parts[1], "country": parts[2], "stamp": parts[3], "file": "/".join(parts[4:])}
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
    # "MMDD_HHMMSS" -> datetime; fallback to lexical
    try:
        return datetime.datetime.strptime(stamp, "%m%d_%H%M%S")
    except Exception:
        return stamp  # lexical
def parse_rev_key(rev: str):
    # numeric-aware sort key: ("is_non_numeric", value)
    m = re.search(r'(\d+)$', (rev or '').strip())
    if m:
        return (0, int(m.group(1)))   # numeric revs sort before non-numeric
    return (1, (rev or '').lower())   # fallback: lexical

    
def parse_best_meta(blobs):
    """Return (best_id, iterations, trials) from best_model_id.txt if present."""
    b = find_blob(blobs, "/best_model_id.txt") or find_blob(blobs, "best_model_id.txt")
    best_id, iters, trials = None, None, None
    if not b:
        return best_id, iters, trials
    txt = read_text_blob(b)
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    if lines:
        best_id = lines[0].split()[0]
    for ln in lines[1:]:
        m = re.search(r"Iterations:\s*(\d+)", ln, re.I)
        if m: iters = int(m.group(1))
        m = re.search(r"Trials:\s*(\d+)", ln, re.I)
        if m: trials = int(m.group(1))
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

def latest_stamp_for_country(runs, rev, country):
    # Return latest (rev,country,stamp) or None
    keys = [k for k in runs.keys() if k[0] == rev and k[1] == country]
    if not keys:
        return None
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

def read_text_blob(blob) -> str:
    return blob.download_as_bytes().decode("utf-8", errors="replace")

def find_blob(blobs, endswith: str):
    for b in blobs:
        if b.name.endswith(endswith):
            return b
    for b in blobs:
        if os.path.basename(b.name) == os.path.basename(endswith):
            return b
    return None

def find_all(blobs, pattern):
    r = re.compile(pattern, re.IGNORECASE)
    return [b for b in blobs if r.search(b.name)]

def parse_best_meta(blobs):
    """Return (best_id, iterations, trials) from best_model_id.txt if present."""
    b = find_blob(blobs, "/best_model_id.txt") or find_blob(blobs, "best_model_id.txt")
    best_id, iters, trials = None, None, None
    if not b:
        return best_id, iters, trials
    txt = read_text_blob(b)
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    if lines:
        best_id = lines[0].split()[0]
    for ln in lines[1:]:
        m = re.search(r"Iterations:\s*(\d+)", ln, re.I)
        if m: iters = int(m.group(1))
        m = re.search(r"Trials:\s*(\d+)", ln, re.I)
        if m: trials = int(m.group(1))
    return best_id, iters, trials

def find_onepager_blob(blobs, best_id: str):
    # Prefer {id}.png, fallback {id}.pdf anywhere in the run
    for ext in (".png", ".pdf"):
        b = find_blob(blobs, f"/{best_id}{ext}")
        if b: return b
        for bb in blobs:
            if os.path.basename(bb.name).lower() == f"{best_id}{ext}":
                return bb
    return None

# ---------- Sidebar (filters + refresh) ----------
with st.sidebar:
    bucket_name = st.text_input("GCS bucket", value=DEFAULT_BUCKET)
    prefix = st.text_input("Root prefix", value=DEFAULT_PREFIX, help="Usually 'robyn/' or narrower like 'robyn/r100/'")

    # NEW: make sure prefix ends with a slash (GCS treats prefixes literally)
    if prefix and not prefix.endswith("/"):
        prefix = prefix + "/"

    do_scan = st.button("🔄 Refresh listing")

    # NEW: quick IAM/permission self-test
    if st.button("Run storage self-test"):
        try:
            test_blobs = list_blobs(bucket_name, prefix)
            st.write(f"Found {len(test_blobs)} blobs under gs://{bucket_name}/{prefix}")
            if test_blobs:
                # Try an actual download to verify storage.objects.get
                _ = test_blobs[0].download_as_bytes()
                st.success("Download OK (storage.objects.get works).")
            elif len(test_blobs) == 0:
                st.info("Listing returned 0 objects. If you expect data here, check the bucket/prefix and IAM.")
        except Exception as e:
            st.error(f"Download failed (likely missing storage.objects.get): {e}")


# Cache & refresh
if do_scan or "runs_cache" not in st.session_state or \
   st.session_state.get("last_bucket") != bucket_name or \
   st.session_state.get("last_prefix") != prefix:
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
        f"If you’re on Cloud Run, make sure the service account has at least roles/storage.objectViewer."
    )
    st.stop()

# ---------- Determine current (latest overall) run ----------
current_key = latest_run_key(runs)
if not current_key:
    st.warning("No runs found.")
    st.stop()
current_rev, current_country, current_stamp = current_key

# ---------- Filters (default to current run & country) ----------
all_revs = sorted({k[0] for k in runs.keys()}, key=parse_rev_key, reverse=True)
latest_any = sorted(runs.keys(), key=lambda k: (parse_rev_key(k[0]), parse_stamp(k[2])), reverse=True)[0]
default_rev, default_country = latest_any[0], latest_any[1]

rev = st.selectbox("Revision", all_revs, index=all_revs.index(default_rev))

# Countries that have this revision
rev_countries = sorted({k[1] for k in runs.keys() if k[0] == rev})
# Default country = the country of the latest run within this revision
latest_in_rev = sorted([k for k in runs.keys() if k[0] == rev],
                       key=lambda k: parse_stamp(k[2]),
                       reverse=True)[0]
default_country_in_rev = latest_in_rev[1]

countries_sel = st.multiselect(
    "Countries (latest run per selected country will be shown)",
    rev_countries,
    default=[default_country_in_rev],
)

if not countries_sel:
    st.info("Select at least one country.")
    st.stop()

# Small renderer to avoid copy/paste and ensure unique keys
def render_run_for_country(bucket_name: str, rev: str, country: str):
    # Pick latest stamp for this (rev,country)
    try:
        key = sorted(
            [k for k in runs.keys() if k[0] == rev and k[1] == country],
            key=lambda k: parse_stamp(k[2]),
            reverse=True
        )[0]
    except IndexError:
        st.warning(f"No runs found for {rev}/{country}.")
        return

    _, _, stamp = key
    blobs = runs[key]
    best_id, iters, trials = parse_best_meta(blobs)

    country_disp = country.upper()
    meta_bits = []
    if iters is not None:  meta_bits.append(f"iterations={iters}")
    if trials is not None: meta_bits.append(f"trials={trials}")
    meta_str = (" · " + " · ".join(meta_bits)) if meta_bits else ""

    st.markdown(f"### {country_disp} — latest: `{stamp}`{meta_str}")

    # Link to the run folder in Cloud Console
    prefix_path = f"robyn/{rev}/{country}/{stamp}/"
    gcs_url = gcs_console_url(bucket_name, prefix_path)
    st.markdown(
        f'**Path:** <a href="{gcs_url}" target="_blank">gs://{bucket_name}/{prefix_path}</a>',
        unsafe_allow_html=True,
    )

    # --- 📊 Allocator metrics (CSV preferred, fallback TXT)
    st.subheader("📊 Allocator metrics")
    metrics_csv = find_blob(blobs, "/allocator_metrics.csv")
    metrics_txt = find_blob(blobs, "/allocator_metrics.txt")
    if metrics_csv:
        fn = os.path.basename(metrics_csv.name)
        # Preview table (best effort)
        try:
            df = pd.read_csv(io.BytesIO(download_bytes(metrics_csv)))
            st.dataframe(df, use_container_width=True)
        except Exception as e:
            st.warning(f"Couldn't preview `{fn}` as a table: {e}")

        # Signed URL (nice-to-have)
        url = try_signed_url(metrics_csv)
        if url:
            st.markdown(f"⬇️ [Open {fn} (signed URL)]({url})")

        # Guaranteed fallback: stream bytes from server
        try:
            st.download_button(
                f"Download {fn}",
                data=download_bytes(metrics_csv),
                file_name=fn,
                mime="text/csv",
                key=blob_key(f"dl_metrics_csv_{country}_{stamp}", metrics_csv.name),
            )
        except Exception as e:
            st.warning(f"Could not stream `{fn}`: {e}")

    elif metrics_txt:
        fn = os.path.basename(metrics_txt.name)
        # Try to render as key/value table
        try:
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
        except Exception as e:
            st.warning(f"Couldn't preview `{fn}`: {e}")

        # Signed URL (nice-to-have)
        url = try_signed_url(metrics_txt)
        if url:
            st.markdown(f"⬇️ [Open {fn} (signed URL)]({url})")

        # Guaranteed fallback
        try:
            st.download_button(
                f"Download {fn}",
                data=(raw.encode("utf-8") if 'raw' in locals() else download_bytes(metrics_txt)),
                file_name=fn,
                mime="text/plain",
                key=blob_key(f"dl_metrics_txt_{country}_{stamp}", metrics_txt.name),
            )
        except Exception as e:
            st.warning(f"Could not stream `{fn}`: {e}")
    else:
        st.info("No allocator metrics found (allocator_metrics.csv/txt).")

    # --- 📈 Allocator plot(s)
    st.subheader("📈 Allocator plot")
    alloc_pngs = find_all(blobs, r"(?:^|/)allocator_.*\.png$")
    if alloc_pngs:
        for b in alloc_pngs:
            try:
                st.image(download_bytes(b),
                         caption=os.path.basename(b.name),
                         use_container_width=True)
            except Exception as e:
                st.warning(f"Couldn't show `{b.name}`: {e}")
    else:
        st.info("No allocator PNG found (expecting `allocator_*.png`).")

    # --- 🧾 Onepager (best_model_id.{png|pdf})
    st.subheader("🧾 Onepager")
    if best_id:
        op_blob = find_onepager_blob(blobs, best_id)
        if op_blob:
            name = os.path.basename(op_blob.name)
            lower = name.lower()

            # Preview inline for PNG
            if lower.endswith(".png"):
                try:
                    st.image(download_bytes(op_blob), caption="onepager", use_container_width=True)
                except Exception as e:
                    st.warning(f"Couldn't preview `{name}`: {e}")
            elif lower.endswith(".pdf"):
                st.info("Onepager available as PDF.")

            # Signed URL (nice-to-have)
            url = try_signed_url(op_blob)
            if url:
                st.markdown(f"⬇️ [Open {name} (signed URL)]({url})")

            # Guaranteed fallback: stream bytes
            try:
                st.download_button(
                    "Download onepager",
                    data=download_bytes(op_blob),
                    file_name=name,
                    mime=("image/png" if lower.endswith(".png") else "application/pdf"),
                    key=blob_key(f"dl_onepager_any_{country}_{stamp}", op_blob.name),
                )
            except Exception as e:
                st.warning(f"Could not stream `{name}`: {e}")
        else:
            st.warning(f"No onepager found for best model id '{best_id}' (expected {best_id}.png or .pdf).")
    else:
        st.warning("best_model_id.txt not found; cannot locate onepager.")

    # --- 📁 All files in this run
    st.subheader("📁 All files")
    for b in sorted(blobs, key=lambda x: x.name):
        fn = os.path.basename(b.name)
        url = try_signed_url(b)
        with st.container(border=True):
            st.write(f"`{fn}` — {b.size:,} bytes")
            if url:
                st.markdown(f"[Open / Download (signed URL)]({url})")
            else:
                mime = "application/octet-stream"
                if fn.lower().endswith(".csv"): mime = "text/csv"
                elif fn.lower().endswith(".txt"): mime = "text/plain"
                elif fn.lower().endswith(".png"): mime = "image/png"
                elif fn.lower().endswith(".pdf"): mime = "application/pdf"
                try:
                    st.download_button(
                        f"Download {fn}",
                        data=download_bytes(b),
                        file_name=fn,
                        mime=mime,
                        key=blob_key(f"dl_any_{country}_{stamp}", b.name),
                    )
                except Exception as e:
                    st.warning(f"Could not stream `{fn}` from server: {e}")

# ---- Render each selected country’s latest run for this revision ----
st.markdown(f"## Detailed View — revision `{rev}`")
for c in countries_sel:
    with st.container():
        render_run_for_country(bucket_name, rev, c)
        st.divider()