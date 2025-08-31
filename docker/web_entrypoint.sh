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

# Start Streamlit application
echo "Starting Streamlit on port ${PORT}..."
exec python3 -m streamlit run streamlit_app.py \
    --server.address=0.0.0.0 \
    --server.port=${PORT} \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
