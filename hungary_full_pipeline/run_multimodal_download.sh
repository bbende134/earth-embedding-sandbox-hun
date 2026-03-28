#!/bin/bash
# Pipeline: Download S2/S1 via STAC and process through TerraMind into Milvus.
# No GEE, no Drive — data streams directly from Planetary Computer.
#
# Usage:
#   ./hungary_full_pipeline/run_multimodal_download.sh                     # smoke test then full run
#   ./hungary_full_pipeline/run_multimodal_download.sh --skip-test         # skip smoke test
#   ./hungary_full_pipeline/run_multimodal_download.sh --start=2021-01-01 --end=2022-01-01

SKIP_TEST=false
START_DATE=""
END_DATE=""
GEOJSON="budapest.geojson"
CLOUD_MAX=80

for arg in "$@"; do
    case "$arg" in
        --skip-test)    SKIP_TEST=true ;;
        --start=*)      START_DATE="${arg#*=}" ;;
        --end=*)        END_DATE="${arg#*=}" ;;
        --geojson=*)    GEOJSON="${arg#*=}" ;;
        --cloud-max=*)  CLOUD_MAX="${arg#*=}" ;;
    esac
done

DATE_ARGS=""
[ -n "$START_DATE" ] && DATE_ARGS="$DATE_ARGS --start=$START_DATE"
[ -n "$END_DATE" ]   && DATE_ARGS="$DATE_ARGS --end=$END_DATE"

echo "Multi-Modal STAC Pipeline"
echo "  Source:  Planetary Computer (no GEE / no Drive)"
echo "  GeoJSON: $GEOJSON"
[ -n "$START_DATE" ] && echo "  Range:   $START_DATE -> $END_DATE"
echo ""

# ----------------------------------------------------------------
# PHASE 1: SMOKE TEST — micro-tile, one month
# ----------------------------------------------------------------
if [ "$SKIP_TEST" = false ]; then
    echo "----------------------------------------------------------------"
    echo "PHASE 1: SMOKE TEST (224x224 micro-tile, one month)"
    echo "----------------------------------------------------------------"

    TEST_START=${START_DATE:-"2021-06-01"}
    TEST_END=${END_DATE:-"2021-07-01"}

    uv run python hungary_full_pipeline/0_export_stac.py \
        --geojson "$GEOJSON" \
        --start="$TEST_START" \
        --end="$TEST_END" \
        --cloud-max="$CLOUD_MAX" \
        --test-aoi \
        --process

    if [ $? -ne 0 ]; then
        echo "❌ Smoke test FAILED!"
        exit 1
    fi

    echo ""
    echo "✅ Smoke test PASSED!"
    read -p "Run full production pipeline? (Y to continue): " confirm
    if [[ "$confirm" != "Y" ]]; then
        echo "Aborting."
        exit 0
    fi
else
    echo "Skipping smoke test..."
fi

# ----------------------------------------------------------------
# PHASE 2: FULL RUN
# ----------------------------------------------------------------
echo ""
echo "----------------------------------------------------------------"
echo "PHASE 2: FULL PRODUCTION RUN"
echo "----------------------------------------------------------------"

uv run python hungary_full_pipeline/0_export_stac.py \
    --geojson "$GEOJSON" \
    $DATE_ARGS \
    --cloud-max="$CLOUD_MAX" \
    --process

if [ $? -ne 0 ]; then
    echo "❌ Full run FAILED!"
    exit 1
fi

echo ""
echo "✅ Done. Verify with:"
echo "   uv run python hungary_full_pipeline/check_multimodal_progress.py"
