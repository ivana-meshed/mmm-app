#!/bin/bash
set -e

echo "🚀 Starting MMM Trainer container..."

# Check if this is a health check request
if [ "$1" = "health" ]; then
    echo "Running health check..."
    python3 /app/health_api.py
    exit 0
fi

echo "🔥 Warming container..."
if [ -f "/app/warm_container.py" ]; then
    python3 /app/warm_container.py &
    WARMUP_PID=$!
else
    echo "⚠️ Warming script not found, skipping..."
    WARMUP_PID=""
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

# Default Google OIDC discovery URL
OIDC_META_URL="https://accounts.google.com/.well-known/openid-configuration"

cat >/app/.streamlit/secrets.toml <<EOF
[auth]
redirect_uri = "${AUTH_REDIRECT_URI}"
cookie_secret = "${AUTH_COOKIE_SECRET}"
client_id = "${AUTH_CLIENT_ID}"
client_secret = "${AUTH_CLIENT_SECRET}"
server_metadata_url = "${OIDC_META_URL}"
# Hint Google for a hosted domain. Not a security boundary — we'll check in code too.
client_kwargs = { hd = "mesheddata.com" }
EOF
# --- end secrets.toml creation ---


# Start the main application
echo "🌐 Starting Streamlit application..."
test -f streamlit_app.py || { echo 'Missing /app/streamlit_app.py'; ls -la; exit 1; }

# Start streamlit in background
python3 -m streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=${PORT} &
STREAMLIT_PID=$!

# Wait for warmup to complete (with timeout)
if wait $WARMUP_PID; then
    echo "✅ Container warming completed successfully"
else
    echo "⚠️ Container warming completed with issues"
fi

# Wait for streamlit to exit
wait $STREAMLIT_PID
