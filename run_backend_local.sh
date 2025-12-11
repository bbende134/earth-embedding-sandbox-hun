#!/bin/bash
set -e

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "uv is not installed. Please install it first: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

echo "Installing dependencies..."
uv sync

echo "Starting backend..."
export MILVUS_HOST="localhost"
export MILVUS_PORT="19530"
export DISABLE_RATELIMIT="true"
export COLLECTION="high_res_hun"

# Run the app
uv run uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
