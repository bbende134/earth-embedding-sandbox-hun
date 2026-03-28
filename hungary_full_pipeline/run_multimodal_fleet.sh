#!/bin/bash
# Fleet orchestrator: Launch multiple workers in parallel
# Each worker processes one tile independently
#
# Usage:
#   ./hungary_full_pipeline/run_multimodal_fleet.sh --num-workers=4 --year=2021 --geojson=budapest.geojson
#   ./hungary_full_pipeline/run_multimodal_fleet.sh --num-workers=8 --year=2021 --tiles=0-50

set -e

NUM_WORKERS=4
YEAR=""
GEOJSON="budapest.geojson"
TILES=""  # e.g. "0-100" or "0,1,5,10" — if empty, auto-detect tile count
SKIP_DELETE=false

for arg in "$@"; do
    case "$arg" in
        --num-workers=*)    NUM_WORKERS="${arg#*=}" ;;
        --year=*)           YEAR="${arg#*=}" ;;
        --geojson=*)        GEOJSON="${arg#*=}" ;;
        --tiles=*)          TILES="${arg#*=}" ;;
        --skip-delete)      SKIP_DELETE=true ;;
    esac
done

if [ -z "$YEAR" ]; then
    echo "Usage: $0 --year=YYYY [--num-workers=N] [--geojson=FILE] [--tiles=0-100 | --tiles=0,1,5]"
    exit 1
fi

echo "================================"
echo "Fleet Orchestrator"
echo "  Workers: $NUM_WORKERS"
echo "  Year: $YEAR"
echo "  GeoJSON: $GEOJSON"
echo "================================"

# ----------------------------------------------------------------
# Determine tile indices to process
# ----------------------------------------------------------------
if [ -z "$TILES" ]; then
    # Auto-detect total tile count from the grid
    total_tiles=$(uv run python -c "
import geopandas as gpd

def create_grid(geojson_path, tile_size_deg=0.225):
    gdf = gpd.read_file(geojson_path)
    minx, miny, maxx, maxy = gdf.total_bounds
    tiles = []
    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            from shapely.geometry import box
            b = box(x, y, x + tile_size_deg, y + tile_size_deg)
            if b.intersects(gdf.geometry.unary_union):
                tiles.append(b)
            y += tile_size_deg
        x += tile_size_deg
    return tiles

tiles = create_grid('$GEOJSON')
print(len(tiles))
")
    echo "Auto-detected tile count: $total_tiles"
    tile_indices=$(seq 0 $((total_tiles - 1)))
elif [[ "$TILES" == *"-"* ]]; then
    # Range: "0-50"
    start=$(echo "$TILES" | cut -d- -f1)
    end=$(echo "$TILES" | cut -d- -f2)
    tile_indices=$(seq $start $end)
else
    # Explicit list: "0,1,5,10"
    tile_indices=$(echo "$TILES" | tr ',' '\n')
fi

# ----------------------------------------------------------------
# Launch workers
# ----------------------------------------------------------------
echo ""
echo "Launching workers..."
active_workers=()
failed_tiles=()

for tile_idx in $tile_indices; do
    # Wait if we have MAX workers running
    while [ ${#active_workers[@]} -ge $NUM_WORKERS ]; do
        for i in "${!active_workers[@]}"; do
            pid=${active_workers[$i]}
            if ! kill -0 "$pid" 2>/dev/null; then
                # Worker finished
                wait "$pid"
                exit_code=$?
                if [ $exit_code -ne 0 ]; then
                    failed_tiles+=($tile_idx)
                fi
                unset 'active_workers[$i]'
            fi
        done
        active_workers=("${active_workers[@]}")  # Reindex array
        sleep 1
    done

    # Launch worker
    delete_flag=""
    [ "$SKIP_DELETE" = true ] && delete_flag="--skip-delete"

    echo "[Tile $tile_idx] Starting worker..."
    ./hungary_full_pipeline/run_multimodal_worker.sh \
        --tile-idx="$tile_idx" \
        --year="$YEAR" \
        --geojson="$GEOJSON" \
        $delete_flag \
        &
    active_workers+=($!)
done

# ----------------------------------------------------------------
# Wait for all remaining workers
# ----------------------------------------------------------------
echo ""
echo "Waiting for remaining workers to finish..."
for pid in "${active_workers[@]}"; do
    wait "$pid"
    exit_code=$?
    if [ $exit_code -ne 0 ]; then
        failed_tiles+=($pid)
    fi
done

# ----------------------------------------------------------------
# Summary
# ----------------------------------------------------------------
echo ""
echo "================================"
echo "Fleet Processing Complete"
if [ ${#failed_tiles[@]} -eq 0 ]; then
    echo "✅ All tiles processed successfully"
else
    echo "⚠ Failed tiles: ${failed_tiles[@]}"
fi
echo "================================"

[ ${#failed_tiles[@]} -eq 0 ]
