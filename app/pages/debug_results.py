# debug_results.py - minimal version to isolate the media file issue
import os, io, re, datetime, hashlib
import pandas as pd
import streamlit as st
from google.cloud import storage
from urllib.parse import quote

st.set_page_config(page_title="DEBUG Results", layout="wide")
st.title("🔍 DEBUG Results browser")

# ---------- Settings / Auth ----------
DEFAULT_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
DEFAULT_PREFIX = "robyn/"

@st.cache_resource
def gcs_client():
    return storage.Client()

client = gcs_client()

def list_blobs(bucket_name: str, prefix: str):
    try:
        bucket = client.bucket(bucket_name)
        return list(client.list_blobs(bucket_or_name=bucket, prefix=prefix))
    except Exception as e:
        st.error(f"Failed to list gs://{bucket_name}/{prefix} — {e}")
        return []

def download_bytes_safe(blob):
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

def parse_path(name: str):
    parts = name.split("/")
    if len(parts) >= 5 and parts[0] == "robyn":
        return {"rev": parts[1], "country": parts[2], "stamp": parts[3], "file": "/".join(parts[4:])}
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

# Get inputs
bucket_name = st.text_input("GCS bucket", value=DEFAULT_BUCKET)
prefix = st.text_input("Root prefix", value=DEFAULT_PREFIX)
if prefix and not prefix.endswith("/"):
    prefix = prefix + "/"

if st.button("🔄 List files"):
    blobs = list_blobs(bucket_name, prefix)
    runs = group_runs(blobs)
    
    if not runs:
        st.info("No runs found")
        st.stop()
    
    st.write(f"Found {len(runs)} runs")
    
    # Pick the first run for testing
    first_run = list(runs.keys())[0]
    rev, country, stamp = first_run
    blobs = runs[first_run]
    
    st.write(f"Testing with run: {rev}/{country}/{stamp}")
    st.write(f"Files in run: {len(blobs)}")
    
    # List all files (NO st.dataframe)
    st.subheader("Files (as text)")
    for b in blobs:
        st.write(f"- {os.path.basename(b.name)} ({b.size:,} bytes)")
    
    # Find PNG files and display with base64 only
    png_files = [b for b in blobs if b.name.lower().endswith('.png')]
    
    if png_files:
        st.subheader("PNG Files (base64 display)")
        for i, b in enumerate(png_files):
            fn = os.path.basename(b.name)
            st.write(f"**{fn}** ({b.size:,} bytes)")
            
            try:
                image_data = download_bytes_safe(b)
                if image_data:
                    import base64
                    b64 = base64.b64encode(image_data).decode()
                    st.markdown(
                        f'<img src="data:image/png;base64,{b64}" style="max-width: 500px; height: auto;" alt="{fn}">',
                        unsafe_allow_html=True
                    )
                    
                    # Simple download button
                    st.download_button(
                        f"Download {fn}",
                        data=image_data,
                        file_name=fn,
                        mime="image/png",
                        key=f"debug_dl_{i}_{fn}",
                    )
                else:
                    st.error("Could not load image data")
            except Exception as e:
                st.error(f"Error with {fn}: {e}")
    else:
        st.info("No PNG files found")

st.write("**Debug Status:** This page uses NO st.dataframe, NO st.image, only base64 HTML images and basic st.download_button")