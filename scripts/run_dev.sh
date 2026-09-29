#!/usr/bin/env bash
set -euo pipefail

# Determine repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "=== Jarvis V1 — Launching Development Server ==="

# Activate virtualenv if present
if [ -d ".venv" ]; then
    echo "Activating virtual environment (.venv)..."
    source .venv/bin/activate
fi

# Load .env if present
if [ -f ".env" ]; then
    echo "Loading environment from .env..."
    export $(grep -v '^#' .env | xargs)
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"

# Security assertion: ensure loopback binding only
if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
    echo "ERROR: Jarvis must only bind to 127.0.0.1 for local security. Configured: $HOST"
    exit 1
fi

echo "Starting Uvicorn on http://${HOST}:${PORT}..."
exec uvicorn api.main:app --host "$HOST" --port "$PORT" --reload
