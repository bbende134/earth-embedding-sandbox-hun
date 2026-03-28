#!/bin/bash
# Master orchestrator: Process ALL years (2017-2026) with parallel workers
# Each year is processed with N workers in parallel
#
# Usage:
#   ./hungary_full_pipeline/run_multimodal_all_years.sh --num-workers=8 --geojson=budapest.geojson
#   ./hungary_full_pipeline/run_multimodal_all_years.sh --num-workers=8 --start-year=2020 --end-year=2025

set -e

NUM_WORKERS=8
START_YEAR=2017
END_YEAR=2026
GEOJSON="budapest.geojson"
SKIP_DELETE=false

for arg in "$@"; do
    case "$arg" in
        --num-workers=*)    NUM_WORKERS="${arg#*=}" ;;
        --start-year=*)     START_YEAR="${arg#*=}" ;;
        --end-year=*)       END_YEAR="${arg#*=}" ;;
        --geojson=*)        GEOJSON="${arg#*=}" ;;
        --skip-delete)      SKIP_DELETE=true ;;
    esac
done

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║ Multimodal Pipeline - All Years                               ║"
echo "║ Workers: $NUM_WORKERS, Years: $START_YEAR-$END_YEAR, GeoJSON: $GEOJSON"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

START_TIME=$(date +%s)
FAILED_YEARS=()
SUCCESSFUL_YEARS=()

for year in $(seq $START_YEAR $END_YEAR); do
    echo "═══════════════════════════════════════════════════════════════"
    echo "Processing Year: $year"
    echo "═══════════════════════════════════════════════════════════════"

    YEAR_START_TIME=$(date +%s)

    # Build fleet command
    fleet_cmd="./hungary_full_pipeline/run_multimodal_fleet.sh --num-workers=$NUM_WORKERS --year=$year --geojson=$GEOJSON"
    [ "$SKIP_DELETE" = true ] && fleet_cmd="$fleet_cmd --skip-delete"

    if $fleet_cmd; then
        YEAR_END_TIME=$(date +%s)
        YEAR_DURATION=$((YEAR_END_TIME - YEAR_START_TIME))
        echo "✅ Year $year complete ($(($YEAR_DURATION / 60))m $(($YEAR_DURATION % 60))s)"
        SUCCESSFUL_YEARS+=($year)
    else
        YEAR_END_TIME=$(date +%s)
        YEAR_DURATION=$((YEAR_END_TIME - YEAR_START_TIME))
        echo "❌ Year $year FAILED ($(($YEAR_DURATION / 60))m $(($YEAR_DURATION % 60))s)"
        FAILED_YEARS+=($year)
    fi

    echo ""
done

# ════════════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════════════
END_TIME=$(date +%s)
TOTAL_DURATION=$((END_TIME - START_TIME))
HOURS=$((TOTAL_DURATION / 3600))
MINUTES=$(( (TOTAL_DURATION % 3600) / 60 ))
SECONDS=$((TOTAL_DURATION % 60))

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║ FINAL SUMMARY                                                  ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "Total time: ${HOURS}h ${MINUTES}m ${SECONDS}s"
echo ""
echo "✅ Successful years (${#SUCCESSFUL_YEARS[@]}):"
for y in "${SUCCESSFUL_YEARS[@]}"; do
    echo "   - $y"
done
echo ""

if [ ${#FAILED_YEARS[@]} -gt 0 ]; then
    echo "❌ Failed years (${#FAILED_YEARS[@]}):"
    for y in "${FAILED_YEARS[@]}"; do
        echo "   - $y"
    done
    exit 1
else
    echo "🎉 ALL YEARS PROCESSED SUCCESSFULLY!"
    exit 0
fi
