# Processing Missing Area Exports

I have identified the missing area in Eastern Hungary and submitted export tasks to Earth Engine. Once these exports are complete, you can run the local processing pipeline to integrate this data.

## 1. Monitor Exports
Check the status of the export tasks at: [https://code.earthengine.google.com/tasks](https://code.earthengine.google.com/tasks)
Wait for tasks named `hun_2024_tile_XXX` to complete.

## 2. Run Local Pipeline
Once the exports are finished, run the following command to download and process the new tiles:

```bash
uv run python local_pipeline/run_pipeline.py --pattern hun_2024_tile --folder earth_engine_exports_hun --interactive
```

This will:
1.  **Download** the new tiles from Google Drive (`earth_engine_exports/hun_2024_tile_*.tif`) to `data/`.
2.  **Merge** them into `data/hun_2024_tile.tif`.
3.  **Convert** the merged file to `hun_2024_tile_raw.zarr`.

## 3. Next Steps
After the Zarr archive is created (`hun_2024_tile_raw.zarr`), run the following commands to consolidate, reduce, and load the data into Milvus:

### 3.1 Consolidate
This step merges the raw data and creates the base zoom level (z8).
```bash
uv run python pipeline/1_consolidate.py \
  --raw_archive hun_2024_tile_raw.zarr \
  --reduced_archive hun_2024_tile_reduced.zarr
```

### 3.2 Reduce
This step creates the other zoom levels (z16, z32, etc.) for efficient querying.
```bash
uv run python pipeline/2_reduce.py \
  --reduced_archive hun_2024_tile_reduced.zarr
```

### 3.3 Load
This step loads the processed data into the Milvus database.
```bash
uv run python hungary_full_pipeline/2_load_generic.py \
  --input hun_2024_tile_reduced.zarr_z8 \
  --collection high_res_hun
```
