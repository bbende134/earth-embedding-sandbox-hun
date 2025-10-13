#!/bin/bash

# Script to process new areas for embedding extraction

AREAS=($(ls small_areas/*.geojson | sed 's/small_areas\///' | sed 's/\.geojson//'))
SERVICE_ACCOUNT="bendebarcza@gen-lang-client-0291927848.iam.gserviceaccount.com"
PROJECT="86493264147"

# Set credentials
export GOOGLE_APPLICATION_CREDENTIALS="/home/barczabende/dev/earth-embedding-sandbox-hun/gen-lang-client-0291927848-14f8e1a428bd.json"
export HV_URL="https://earthengine-highvolume.googleapis.com"

for area in "${AREAS[@]}"; do
  echo "Processing $area"

  # Step 1: Extract raw embeddings
  echo "Running extract for $area"
  uv run python pipeline/0_extract.py \
    --input_geojson small_areas/${area}.geojson \
    --utm_zone EPSG:32633 \
    --raw_archive gs://earth-embeddings-hungary-input/${area}_raw \
    --service_account_email $SERVICE_ACCOUNT \
    --project $PROJECT \
    --runner DirectRunner \
    --ee_max_num_workers 1 \
    --direct_num_workers 1

  echo "next step..."
  sleep 60

  # Step 2: Consolidate
  echo "Running consolidate for $area"
  GRPC_MAX_MESSAGE_LENGTH=8000000000 uv run python pipeline/1_consolidate.py \
    --raw_archive gs://earth-embeddings-hungary-input/${area}_raw \
    --reduced_archive gs://earth-embeddings-hungary-output/${area}_embeddings \
    --runner DirectRunner \
    --service_account_email $SERVICE_ACCOUNT \
    --project $PROJECT \
    --direct_runner_grpc_max_message_size=8000000000 \
    --direct_num_workers=1

  echo "Sleeping 30 seconds before next step..."
  sleep 30

  # Step 3: Reduce (create zoom levels)
  echo "Running reduce for $area"
  uv run python pipeline/2_reduce.py \
    --reduced_archive gs://earth-embeddings-hungary-output/${area}_embeddings \
    --service_account_email $SERVICE_ACCOUNT \
    --project $PROJECT \
    --direct_runner_grpc_max_message_size=4294967296

  echo "Sleeping 30 seconds before next step..."
  sleep 30

  # Step 4: Load into database
  echo "Running load for $area"
  reduced_archive=gs://earth-embeddings-hungary-output/${area}_embeddings \
  GCP_PROJECT_ID=$PROJECT \
  uv run python pipeline/3_load_into_db.py

  echo "Completed processing $area"

  # Test the data
  echo "Testing data for $area"
  geojson=$(cat small_areas/${area}.geojson)
  response=$(curl -s -X POST http://localhost:8000/neighbours -H "Content-Type: application/json" -d "{\"geojson\": $geojson, \"k\": 5}")
  if [ $? -ne 0 ]; then
    echo "Curl failed for $area, stopping processing."
    break
  fi
  # Check if response has neighbours with features
  if echo "$response" | jq -e '.neighbours.features | length > 0' > /dev/null 2>&1; then
    echo "Data found for $area, moving to processed folder."
    mv small_areas/${area}.geojson processed/
  else
    echo "No valid data found for $area (empty or all-zero embeddings), stopping processing."
    break
  fi
done

echo "Processing complete."