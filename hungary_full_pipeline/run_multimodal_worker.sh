#!/bin/bash
# Worker: Process one tile end-to-end (per-scene, no intermediate files)
#
# Usage:
#   ./hungary_full_pipeline/run_multimodal_worker.sh --tile-idx=42 --year=2021

set -e

TILE_IDX=""
YEAR=""
GEOJSON="budapest.geojson"
COLLECTION="dyn_terra"
MILVUS_HOST="localhost"
MILVUS_PORT="19530"

for arg in "$@"; do
    case "$arg" in
        --tile-idx=*)       TILE_IDX="${arg#*=}" ;;
        --year=*)           YEAR="${arg#*=}" ;;
        --geojson=*)        GEOJSON="${arg#*=}" ;;
        --collection=*)     COLLECTION="${arg#*=}" ;;
        --milvus-host=*)    MILVUS_HOST="${arg#*=}" ;;
        --milvus-port=*)    MILVUS_PORT="${arg#*=}" ;;
    esac
done

if [ -z "$TILE_IDX" ] || [ -z "$YEAR" ]; then
    echo "Usage: $0 --tile-idx=N --year=YYYY [--geojson=FILE]"
    exit 1
fi

timeout 900 uv run python hungary_full_pipeline/process_tile.py \
    --tile-idx "$TILE_IDX" \
    --year "$YEAR" \
    --geojson "$GEOJSON" \
    --collection "$COLLECTION" \
    --milvus-host "$MILVUS_HOST" \
    --milvus-port "$MILVUS_PORT"

exit_code=$?
if [ $exit_code -eq 124 ]; then
    echo "Tile $TILE_IDX timed out (15min)"
    exit 1
fi
exit $exit_code
