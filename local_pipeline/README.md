# Local Pipeline for Processing Earth Engine Exports

This pipeline processes GeoTIFF files exported from Earth Engine into the Zarr format used by the main pipeline.

## Prerequisites

```bash
uv add rioxarray rasterio
```

## Steps

### Automated Run (Recommended)

Run the entire pipeline (Download -> Merge -> Convert) in one go:

```bash
uv run python local_pipeline/run_pipeline.py
```

**Authentication**:
- **Service Account (Recommended)**: Set `GOOGLE_APPLICATION_CREDENTIALS` in your `.env` file. Ensure the Drive folder is shared with the Service Account email.
- **Interactive**: Place `credentials.json` in `local_pipeline/` and follow the browser prompt.

### Manual Steps

### Step 0: Download from Google Drive

**Automated**:
```bash
uv run python local_pipeline/download_from_drive.py
```

**Manual**:
1. Go to https://drive.google.com
2. Navigate to `earth_engine_exports/`
3. Download all `budapest_2024-*.tif` tiles
4. Place them in the `data/` directory

### Step 1: Merge Tiles

```bash
uv run python local_pipeline/0_merge_tiles.py
```

This merges the tiled GeoTIFF exports into a single file: `data/budapest_2024.tif`

### Step 2: Convert to Zarr

```bash
uv run python local_pipeline/1_convert_to_zarr.py
```

This converts the GeoTIFF to Zarr format: `budapest_2024_raw.zarr`

### Step 3: Run Main Pipeline

Now use the existing pipeline scripts for consolidation, reduction, and loading:

```bash
# Consolidation (creates _z8 zoom level)
uv run python pipeline/1_consolidate.py \
  --raw_archive budapest_2024_raw.zarr \
  --reduced_archive budapest_2024_reduced.zarr

# Reduction (creates z16, z32, z64, z128, z256)
uv run python pipeline/2_reduce.py \
  --input budapest_2024_reduced.zarr_z8 \
  --output budapest_2024_reduced.zarr

# Load into Milvus
uv run python scripts/load_budapest_2024.py
```

### Step 4: Verify

```bash
uv run python scripts/hungary_plotting.py --year 2024 --zoom 16
```

Should show Budapest 2024 data on the plot!

## File Structure

```
data/                           # Downloaded GeoTIFF tiles
├── budapest_2024-0000000000.tif
├── budapest_2024-0000001024.tif
├── budapest_2024-0000002048.tif
└── budapest_2024-0000003072.tif

budapest_2024.tif              # Merged GeoTIFF (created by step 1)
budapest_2024_raw.zarr/        # Zarr format (created by step 2)
budapest_2024_reduced.zarr_z8/ # Consolidated (created by pipeline step 3)
budapest_2024_reduced.zarr_z*/ # Pyramid levels (created by pipeline step 3)
```
