#!/bin/bash
# Script to process Hungary with neighbors for 2024 and 2017 only

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

# Load environment variables
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# More lenient error handling - don't exit on first error
# set -euo pipefail

# Configuration
GEOJSON_PATH="$ROOT_DIR/hungary_with_neighbors.geojson"
AREA_NAME="hungary_with_neighbors"
YEARS=("2024" "2017")

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting pipeline for Hungary with neighbors (2024, 2017 only)${NC}"
echo -e "${YELLOW}Environment variables loaded from .env${NC}"

# Check if GeoJSON exists
if [ ! -f "$GEOJSON_PATH" ]; then
    echo -e "${RED}Error: GeoJSON file not found at $GEOJSON_PATH${NC}"
    exit 1
fi

echo -e "${YELLOW}Using GeoJSON: $GEOJSON_PATH${NC}"

# Process each year
for year in "${YEARS[@]}"; do
    echo -e "${GREEN}Processing year: $year${NC}"

    # Check if required environment variables are set
    if [ -z "$GCP_PROJECT_ID" ] || [ -z "$service_account_email" ]; then
        echo -e "${RED}Error: GCP_PROJECT_ID or service_account_email not set${NC}"
        echo -e "${YELLOW}Please check your .env file${NC}"
        exit 1
    fi

    # Step 1: Extract data
    echo -e "${YELLOW}Step 1: Extracting raw data for $year...${NC}"
    if uv run python pipeline/0_extract.py \
        --input_geojson "$GEOJSON_PATH" \
        --raw_archive "gs://earth-embeddings-hungary-output/${AREA_NAME}_${year}_embeddings" \
        --start_date "${year}-01-01" \
        --end_date "${year}-12-31" \
        --utm_zone "EPSG:32633" \
        --scale 10 \
        --ee_max_num_workers 10 \
        --runner DirectRunner \
        --project "$GCP_PROJECT_ID" \
        --service_account_email "$service_account_email"; then
        echo -e "${GREEN}Step 1 completed successfully for $year${NC}"
    else
        echo -e "${RED}Step 1 failed for $year, continuing to next year...${NC}"
        continue
    fi

    # Step 2: Consolidate data
    echo -e "${YELLOW}Step 2: Consolidating data for $year...${NC}"
    if uv run python pipeline/1_consolidate.py \
        --raw_archive "gs://earth-embeddings-hungary-output/${AREA_NAME}_${year}_embeddings" \
        --reduced_archive "gs://earth-embeddings-hungary-output/${AREA_NAME}_${year}_embeddings"; then
        echo -e "${GREEN}Step 2 completed successfully for $year${NC}"
    else
        echo -e "${RED}Step 2 failed for $year, continuing to next year...${NC}"
        continue
    fi

    # Step 3: Reduce data (create zoom pyramid)
    echo -e "${YELLOW}Step 3: Creating zoom pyramid for $year...${NC}"
    if uv run python pipeline/2_reduce.py \
        --reduced_archive "gs://earth-embeddings-hungary-output/${AREA_NAME}_${year}_embeddings" \
        --runner DirectRunner \
        --project "$GCP_PROJECT_ID" \
        --service_account_email "$service_account_email"; then
        echo -e "${GREEN}Step 3 completed successfully for $year${NC}"
    else
        echo -e "${RED}Step 3 failed for $year, continuing to next year...${NC}"
        continue
    fi

    echo -e "${GREEN}Completed processing for year $year${NC}"
done

# Step 4: Load into database
echo -e "${YELLOW}Step 4: Loading data into new collection...${NC}"
if COLLECTION=hungary_with_neighbors_embeddings \
YEARS="2017,2024" \
uv run python pipeline/3_load_into_db.py; then
    echo -e "${GREEN}Step 4 completed successfully!${NC}"
else
    echo -e "${RED}Step 4 failed, but pipeline may have partially succeeded${NC}"
fi

echo -e "${GREEN}Pipeline completed!${NC}"
echo -e "${GREEN}New collection: hungary_with_neighbors_embeddings${NC}"
echo -e "${GREEN}Available years: 2017, 2024${NC}"