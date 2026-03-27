#!/bin/bash
# Pipeline: Export multi-modal EE data and download from Drive.
# Usage:
#   ./hungary_full_pipeline/run_multimodal_download.sh              # full run with smoke test
#   ./hungary_full_pipeline/run_multimodal_download.sh --skip-test  # skip smoke test
#   ./hungary_full_pipeline/run_multimodal_download.sh --download-only  # skip processing

SKIP_TEST=false
DOWNLOAD_ONLY=""
FORCE=""
START_DATE=""
END_DATE=""

for arg in "$@"; do
    case "$arg" in
        --skip-test)    SKIP_TEST=true ;;
        --download-only) DOWNLOAD_ONLY="--download-only" ;;
        --force)        FORCE="--force" ;;
        --start=*)      START_DATE="${arg#*=}" ;;
        --end=*)        END_DATE="${arg#*=}" ;;
    esac
done

DATE_ARGS=""
[ -n "$START_DATE" ] && DATE_ARGS="$DATE_ARGS --start=$START_DATE"
[ -n "$END_DATE" ]   && DATE_ARGS="$DATE_ARGS --end=$END_DATE"

echo "Starting Multi-Modal Download Pipeline..."
[ -n "$START_DATE" ] && echo "Date range: $START_DATE → $END_DATE"

if [ "$SKIP_TEST" = false ]; then
    echo "----------------------------------------------------------------"
    echo "PHASE 1: SMOKE TEST (Single Tile)"
    echo "----------------------------------------------------------------"

    echo "[Test] Submitting single tile export..."
    uv run python hungary_full_pipeline/0_export_multimodal.py --test $DATE_ARGS

    if [ $? -ne 0 ]; then
        echo "❌ [Test] Export submission failed!"
        exit 1
    fi

    echo "[Test] Waiting for export and downloading (One-Shot)..."
    uv run python hungary_full_pipeline/1_download_multimodal.py --one-shot --no-delete $DOWNLOAD_ONLY

    if [ $? -ne 0 ]; then
        echo "❌ [Test] Download failed!"
        exit 1
    fi

    echo "✅ [Test] Smoke test PASSED!"
    read -p "Run full production pipeline? (Y to continue): " confirm
    if [[ "$confirm" != "Y" ]]; then
        echo "Aborting full run."
        exit 0
    fi
else
    echo "Skipping Smoke Test..."
fi

echo "----------------------------------------------------------------"
echo "PHASE 2: FULL PRODUCTION RUN"
echo "----------------------------------------------------------------"

echo "Step 1: Submitting full export grid..."
read -p "Submit new Earth Engine export? (Y/N): " submit_confirm
if [[ "$submit_confirm" == "Y" || "$submit_confirm" == "y" ]]; then
    uv run python hungary_full_pipeline/0_export_multimodal.py $DATE_ARGS $FORCE
    if [ $? -ne 0 ]; then
        echo "❌ Export submission failed!"
        exit 1
    fi
    echo "✅ Export tasks submitted. Waiting for EE to process..."
else
    echo "Skipping submission — assuming exports are already running or done."
fi

echo ""
echo "Step 2: Starting download loop (monitors Drive until all files are downloaded)..."
echo "Press Ctrl+C to stop."
uv run python hungary_full_pipeline/1_download_multimodal.py $DOWNLOAD_ONLY
