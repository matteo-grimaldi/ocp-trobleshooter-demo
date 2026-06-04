#!/usr/bin/env bash
# start.sh — Start Prometheus MCP server and Gradio UI
set -e

echo "Starting Prometheus MCP server on port ${PROMETHEUS_MCP_PORT:-8765}..."
python prometheus_mcp.py &
PROM_PID=$!

echo "Waiting for OGX server to be ready at ${OGX_BASE_URL:-http://localhost:8321}..."
OGX_URL="${OGX_BASE_URL:-http://localhost:8321}"
for i in $(seq 1 30); do
    if curl -sf "${OGX_URL}/v1/models" -o /dev/null 2>/dev/null; then
        echo "OGX server is ready."
        break
    fi
    echo "  Attempt ${i}/30 — waiting 5s..."
    sleep 5
done

echo "Starting Gradio UI on port ${GRADIO_PORT:-7860}..."
exec python app.py
