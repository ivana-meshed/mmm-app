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