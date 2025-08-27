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
    """Generate signed URL with better error handling"""
    try:
        url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=minutes),
            method="GET",
        )
        return url
    except Exception as e:
        st.warning(f"Signed URL generation failed: {e}")
        return None

def download_bytes(blob):
    """Download blob with error handling"""
    try:
        data = blob.download_as_bytes()
        if len(data) == 0:
            st.warning(f"Downloaded file is empty: {blob.name}")
            return None
        return data
    except Exception as e:
        st.error(f"Download failed for {blob.name}: {e}")
        return None

def read_text_blob(blob) -> str:
    try:
        return blob.download_as_bytes().decode("utf-8", errors="replace")
    except Exception as e:
        st.error(f"Failed to read text from {blob.name}: {e}")
        return ""

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

def find_onepager_blob(blobs, best_id: str):
    """Find onepager with improved logic to handle actual Robyn file patterns"""
    
    # First try exact match with best_id
    for ext in (".png", ".pdf"):
        exact_match = f"{best_id}{ext}"
        for b in blobs:
            if os.path.basename(b.name).lower() == exact_match.lower():
                return b
    
    # Then try any PNG/PDF that might be a onepager
    # Robyn often generates files like "1_7_3.png" regardless of the model ID
    for ext in (".png", ".pdf"):
        candidates = []
        for b in blobs:
            filename = os.path.basename(b.name).lower()
            # Look for files that could be onepagers (exclude known other types)
            if (filename.endswith(ext) and 
                not filename.startswith('allocator') and
                not filename.startswith('response') and
                not filename.startswith('saturation') and
                b.size > 50000):  # Must be reasonably large (>50KB)
                candidates.append(b)
        
        if candidates:
            # Sort by size (largest first, as onepagers are usually big)
            candidates.sort(key=lambda x: x.size, reverse=True)
            return candidates[0]
    
    return None

def find_allocator_plots(blobs):
    """Find allocator plots with improved search logic"""
    allocator_plots = []
    
    # Look for files with 'allocator' in the name
    for b in blobs:
        filename = os.path.basename(b.name).lower()
        if ('allocator' in filename and 
            filename.endswith('.png') and 
            b.size > 1000):  # Must be reasonably sized
            allocator_plots.append(b)
    
    # Also look for plots in allocator_plots subdirectories
    # Since your files might be in allocator_plots_TIMESTAMP/
    for b in blobs:
        if 'allocator_plots_' in b.name and b.name.endswith('.png') and b.size > 1000:
            allocator_plots.append(b)
    
    return allocator_plots

def debug_blob_info(blobs):
    """Helper function to debug what files we actually have"""
    st.write("**Debug: All files in this run:**")
    
    file_info = []
    for b in blobs:
        file_info.append({
            "name": b.name,
            "basename": os.path.basename(b.name),
            "size_bytes": b.size,
            "size_mb": round(b.size / 1024 / 1024, 2),
            "is_png": b.name.lower().endswith('.png'),
            "is_pdf": b.name.lower().endswith('.pdf'),
        })
    
    # Sort by size (largest first)
    file_info.sort(key=lambda x: x["size_bytes"], reverse=True)
    
    for info in file_info:
        st.write(f"- `{info['basename']}` ({info['size_mb']} MB) - {info['name']}")

def render_metrics_section(blobs):
    """Render the allocator metrics section"""
    st.subheader("📊 Allocator metrics")
    
    metrics_csv = find_blob(blobs, "/allocator_metrics.csv")
    metrics_txt = find_blob(blobs, "/allocator_metrics.txt")
    
    if metrics_csv:
        fn = os.path.basename(metrics_csv.name)
        # Preview table (best effort)
        try:
            csv_data = download_bytes(metrics_csv)
            if csv_data:
                df = pd.read_csv(io.BytesIO(csv_data))
                st.dataframe(df, use_container_width=True)
            else:
                st.warning(f"Could not read {fn}")
        except Exception as e:
            st.warning(f"Couldn't preview `{fn}` as a table: {e}")

        # Download options
        col1, col2 = st.columns(2)
        
        with col1:
            # Signed URL (nice-to-have)
            url = try_signed_url(metrics_csv)
            if url:
                st.markdown(f"⬇️ [Open {fn} (direct link)]({url})")

        with col2:
            # Guaranteed fallback: stream bytes from server
            try:
                csv_data = download_bytes(metrics_csv)
                if csv_data:
                    st.download_button(
                        f"📥 Download {fn}",
                        data=csv_data,
                        file_name=fn,
                        mime="text/csv",
                        key=f"dl_metrics_csv_{fn}",
                    )
                else:
                    st.error("Could not download CSV file")
            except Exception as e:
                st.error(f"Could not stream `{fn}`: {e}")

    elif metrics_txt:
        fn = os.path.basename(metrics_txt.name)
        # Try to render as key/value table
        try:
            txt_data = download_bytes(metrics_txt)
            if txt_data:
                raw = txt_data.decode("utf-8", errors="ignore")
                rows = []
                for line in raw.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        rows.append({"metric": k.strip(), "value": v.strip()})
                if rows:
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
                else:
                    st.code(raw)
            else:
                st.warning(f"Could not read {fn}")
        except Exception as e:
            st.warning(f"Couldn't preview `{fn}`: {e}")

        # Download options
        col1, col2 = st.columns(2)
        
        with col1:
            # Signed URL (nice-to-have)
            url = try_signed_url(metrics_txt)
            if url:
                st.markdown(f"⬇️ [Open {fn} (direct link)]({url})")

        with col2:
            # Guaranteed fallback
            try:
                txt_data = download_bytes(metrics_txt)
                if txt_data:
                    st.download_button(
                        f"📥 Download {fn}",
                        data=txt_data,
                        file_name=fn,
                        mime="text/plain",
                        key=f"dl_metrics_txt_{fn}",
                    )
                else:
                    st.error("Could not download TXT file")
            except Exception as e:
                st.error(f"Could not stream `{fn}`: {e}")
    else:
        st.info("No allocator metrics found (allocator_metrics.csv/txt).")

def render_allocator_section(blobs, country, stamp):
    """Render allocator plots with improved finding logic"""
    st.subheader("📈 Allocator plot")
    
    alloc_plots = find_allocator_plots(blobs)
    
    if alloc_plots:
        st.success(f"Found {len(alloc_plots)} allocator plot(s)")
        for i, b in enumerate(alloc_plots):
            try:
                st.write(f"**{os.path.basename(b.name)}** ({b.size:,} bytes)")
                
                # Display the plot
                image_data = download_bytes(b)
                if image_data:
                    st.image(image_data, caption=os.path.basename(b.name), use_container_width=True)
                    
                    # Download button
                    st.download_button(
                        f"📥 Download {os.path.basename(b.name)}",
                        data=image_data,
                        file_name=os.path.basename(b.name),
                        mime="image/png",
                        key=f"dl_allocator_{country}_{stamp}_{i}",
                    )
                else:
                    st.error("Could not load image data")
                    
            except Exception as e:
                st.error(f"Error with {os.path.basename(b.name)}: {e}")
    else:
        st.info("No allocator plots found using standard patterns.")
        
        # Debug: show what we're looking for
        with st.expander("🔍 Debug: Show all PNG files"):
            png_files = [b for b in blobs if b.name.lower().endswith('.png')]
            if png_files:
                st.write("Found PNG files:")
                for b in png_files:
                    st.write(f"- `{os.path.basename(b.name)}` ({b.size:,} bytes)")
                    st.write(f"  Full path: `{b.name}`")
                    
                    # Try to display any PNG as a potential allocator plot
                    if st.button(f"Try displaying {os.path.basename(b.name)}", 
                                key=f"try_png_{os.path.basename(b.name)}_{stamp}"):
                        try:
                            image_data = download_bytes(b)
                            if image_data:
                                st.image(image_data, caption=os.path.basename(b.name), use_container_width=True)
                        except Exception as e:
                            st.error(f"Could not display: {e}")
            else:
                st.write("No PNG files found at all.")

def render_onepager_section(blobs, best_id, country, stamp):
    """Render the onepager section with improved file finding"""
    st.subheader("🧾 Onepager")
    
    if best_id:
        op_blob = find_onepager_blob(blobs, best_id)
        if op_blob:
            name = os.path.basename(op_blob.name)
            lower = name.lower()
            
            st.success(f"Found onepager: **{name}** ({op_blob.size:,} bytes)")

            # Preview inline for PNG
            if lower.endswith(".png"):
                try:
                    image_data = download_bytes(op_blob)
                    if image_data:
                        st.image(image_data, caption="Onepager", use_container_width=True)
                    else:
                        st.warning("Image data is empty")
                except Exception as e:
                    st.error(f"Couldn't preview `{name}`: {e}")
            elif lower.endswith(".pdf"):
                st.info("Onepager available as PDF (preview not supported).")

            # Download options
            col1, col2 = st.columns(2)
            
            with col1:
                # Signed URL (if available)
                url = try_signed_url(op_blob)
                if url:
                    st.markdown(f"⬇️ [Open {name} (direct link)]({url})")

            with col2:
                # Stream download (guaranteed to work)
                try:
                    file_data = download_bytes(op_blob)
                    if file_data:
                        mime_type = "image/png" if lower.endswith(".png") else "application/pdf"
                        st.download_button(
                            f"📥 Download {name}",
                            data=file_data,
                            file_name=name,
                            mime=mime_type,
                            key=f"dl_onepager_{country}_{stamp}",
                        )
                    else:
                        st.error("Could not download file - empty data")
                except Exception as e:
                    st.error(f"Download failed: {e}")
        else:
            st.warning(f"No onepager found for best model id '{best_id}' using standard patterns.")
            
            # Look for any large image files that might be onepagers
            potential_onepagers = [b for b in blobs if 
                                 (b.name.lower().endswith('.png') or b.name.lower().endswith('.pdf')) 
                                 and b.size > 50000]  # > 50KB
            
            if potential_onepagers:
                st.info("Found potential onepager files:")
                for b in potential_onepagers:
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.write(f"**{os.path.basename(b.name)}** ({b.size:,} bytes)")
                    with col2:
                        if st.button(f"Try as onepager", 
                                    key=f"try_onepager_{os.path.basename(b.name)}_{stamp}"):
                            if b.name.lower().endswith('.png'):
                                try:
                                    image_data = download_bytes(b)
                                    if image_data:
                                        st.image(image_data, use_container_width=True)
                                        # Add download button
                                        st.download_button(
                                            f"📥 Download {os.path.basename(b.name)}",
                                            data=image_data,
                                            file_name=os.path.basename(b.name),
                                            mime="image/png",
                                            key=f"dl_potential_onepager_{country}_{stamp}_{os.path.basename(b.name)}",
                                        )
                                except Exception as e:
                                    st.error(f"Could not display: {e}")
                            else:
                                st.info("PDF preview not supported, but you can download it.")
            else:
                # Show debug info to help troubleshoot
                with st.expander("🔍 Debug: Show all files"):
                    debug_blob_info(blobs)
    else:
        st.warning("best_model_id.txt not found; cannot locate onepager.")

def render_all_files_section(blobs, bucket_name, country, stamp):
    """Render the all files section with improved download handling"""
    st.subheader("📁 All files")
    
    for b in sorted(blobs, key=lambda x: x.name):
        fn = os.path.basename(b.name)
        
        with st.container(border=True):
            st.write(f"`{fn}` — {b.size:,} bytes")
            
            col1, col2 = st.columns(2)
            
            with col1:
                # Try signed URL first
                url = try_signed_url(b)
                if url:
                    st.markdown(f"[🔗 Open/Download (direct link)]({url})")
            
            with col2:
                # Guaranteed download via stream
                mime = "application/octet-stream"
                if fn.lower().endswith(".csv"): 
                    mime = "text/csv"
                elif fn.lower().endswith(".txt"): 
                    mime = "text/plain"
                elif fn.lower().endswith(".png"): 
                    mime = "image/png"
                elif fn.lower().endswith(".pdf"): 
                    mime = "application/pdf"
                elif fn.lower().endswith(".rds"):
                    mime = "application/octet-stream"
                
                try:
                    file_data = download_bytes(b)
                    if file_data:
                        st.download_button(
                            f"📥 Download {fn}",
                            data=file_data,
                            file_name=fn,
                            mime=mime,
                            key=f"dl_any_{country}_{stamp}_{fn}",
                        )
                    else:
                        st.error("Could not download - empty file")
                except Exception as e:
                    st.error(f"Could not stream `{fn}` from server: {e}")

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
                test_data = download_bytes(test_blobs[0])
                if test_data is not None:
                    st.success("✅ Download OK (storage.objects.get works).")
                else:
                    st.error("❌ Download failed - got empty data")
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
        f"If you're on Cloud Run, make sure the service account has at least roles/storage.objectViewer."
    )
    st.stop()

# ---------- Determine current (latest overall) run ----------
current_key = latest_run_key(runs)
if not current_key:
    st.warning("No runs found.")
    st.stop()

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

# Main renderer to avoid copy/paste and ensure unique keys
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

    # Show best model ID if available
    if best_id:
        st.info(f"**Best Model ID:** {best_id}")

    # Render each section
    render_metrics_section(blobs)
    render_allocator_section(blobs, country, stamp)
    render_onepager_section(blobs, best_id, country, stamp)
    render_all_files_section(blobs, bucket_name, country, stamp)

# ---- Render each selected country's latest run for this revision ----
st.markdown(f"## Detailed View — revision `{rev}`")
for c in countries_sel:
    with st.container():
        render_run_for_country(bucket_name, rev, c)
        st.divider()