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

import datetime
from google.cloud import storage
client = storage.Client()
bucket = client.bucket(os.getenv("GCS_BUCKET"))
test = bucket.blob("diagnostics/healthcheck.txt")
test.upload_from_string("ok")
url = signed_url_or_none(test, minutes=10)
st.write("Signed URL:", url)
