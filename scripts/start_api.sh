#!/bin/bash
set -e
cd "$(dirname "$0")/.."
set -a; source .env; set +a
echo "Installing dependencies..."
pip install -r api/requirements.txt -q
echo "Starting API on port ${API_PORT:-8000}..."
uvicorn api.main:app --reload --reload-dir api --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"
