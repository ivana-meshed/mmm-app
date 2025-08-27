# app/pages/1_Results.py
import os, io, re, datetime, hashlib, base64
import pandas as pd
import streamlit as st
from google.cloud import storage
from urllib.parse import quote
from uuid import uuid4
from google.auth import default as google_auth_default
from google.auth.transport.requests import Request
from google.auth.iam import Signer as IAMSigner


st.set_page_config(page_title="Results: Robyn MMM", layout="wide")
st.title("Results browser (GCS)")

# ---------- Settings / Auth ----------
DEFAULT_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
DEFAULT_PREFIX = "robyn/"

@st.cache_resource
def gcs_client():
    return storage.Client()

client = gcs_client()

# ---------- Helpers ----------

IS_CLOUDRUN = bool(os.getenv("K_SERVICE"))

def _sa_email_from_creds(creds):
    return (
        os.getenv("RUN_SERVICE_ACCOUNT_EMAIL")
        or getattr(creds, "service_account_email", None)
    )

def signed_url_or_none(blob, minutes=60):
    try:
        # Use ADC + IAM Signer (works on Cloud Run without a private key)
        creds, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        sa_email = _sa_email_from_creds(creds)
        if not sa_email:
            raise RuntimeError("Could not determine service account email for signing")

        signer = IAMSigner(Request(), creds, sa_email)

        return blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=minutes),
            method="GET",
            signer=signer,                   # <— key bit
            service_account_email=sa_email,  # helps gcs lib set the signer ID
        )
    except Exception as e:
        st.caption(f"Signed URL error: {e}")
        return None
'''
def signed_url_or_none(blob, minutes=60):
    import google.auth
    from google.auth.transport.requests import Request
    from google.cloud import iam_credentials_v1
    try:
        # Fast path: many recent storage libs can use ADC to IAM-sign implicitly
        return blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=minutes),
            method="GET",
            response_disposition=f'attachment; filename="{os.path.basename(blob.name)}"',
            credentials=client._credentials,   # ← use the same ADC creds as the Storage client
        )
    except Exception as e1:
        # Fallback: wire IAM Credentials explicitly
        try:
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])

            # Figure out the service account email
            sa_email = os.getenv("RUN_SERVICE_ACCOUNT_EMAIL")
            if not sa_email and IS_CLOUDRUN:
                try:
                    # Query metadata for the default SA email
                    from google.auth.compute_engine import _metadata
                    sa_email = _metadata.get("instance/service-accounts/default/email")
                except Exception:
                    pass
            if not sa_email:
                raise RuntimeError("service account email unknown for signing")

            iam_client = iam_credentials_v1.IAMCredentialsClient()
            return blob.generate_signed_url(
                version="v4",
                expiration=datetime.timedelta(minutes=minutes),
                method="GET",
                response_disposition=f'attachment; filename="{os.path.basename(blob.name)}"',
                service_account_email=sa_email,
                credentials=creds,
                iam_client=iam_client,
            )
        except Exception as e2:
            if IS_CLOUDRUN:
                st.caption(f"⚠️ Signed URL failed: {e2!s}")
            return None
'''



def gcs_console_url(bucket: str, prefix: str) -> str:
    # keep "/" unencoded so the Console URL resolves correctly
    return f"https://console.cloud.google.com/storage/browser/{bucket}/{quote(prefix, safe='/')}"

def blob_key(prefix: str, name: str, suffix: str = "") -> str:
    # Create unique keys by combining prefix, file hash, and optional suffix
    h = hashlib.sha1((name + suffix).encode("utf-8")).hexdigest()[:10]
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

def download_bytes_safe(blob):
    """Download blob with comprehensive error handling"""
    try:
        data = blob.download_as_bytes()
        if len(data) == 0:
            st.warning(f"Downloaded file is empty: {blob.name}")
            return None
        return data
    except Exception as e:
        st.error(f"Download failed for {blob.name}: {e}")
        return None
# --- helpers to add/replace ---

DATA_URI_MAX_BYTES = int(os.getenv("DATA_URI_MAX_BYTES", str(8 * 1024 * 1024)))  # 8 MB default

def gcs_object_details_url(blob) -> str:
    # _details view; keep slashes in the object path
    return f"https://console.cloud.google.com/storage/browser/_details/{blob.bucket.name}/{quote(blob.name, safe='/')}"

def signed_url_or_none(blob, minutes=60):
    """Return a V4 signed URL or None; also sets a friendly download filename."""
    try:
        return blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=minutes),
            method="GET",
            # Force browser download with a clean filename
            response_disposition=f'attachment; filename="{os.path.basename(blob.name)}"',
        )
    except Exception as e:
        # Tiny, non-invasive hint so you can diagnose in Cloud Run
        if IS_CLOUDRUN:
            st.caption(f"⚠️ Signed URL failed: {e!s}")
        return None

def download_link_for_blob(blob, label=None, mime_hint=None, minutes=60, key_suffix: str | None = None):
    """
    1) Try signed URL (best on Cloud Run) via <a download>.
    2) Else use a data: URI <a download> (no Streamlit media store).
    3) If too large for data URI, show GCS Console object link.
    """
    name = os.path.basename(blob.name)
    label = label or f"⬇️ Download {name}"
    mime = mime_hint or "application/octet-stream"

    # 1) Signed URL
    url = signed_url_or_none(blob, minutes)
    if url:
        st.markdown(f'<a href="{url}" download="{name}">{label}</a>', unsafe_allow_html=True)
        return

    # 2) Data-URI fallback (no /media, survives reruns)
    data = download_bytes_safe(blob)
    if data is None:
        st.warning(f"Couldn't fetch {name} for download.")
        return

    if len(data) <= DATA_URI_MAX_BYTES:
        b64 = base64.b64encode(data).decode()
        href = f"data:{mime};base64,{b64}"
        st.markdown(f'<a href="{href}" download="{name}">{label}</a>', unsafe_allow_html=True)
        return

    # 3) Too big for data URI: guide user to Console (and hint about signing)
    st.info(
        "File is large and a signed URL couldn’t be created. "
        "Open it directly in the GCS Console (or enable URL signing for your Cloud Run service account)."
    )
    st.markdown(f'[Open **{name}** in GCS Console]({gcs_object_details_url(blob)})')


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
            "size_mb": round(b.size / 1024 / 1024, 2) if b.size > 0 else 0,
            "is_png": b.name.lower().endswith('.png'),
            "is_pdf": b.name.lower().endswith('.pdf'),
        })
    
    # Sort by size (largest first)
    file_info.sort(key=lambda x: x["size_bytes"], reverse=True)
    
    for info in file_info:
        st.write(f"- `{info['basename']}` ({info['size_mb']} MB)")

def render_metrics_section(blobs, country, stamp):
    """Render the allocator metrics section"""
    st.subheader("📊 Allocator metrics")
    
    metrics_csv = find_blob(blobs, "/allocator_metrics.csv")
    metrics_txt = find_blob(blobs, "/allocator_metrics.txt")
    
    if metrics_csv:
        fn = os.path.basename(metrics_csv.name)
        # Preview table WITHOUT using st.dataframe (which has built-in download buttons)
        try:
            csv_data = download_bytes_safe(metrics_csv)
            if csv_data:
                df = pd.read_csv(io.BytesIO(csv_data))
                
                # Display as HTML table instead of st.dataframe to avoid built-in downloads
                st.markdown("**Metrics Preview:**")
                st.markdown(df.to_html(escape=False, index=False), unsafe_allow_html=True)
                
                # Our own download button
                download_link_for_blob(metrics_csv, label=f"📥 Download {fn}", mime_hint="text/csv", key_suffix=f"metrics_csv|{country}|{stamp}")

            else:
                st.warning(f"Could not read {fn}")
        except Exception as e:
            st.warning(f"Couldn't preview `{fn}` as a table: {e}")

    elif metrics_txt:
        fn = os.path.basename(metrics_txt.name)
        # Try to render as key/value table
        try:
            txt_data = download_bytes_safe(metrics_txt)
            if txt_data:
                raw = txt_data.decode("utf-8", errors="ignore")
                
                # Parse and display as HTML table instead of st.dataframe
                rows = []
                for line in raw.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        rows.append({"metric": k.strip(), "value": v.strip()})
                
                if rows:
                    df = pd.DataFrame(rows)
                    st.markdown("**Metrics:**")
                    st.markdown(df.to_html(escape=False, index=False), unsafe_allow_html=True)
                else:
                    st.code(raw)
                
                # Download button
                download_link_for_blob(metrics_txt, label=f"📥 Download {fn}", mime_hint="text/plain", key_suffix=f"metrics_txt|{country}|{stamp}")
                
            else:
                st.warning(f"Could not read {fn}")
        except Exception as e:
            st.warning(f"Couldn't preview `{fn}`: {e}")
    else:
        st.info("No allocator metrics found (allocator_metrics.csv/txt).")

def render_allocator_section(blobs, country, stamp):
    """Render allocator plots with base64 display"""
    st.subheader("Allocator plot")
    
    alloc_plots = find_allocator_plots(blobs)
    
    if alloc_plots:
        st.success(f"Found {len(alloc_plots)} allocator plot(s)")
        for i, b in enumerate(alloc_plots):
            try:
                fn = os.path.basename(b.name)
                st.write(f"**{fn}** ({b.size:,} bytes)")
                
                # Display using base64 HTML to avoid Streamlit media cache
                image_data = download_bytes_safe(b)
                if image_data:
                    b64 = base64.b64encode(image_data).decode()
                    st.markdown(
                        f'<img src="data:image/png;base64,{b64}" style="width: 100%; height: auto;" alt="{fn}">',
                        unsafe_allow_html=True
                    )
                    
                    # Download button
                    download_link_for_blob(b, label=f"Download {fn}", mime_hint="image/png", key_suffix=f"alloc|{country}|{stamp}|{i}")

                else:
                    st.error("Could not load image data")
                    
            except Exception as e:
                st.error(f"Error with {os.path.basename(b.name)}: {e}")
    else:
        st.info("No allocator plots found using standard patterns.")
        
        # Debug: show what PNG files exist
        with st.expander("Show all PNG files"):
            png_files = [b for b in blobs if b.name.lower().endswith('.png')]
            if png_files:
                st.write("Found PNG files:")
                for i, b in enumerate(png_files):
                    fn = os.path.basename(b.name)
                    st.write(f"- `{fn}` ({b.size:,} bytes)")
                    
                    # Try button to display
                    if st.button(f"Display {fn}", 
                                key=blob_key("try_png", f"{country}_{stamp}_{i}_{fn}")):
                        try:
                            image_data = download_bytes_safe(b)
                            if image_data:
                                b64 = base64.b64encode(image_data).decode()
                                st.markdown(
                                    f'<img src="data:image/png;base64,{b64}" style="width: 100%; height: auto;" alt="{fn}">',
                                    unsafe_allow_html=True
                                )
                        except Exception as e:
                            st.error(f"Could not display: {e}")
            else:
                st.write("No PNG files found at all.")

def render_onepager_section(blobs, best_id, country, stamp):
    """Render the onepager section with base64 display"""
    st.subheader("Onepager")
    
    if best_id:
        op_blob = find_onepager_blob(blobs, best_id)
        if op_blob:
            name = os.path.basename(op_blob.name)
            lower = name.lower()
            
            st.success(f"Found onepager: **{name}** ({op_blob.size:,} bytes)")

            # Display using base64 HTML
            if lower.endswith(".png"):
                try:
                    image_data = download_bytes_safe(op_blob)
                    if image_data:
                        b64 = base64.b64encode(image_data).decode()
                        st.markdown(
                            f'<img src="data:image/png;base64,{b64}" style="width: 100%; height: auto;" alt="Onepager">',
                            unsafe_allow_html=True
                        )
                        
                        # Download button
                        download_link_for_blob(op_blob, label=f"Download {name}", mime_hint="image/png", key_suffix=f"onepager|{country}|{stamp}")

                    else:
                        st.warning("Image data is empty")
                except Exception as e:
                    st.error(f"Couldn't preview `{name}`: {e}")
            elif lower.endswith(".pdf"):
                st.info("Onepager available as PDF (preview not supported).")
                
                # Download button for PDF
                try:
                    pdf_data = download_bytes_safe(op_blob)
                    if pdf_data:
                        download_link_for_blob(op_blob, label=f"Download {name}", mime_hint="application/pdf", key_suffix=f"onepager|{country}|{stamp}")

                except Exception as e:
                    st.error(f"PDF download failed: {e}")
        else:
            st.warning(f"No onepager found for best model id '{best_id}' using standard patterns.")
            
            '''# Look for potential onepagers
            potential_onepagers = [b for b in blobs if 
                                 (b.name.lower().endswith('.png') or b.name.lower().endswith('.pdf')) 
                                 and b.size > 50000]  # > 50KB
            
            if potential_onepagers:
                st.info("Found potential onepager files:")
                for i, b in enumerate(potential_onepagers):
                    fn = os.path.basename(b.name)
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.write(f"**{fn}** ({b.size:,} bytes)")
                    with col2:
                        if st.button(f"Display", 
                                    key=blob_key("try_onepager", f"{country}_{stamp}_{i}_{fn}")):
                            if b.name.lower().endswith('.png'):
                                try:
                                    image_data = download_bytes_safe(b)
                                    if image_data:
                                        b64 = base64.b64encode(image_data).decode()
                                        st.markdown(
                                            f'<img src="data:image/png;base64,{b64}" style="width: 100%; height: auto;" alt="{fn}">',
                                            unsafe_allow_html=True
                                        )
                                        # Add download button
                                        download_link_for_blob(op_blob, label=f"Download {fn}", mime_hint="image/png")

                                except Exception as e:
                                    st.error(f"Could not display: {e}")
                            else:
                                st.info("PDF preview not supported, but you can download it.")
            else:
                # Show debug info
                with st.expander("Show all files"):
                    debug_blob_info(blobs)'''
    else:
        st.warning("best_model_id.txt not found; cannot locate onepager.")

def render_all_files_section(blobs, bucket_name, country, stamp):
    """Render the all files section using signed URLs (fallback to streaming)."""
    st.subheader("All files")

    def guess_mime(name: str) -> str:
        n = name.lower()
        if n.endswith(".csv"): return "text/csv"
        if n.endswith(".txt"): return "text/plain"
        if n.endswith(".png"): return "image/png"
        if n.endswith(".pdf"): return "application/pdf"
        if n.endswith(".log"): return "text/plain"
        if n.endswith(".rds"): return "application/octet-stream"
        return "application/octet-stream"

    for i, b in enumerate(sorted(blobs, key=lambda x: x.name)):
        fn = os.path.basename(b.name)
        with st.container(border=True):
            st.write(f"`{fn}` — {b.size:,} bytes")
            download_link_for_blob(
                b,
                label=f"Download {fn}",
                mime_hint=guess_mime(fn),
                key_suffix=f"all|{country}|{stamp}|{i}"
            )



def render_all_files_section_old(blobs, bucket_name, country, stamp):
    """Render the all files section with improved download handling"""
    st.subheader("All files")
    
    # Group files by type for better organization
    files_by_type = {
        "Images (PNG/PDF)": [],
        "Data (CSV/TXT)": [],
        "Models (RDS)": [],
        "Logs": [],
        "Other": []
    }
    
    for b in sorted(blobs, key=lambda x: x.name):
        fn = os.path.basename(b.name)
        fn_lower = fn.lower()
        
        if fn_lower.endswith(('.png', '.pdf')):
            files_by_type["Images (PNG/PDF)"].append(b)
        elif fn_lower.endswith(('.csv', '.txt')):
            files_by_type["Data (CSV/TXT)"].append(b)
        elif fn_lower.endswith('.rds'):
            files_by_type["Models (RDS)"].append(b)
        elif fn_lower.endswith('.log'):
            files_by_type["Logs"].append(b)
        else:
            files_by_type["Other"].append(b)
    
    for category, file_list in files_by_type.items():
        if not file_list:
            continue
            
        st.write(f"**{category}**")
        
        for i, b in enumerate(file_list):
            fn = os.path.basename(b.name)
            
            with st.container(border=True):
                col1, col2 = st.columns([3, 1])
                
                with col1:
                    st.write(f"`{fn}` — {b.size:,} bytes")
                
                with col2:
                    # Only provide downloads for files under 50MB to avoid memory issues
                    if b.size < 50 * 1024 * 1024:  # 50MB limit
                        # Determine MIME type
                        mime = "application/octet-stream"
                        if fn.lower().endswith(".csv"): 
                            mime = "text/csv"
                        elif fn.lower().endswith(".txt"): 
                            mime = "text/plain"
                        elif fn.lower().endswith(".png"): 
                            mime = "image/png"
                        elif fn.lower().endswith(".pdf"): 
                            mime = "application/pdf"
                        elif fn.lower().endswith(".log"):
                            mime = "text/plain"
                        
                        try:
                            # Use a more robust download approach
                            if st.button(f"Download", key=f"download_{country}_{stamp}_{category}_{i}_{fn}"):
                                file_data = download_bytes_safe(b)
                                if file_data:
                                    # Force download via session state to avoid media system
                                    st.session_state[f"download_ready_{fn}"] = {
                                        "data": file_data,
                                        "filename": fn,
                                        "mime": mime
                                    }
                                    st.success(f"File {fn} ready for download")
                                    st.rerun()
                                else:
                                    st.error("Could not prepare file for download")
                            
                            # Check if file is ready for download
                            download_key = f"download_ready_{fn}"
                            if download_key in st.session_state:
                                download_info = st.session_state[download_key]
                                st.download_button(
                                    f"Save {fn}",
                                    data=download_info["data"],
                                    file_name=download_info["filename"],
                                    mime=download_info["mime"],
                                    key=f"save_{country}_{stamp}_{category}_{i}_{fn}",
                                )
                                # Clean up session state after download
                                if st.button(f"Clear", key=f"clear_{country}_{stamp}_{category}_{i}_{fn}"):
                                    del st.session_state[download_key]
                                    st.rerun()
                        
                        except Exception as e:
                            st.error(f"Download error: {e}")
                    else:
                        st.info(f"File too large ({b.size:,} bytes) - download via GCS console")
                        # Provide GCS console link for large files
                        gcs_url = f"https://console.cloud.google.com/storage/browser/_details/{bucket_name}/{b.name}"
                        st.markdown(f"[View in GCS Console]({gcs_url})")

# ---------- Sidebar (filters + refresh) ----------
with st.sidebar:
    bucket_name = st.text_input("GCS bucket", value=DEFAULT_BUCKET)
    prefix = st.text_input("Root prefix", value=DEFAULT_PREFIX, help="Usually 'robyn/' or narrower like 'robyn/r100/'")

    # Make sure prefix ends with a slash (GCS treats prefixes literally)
    if prefix and not prefix.endswith("/"):
        prefix = prefix + "/"

    do_scan = st.button("🔄 Refresh listing")

    # Storage self-test
    if st.button("Run storage self-test"):
        try:
            test_blobs = list_blobs(bucket_name, prefix)
            st.write(f"Found {len(test_blobs)} blobs under gs://{bucket_name}/{prefix}")
            if test_blobs:
                # Try an actual download to verify storage.objects.get
                test_data = download_bytes_safe(test_blobs[0])
                if test_data is not None:
                    st.success("✅ Download OK (storage.objects.get works).")
                else:
                    st.error("❌ Download failed - got empty data")
            elif len(test_blobs) == 0:
                st.info("Listing returned 0 objects. If you expect data here, check the bucket/prefix and IAM.")
        except Exception as e:
            st.error(f"Test failed: {e}")

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

    # Render each section with unique context
    render_metrics_section(blobs, country, stamp)
    render_allocator_section(blobs, country, stamp)
    render_onepager_section(blobs, best_id, country, stamp)
    render_all_files_section(blobs, bucket_name, country, stamp)

# ---- Render each selected country's latest run for this revision ----
st.markdown(f"## Detailed View — revision `{rev}`")
for c in countries_sel:
    with st.container():
        render_run_for_country(bucket_name, rev, c)
        st.divider()