#!/bin/bash
set -e

echo "Starting MMM Web Service..."

# Health check endpoint
if [ "$1" = "health" ]; then
    echo "Running health check..."
    python3 -c "
from health import health_checker
import json
status = health_checker.check_container_health()
print(json.dumps(status))
"
    exit 0
fi

# Verify environment
echo "Web Service Environment:"
echo "- Python version: $(python3 --version)"
echo "- PORT: ${PORT:-8080}"
echo "- PROJECT_ID: ${PROJECT_ID:-not set}"
echo "- REGION: ${REGION:-not set}"
echo "- TRAINING_JOB_NAME: ${TRAINING_JOB_NAME:-not set}"

# Verify Python dependencies
echo "Verifying dependencies..."
python3 -c "
try:
    import streamlit, pandas, google.cloud.storage, google.cloud.run_v2
    print('All dependencies verified')
except ImportError as e:
    print(f'Missing dependency: {e}')
    exit(1)
"

# Check required files
if [ ! -f "0_Connect_Your_Data.py" ]; then
    echo "ERROR: 0_Connect_Your_Data.py not found"
    ls -la /app/
    exit 1
fi

# --- Build Streamlit secrets.toml for OIDC auth ---
py_get_secret() {
  python3 - "$1" <<'PY'
import os, sys, base64
path = sys.argv[1]
if path.startswith("projects/") and "/secrets/" in path and "/versions/" in path:
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        payload = client.access_secret_version(name=path).payload.data
        sys.stdout.write(payload.decode("utf-8"))
    except Exception as e:
        # Print nothing; caller can decide if empty is acceptable
        pass
else:
    sys.stdout.write(path)
PY
}

# Prefer *_SECRET first (resource path), then raw fallback
AUTH_CLIENT_ID="$(py_get_secret "${AUTH_CLIENT_ID:-${AUTH_CLIENT_ID:-}}")"
AUTH_CLIENT_SECRET="$(py_get_secret "${AUTH_CLIENT_SECRET:-${AUTH_CLIENT_SECRET:-}}")"
AUTH_COOKIE_SECRET="$(py_get_secret "${AUTH_COOKIE_SECRET:-${AUTH_COOKIE_SECRET:-}}")"
AUTH_REDIRECT_URI="${AUTH_REDIRECT_URI:-}"

# --- Build Streamlit secrets.toml for OIDC auth ---
mkdir -p /app/.streamlit

# Fail fast if anything critical is empty
if [[ -z "$AUTH_CLIENT_ID" || -z "$AUTH_CLIENT_SECRET" || -z "$AUTH_COOKIE_SECRET" || -z "$AUTH_REDIRECT_URI" ]]; then
  echo "ERROR: Missing one of AUTH_CLIENT_ID / AUTH_CLIENT_SECRET / AUTH_COOKIE_SECRET / AUTH_REDIRECT_URI"
  exit 1
fi

cat > /app/.streamlit/secrets.toml <<EOF
[auth]
redirect_uri = "${AUTH_REDIRECT_URI}"
cookie_secret = "${AUTH_COOKIE_SECRET}"
client_id = "${AUTH_CLIENT_ID}"
client_secret = "${AUTH_CLIENT_SECRET}"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
providers = ["google"]
EOF

python3 - <<'PY'
import tomllib, sys, os
p = "/app/.streamlit/secrets.toml"
with open(p,"rb") as f:
    s = tomllib.load(f)
auth = s.get("auth", {})
required = ["redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url"]
missing = [k for k in required if not auth.get(k)]
print("Auth keys present:", sorted(k for k in auth.keys()))
if missing:
    print("MISSING keys in [auth]:", missing, file=sys.stderr)
    sys.exit(1)
PY

echo "✅ Wrote /app/.streamlit/secrets.toml"

# Optional: show a redacted preview in logs for debugging
sed -n '1,80p' /app/.streamlit/secrets.toml | sed -E 's/(client_secret *= *").*/\1***"/; s/(cookie_secret *= *").*/\1***"/'

# Start Streamlit application
echo "Starting Streamlit on port ${PORT}..."
exec python3 -m streamlit run 0_Connect_Your_Data.py \
    --server.address=0.0.0.0 \
    --server.port=${PORT} \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
