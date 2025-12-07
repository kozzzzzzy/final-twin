#!/usr/bin/env bash
set -e

echo "=========================================="
echo "TwinSync Spot v1.0.3 - Starting..."
echo "=========================================="

# Debug: show if token is available
if [ -n "$SUPERVISOR_TOKEN" ]; then
    echo "Supervisor token: available (${#SUPERVISOR_TOKEN} chars)"
else
    echo "Running in standalone mode (no SUPERVISOR_TOKEN)"
fi

# Set data directory
export DATA_DIR="/data"

# Get ingress path from supervisor if available
if [ -n "$SUPERVISOR_TOKEN" ]; then
    INGRESS_INFO=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" http://supervisor/addons/self/info 2>/dev/null || echo "{}")
    INGRESS_ENTRY=$(echo "$INGRESS_INFO" | python3 -c "import sys, json; print(json.load(sys.stdin).get('data', {}).get('ingress_entry', ''))" 2>/dev/null || echo "")
    if [ -n "$INGRESS_ENTRY" ]; then
        export INGRESS_PATH="$INGRESS_ENTRY"
        echo "Ingress path: $INGRESS_PATH"
    fi
fi

echo "Starting FastAPI server on port 8099..."
echo "=========================================="

# Run the FastAPI app with full environment
cd /app
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8099
