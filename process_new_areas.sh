#!/bin/bash

# Script to process new areas for embedding extraction

AREAS=("northwest" "northeast" "southwest" "southeast")
SERVICE_ACCOUNT="bendebarcza@gen-lang-client-0291927848.iam.gserviceaccount.com"
PROJECT="86493264147"

# Set credentials
export GOOGLE_APPLICATION_CREDENTIALS="/home/barczabende/dev/earth-embedding-sandbox-hun/gen-lang-client-0291927848-14f8e1a428bd.json"

for area in "${AREAS[@]}"; do
  echo "Processing $area"

  # Step 1: Extract raw embeddings
  echo "Running extract for $area"
  uv run python pipeline/0_extract.py \
    --input_geojson ${area}_hungary.geojson \
    --utm_zone EPSG:32633 \
    --raw_archive gs://earth-embeddings-hungary-input/${area}_raw \
    --service_account_email $SERVICE_ACCOUNT \
    --project $PROJECT \
    --runner DirectRunner \
    --ee_max_num_workers 1 \
    --direct_num_workers 1

  echo "Sleeping 60 seconds before next step..."
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

  # Ask to continue
  read -p "Continue to next area? (y/n): " choice
  if [ "$choice" != "y" ]; then
    echo "Stopping processing."
    break
  fi
done

echo "Processing complete."