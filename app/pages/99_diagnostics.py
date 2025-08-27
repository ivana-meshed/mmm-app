# pages/99_Diagnostics.py
import os, sys, platform, json
import streamlit as st
from importlib.metadata import version, PackageNotFoundError
import subprocess

st.title("📦 Runtime diagnostics")

pkgs = [
    "google-cloud-storage",
    "google-auth",
    "google-cloud-iam-credentials",
    "streamlit",
    "pandas",
    "requests",
    "urllib3",
    "grpcio",
    "protobuf",
]

rows = []
for p in pkgs:
    try:
        rows.append({"package": p, "version": version(p)})
    except PackageNotFoundError:
        rows.append({"package": p, "version": "NOT INSTALLED"})

st.subheader("Selected packages")
st.table(rows)

# Optional: full pip freeze (filtered) — handy to copy into logs/UI
try:
    out = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                         capture_output=True, text=True, check=True).stdout
    filt = "\n".join([l for l in out.splitlines()
                      if l.lower().startswith(("google-", "grpcio", "protobuf"))])
    st.subheader("pip freeze (google/* + grpcio/protobuf)")
    st.code(filt or "(none)")
except Exception as e:
    st.caption(f"pip freeze unavailable: {e}")

st.subheader("Environment")
st.json({
    "python": platform.python_version(),
    "platform": platform.platform(),
    "K_SERVICE": os.getenv("K_SERVICE"),
    "RUN_SERVICE_ACCOUNT_EMAIL": os.getenv("RUN_SERVICE_ACCOUNT_EMAIL"),
    "GCS_BUCKET": os.getenv("GCS_BUCKET"),
})

# Also print to logs
print("DIAG_VERSIONS", json.dumps(rows))
