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

# Fetch secret payloads via Secret Manager only if env holds resource names.
# If you inject raw values as envs instead, just write them directly.

get_secret_payload () {
  local resource="$1"
  if [[ "$resource" == projects/*/secrets/*/versions/* ]]; then
    # Requires the Cloud Run SA to have roles/secretmanager.secretAccessor
    gcloud secrets versions access "$resource" || true
  else
    # Treat as raw value
    echo -n "$resource"
  fi
}

AUTH_CLIENT_ID="$(get_secret_payload "${AUTH_CLIENT_ID_SECRET:-$AUTH_CLIENT_ID}")"
AUTH_CLIENT_SECRET="$(get_secret_payload "${AUTH_CLIENT_SECRET_SECRET:-$AUTH_CLIENT_SECRET}")"
AUTH_COOKIE_SECRET="$(get_secret_payload "${AUTH_COOKIE_SECRET_SECRET:-$AUTH_COOKIE_SECRET}")"

# Start Streamlit application
echo "Starting Streamlit on port ${PORT}..."
exec python3 -m streamlit run streamlit_app.py \
    --server.address=0.0.0.0 \
    --server.port=${PORT} \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
