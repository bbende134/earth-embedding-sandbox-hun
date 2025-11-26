SHELL := /bin/bash

include .env
export

# ####################### #
# docker building and pushing for the beam sdk container
# ####################### #
docker-auth:
	gcloud auth activate-service-account --key-file="$GOOGLE_APPLICATION_CREDENTIALS"
	gcloud auth configure-docker

docker-build:
	docker build -t $$sdk_container_image -f infra/DockerfileBeam . --no-cache

docker-push:
	docker push $$sdk_container_image

# ####################### #
# docker compose commands #
# ####################### #

# dev build has no api (use with e.g. FastAPI TestClient)
up-build-dev:
	docker compose -f infra/docker-compose-dev.yaml up --build

down-v-dev:
	docker compose -f infra/docker-compose-dev.yaml down -v

# standard build includes API (e.g. for frontend dev)
up-build:
	docker compose -f infra/docker-compose.yaml up --build

down-v:
	docker compose -f infra/docker-compose.yaml down -v

# app build includes front-end
up-build-app:
	docker compose -f infra/docker-compose-app.yaml up --build

down-v-app:
	docker compose -f infra/docker-compose-app.yaml down -v

# ####################### #
# ### pipeline steps #### #
# ####################### #
extract-dataflow:
	uv run python pipeline/0_extract.py \
		--input_geojson $$geojson_path \
		--raw_archive $$raw_archive \
		--runner DataflowRunner \
		--temp_location $$temp_location \
		--region $$GCP_REGION \
		--staging_location $$staging_location \
		--sdk_location container \
		--sdk_container_image $$sdk_container_image \
		--job_name extract-dataset-subset \
		--project $$GCP_PROJECT_ID \
		--scale $$scale \
		--max_num_workers $$df_max_num_workers \
		--ee_max_num_workers $$ee_max_num_workers \
		--service_account_email $$SERVICE_ACCOUNT_EMAIL \
		--machine_type $$machine_type_extract

extract-dataflow-bandwise:
	@set -euo pipefail; \
	for i in $$(seq -w 0 2 62); do \
		j=$$(printf "%02d" $$((10#$$i + 1))); \
		band_set="A$$i,A$$j"; \
		job_name="extract-dataset-subset-$$(echo $$band_set | tr ',' '-' | tr '[:upper:]' '[:lower:]')"; \
		raw_archive_banded="$${raw_archive}-$$(echo $$band_set | tr ',' '-')"; \
		echo "→ Running extract for bands: $$band_set (job: $$job_name) → raw_archive=$$raw_archive_banded"; \
		uv run python pipeline/0_extract.py \
			--input_geojson $${geojson_path} \
			--raw_archive "$$raw_archive_banded" \
			--runner DataflowRunner \
			--temp_location $${temp_location} \
			--region $${GCP_REGION} \
			--staging_location $${staging_location} \
			--sdk_location container \
			--sdk_container_image $${sdk_container_image} \
			--job_name "$$job_name" \
			--project $${GCP_PROJECT_ID} \
			--scale $${scale} \
			--bands $${band_set} \
			--utm_zone $${TARGET_CRS} \
			--max_num_workers $${df_max_num_workers} \
			--ee_max_num_workers $${ee_max_num_workers} \
			--service_account_email $${SERVICE_ACCOUNT_EMAIL} \
			--machine_type $${machine_type_extract}; \
	done

extract-local:
	uv run python pipeline/0_extract.py \
		--input_geojson $$geojson_path \
		--raw_archive $$raw_archive \
		--runner DirectRunner \
		--project $$GCP_PROJECT_ID \
		--service_account_email $$SERVICE_ACCOUNT_EMAIL \
		--scale $$scale \
		--num_workers $$max_num_workers \
		--ee_max_num_workers $$ee_max_num_workers

consolidate-dataflow:
	uv run python pipeline/1_consolidate.py \
		--raw_archive $$raw_archive \
		--reduced_archive $$reduced_archive \
		--runner DataflowRunner \
		--temp_location $$temp_location \
		--region $$GCP_REGION \
		--staging_location $$staging_location \
		--sdk_location container \
		--sdk_container_image $$sdk_container_image \
		--job_name consolidate-dataset \
		--project $$GCP_PROJECT_ID \
		--max_num_workers $$df_max_num_workers \
		--service_account_email $$SERVICE_ACCOUNT_EMAIL \
		--machine_type $$machine_type_consolidate

reduce-dataflow:
	uv run python pipeline/2_reduce.py \
		--reduced_archive $$reduced_archive \
		--runner DataflowRunner \
		--temp_location $$temp_location \
		--region $$GCP_REGION \
		--staging_location $$staging_location \
		--sdk_location container \
		--sdk_container_image $$sdk_container_image \
		--job_name reduce-dataset \
		--project $$GCP_PROJECT_ID \
		--max_num_workers $$df_max_num_workers \
		--service_account_email $$SERVICE_ACCOUNT_EMAIL \
		--machine_type $$machine_type_reduce
