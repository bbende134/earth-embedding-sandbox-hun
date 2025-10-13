#!/bin/bash

# Script to process new areas for embedding extraction

AREAS=($(ls small_areas/*.geojson | sed 's/small_areas\///' | sed 's/\.geojson//'))
SERVICE_ACCOUNT="bendebarcza@gen-lang-client-0291927848.iam.gserviceaccount.com"
PROJECT="86493264147"
YEARS=(2017 2018 2019 2020 2021 2022 2023)  # Years to process

# Set credentials
export GOOGLE_APPLICATION_CREDENTIALS="/home/barczabende/dev/earth-embedding-sandbox-hun/gen-lang-client-0291927848-14f8e1a428bd.json"
export HV_URL="https://earthengine-highvolume.googleapis.com"

for area in "${AREAS[@]}"; do
  echo "Processing $area"

  for year in "${YEARS[@]}"; do
    echo "Processing $area for year $year"
    
    # Check if data for this area and year already exists in GCS
    archive_path="gs://earth-embeddings-hungary-output/${area}_${year}_embeddings_z16"
    archive_exists=$(uv run python3 -c "
import gcsfs
from google.oauth2 import service_account
import os
import sys

archive_path = sys.argv[1]
try:
    credentials = service_account.Credentials.from_service_account_file(
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'],
        scopes=['https://www.googleapis.com/auth/devstorage.read_write'],
    )
    fs = gcsfs.GCSFileSystem(token=credentials)
    # Check if the reduced archive exists
    exists = fs.exists(archive_path)
    print('exists' if exists else 'missing')
except Exception as e:
    print(f'Error checking archive: {e}')
    print('missing')
" "$archive_path" 2>/dev/null || echo "missing")

    if [ "$archive_exists" = "exists" ]; then
      echo "Archive for $area year $year already exists, skipping..."
      continue
    fi

    echo "Archive for $area year $year missing, processing..."

    # Step 1: Extract raw embeddings
    echo "Running extract for $area year $year"
    uv run python pipeline/0_extract.py \
      --input_geojson small_areas/${area}.geojson \
      --utm_zone EPSG:32633 \
      --raw_archive gs://earth-embeddings-hungary-input/${area}_${year}_raw \
      --service_account_email $SERVICE_ACCOUNT \
      --project $PROJECT \
      --runner DirectRunner \
      --ee_max_num_workers 1 \
      --direct_num_workers 1 \
      --start_date ${year}-01-01 \
      --end_date ${year}-12-31

    echo "1_============================="

    # Step 2: Consolidate
    echo "Running consolidate for $area year $year"
    GRPC_MAX_MESSAGE_LENGTH=8000000000 uv run python pipeline/1_consolidate.py \
      --raw_archive gs://earth-embeddings-hungary-input/${area}_${year}_raw \
      --reduced_archive gs://earth-embeddings-hungary-output/${area}_${year}_embeddings \
      --runner DirectRunner \
      --service_account_email $SERVICE_ACCOUNT \
      --project $PROJECT \
      --direct_runner_grpc_max_message_size=8000000000 \
      --direct_num_workers=1

    echo "2_ ============================="

    # Step 3: Reduce (create zoom levels)
    echo "Running reduce for $area year $year"
    uv run python pipeline/2_reduce.py \
      --reduced_archive gs://earth-embeddings-hungary-output/${area}_${year}_embeddings \
      --service_account_email $SERVICE_ACCOUNT \
      --project $PROJECT \
      --direct_runner_grpc_max_message_size=4294967296

    echo "3_============================="

    # Step 4: Load into database
    echo "Running load for $area year $year"
    reduced_archive=gs://earth-embeddings-hungary-output/${area}_${year}_embeddings \
    GCP_PROJECT_ID=$PROJECT \
    YEARS="$(echo $YEARS | tr ' ' ',')" \
    uv run python pipeline/3_load_into_db.py

    echo "Completed processing $area year $year"
    echo "-----------------------------------test--------------------------------------"
    # Test the data
    echo "Testing data for $area year $year"
    geojson=$(cat small_areas/${area}.geojson)
    response=$(curl -s -X POST http://localhost:8000/neighbours -H "Content-Type: application/json" -d "{\"geojson\": $geojson, \"k\": 5, \"year\": $year}")
    if [ $? -ne 0 ]; then
      echo "Curl failed for $area year $year, continuing..."
      continue
    fi
    # Check if response has neighbours with features
    if echo "$response" | jq -e '.neighbours.features | length > 0' > /dev/null 2>&1; then
      echo "Data found for $area year $year"
    else
      echo "No valid data found for $area year $year (empty or all-zero embeddings)"
    fi
  done

  # Move area to processed folder after processing all years
  mv small_areas/${area}.geojson processed/
done

echo "Processing complete."