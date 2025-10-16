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
if [ ! -f "streamlit_app.py" ]; then
    echo "ERROR: streamlit_app.py not found"
    ls -la /app/
    exit 1
fi

# --- Build Streamlit secrets.toml for OIDC auth ---
mkdir -p /app/.streamlit

get_secret_payload () {
  local resource="$1"
  if [[ "$resource" == projects/*/secrets/*/versions/* ]]; then
    # Requires roles/secretmanager.secretAccessor on the Cloud Run SA
    gcloud secrets versions access "$resource" || true
  else
    # Treat as raw value
    echo -n "$resource"
  fi
}

AUTH_CLIENT_ID="$(get_secret_payload "${AUTH_CLIENT_ID:-$AUTH_CLIENT_ID}")"
AUTH_CLIENT_SECRET="$(get_secret_payload "${AUTH_CLIENT_SECRET:-$AUTH_CLIENT_SECRET}")"
AUTH_COOKIE_SECRET="$(get_secret_payload "${AUTH_COOKIE_SECRET:-$AUTH_COOKIE_SECRET}")"

# Fail fast if anything critical is empty
if [[ -z "$AUTH_CLIENT_ID" || -z "$AUTH_CLIENT_SECRET" || -z "$AUTH_COOKIE_SECRET" || -z "$AUTH_REDIRECT_URI" ]]; then
  echo "ERROR: Missing one of AUTH_CLIENT_ID / AUTH_CLIENT_SECRET / AUTH_COOKIE_SECRET / AUTH_REDIRECT_URI"
  echo "      Make sure Terraform set the env vars and the service account can read the secrets."
  exit 1
fi

OIDC_META_URL="https://accounts.google.com/.well-known/openid-configuration"

cat > /app/.streamlit/secrets.toml <<EOF
[auth]
redirect_uri = "${AUTH_REDIRECT_URI}"
cookie_secret = "${AUTH_COOKIE_SECRET}"
client_id = "${AUTH_CLIENT_ID}"
client_secret = "${AUTH_CLIENT_SECRET}"
server_metadata_url = "${OIDC_META_URL}"
# Optional hint to Google; *not* a security boundary
client_kwargs = { hd = "mesheddata.com" }
EOF

echo "Wrote /app/.streamlit/secrets.toml with [auth] config"

# Start Streamlit application
echo "Starting Streamlit on port ${PORT}..."
exec python3 -m streamlit run streamlit_app.py \
    --server.address=0.0.0.0 \
    --server.port=${PORT} \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
