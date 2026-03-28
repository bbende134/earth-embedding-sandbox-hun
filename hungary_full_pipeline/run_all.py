"""
Master orchestrator: Process all tiles x all years x all scenes with parallel workers.
Each worker handles ONE scene (one sensor, one date, one tile) end-to-end:
  download -> process with TerraMind API -> insert embeddings + tokens into Milvus -> next

Usage:
    uv run python hungary_full_pipeline/run_all.py --num-workers=8
    uv run python hungary_full_pipeline/run_all.py --num-workers=8 --start-year=2020 --end-year=2025
    uv run python hungary_full_pipeline/run_all.py --num-workers=8 --tiles=0-50
"""

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import geopandas as gpd
import numpy as np
import planetary_computer
import pystac_client
import rasterio
import rasterio.warp
import requests
from affine import Affine
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from rasterio.transform import xy as transform_xy
from shapely.geometry import box
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
API_URL = "http://192.168.242.180:8000"
PATCH_SIZE = 224
EMBEDDING_DIM = 1024
TOKEN_DIM = 196
TARGET_RES_DEG = 10 / 111320
NO_NDVI = -999.0
NDVI_B8_IDX = 7
NDVI_B4_IDX = 3

SENSOR_CFG = {
    "s2l2a": {
        "collection": "sentinel-2-l2a",
        "assets": [
            "B01",
            "B02",
            "B03",
            "B04",
            "B05",
            "B06",
            "B07",
            "B08",
            "B8A",
            "B09",
            "B11",
            "B12",
        ],
        "band_names": ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"],
        "modality": "untok_sen2l2a@224",
        "scale": 1 / 10000.0,
        "cloud_filter": True,
    },
    "s1grd": {
        "collection": "sentinel-1-rtc",
        "assets": ["vv", "vh"],
        "band_names": ["VV", "VH"],
        "modality": "untok_sen1grd@224",
        "scale": 1.0,
        "cloud_filter": False,
    },
}


# ---------------------------------------------------------------------------
# Grid + STAC
# ---------------------------------------------------------------------------


def create_grid(geojson_path, tile_size_deg=0.225):
    gdf = gpd.read_file(geojson_path)
    minx, miny, maxx, maxy = gdf.total_bounds
    tiles = []
    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            b = box(x, y, x + tile_size_deg, y + tile_size_deg)
            if b.intersects(gdf.geometry.unary_union):
                tiles.append(b)
            y += tile_size_deg
        x += tile_size_deg
    return tiles


def query_items(sensor, bbox, date_start, date_end, cloud_max=80):
    cfg = SENSOR_CFG[sensor]
    catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    search_kwargs = {
        "collections": [cfg["collection"]],
        "bbox": bbox,
        "datetime": f"{date_start}/{date_end}",
    }
    if cfg["cloud_filter"]:
        search_kwargs["query"] = {"eo:cloud_cover": {"lt": cloud_max}}
    items = list(catalog.search(**search_kwargs).item_collection())
    items.sort(key=lambda i: i.datetime)
    if sensor == "s1grd":
        seen, unique = set(), []
        for item in items:
            d = item.datetime.strftime("%Y%m%d")
            if d not in seen:
                seen.add(d)
                unique.append(item)
        items = unique
    return items


# ---------------------------------------------------------------------------
# Milvus collections
# ---------------------------------------------------------------------------

_milvus_lock = threading.Lock()
_milvus_connected = False
_emb_coll = None
_tok_coll = None


def get_or_create_embedding_collection(name):
    if utility.has_collection(name):
        return Collection(name)
    fields = [
        FieldSchema("id", DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema("vector", DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
        FieldSchema("lat", DataType.FLOAT),
        FieldSchema("lon", DataType.FLOAT),
        FieldSchema("date_start", DataType.VARCHAR, max_length=20),
        FieldSchema("date_end", DataType.VARCHAR, max_length=20),
        FieldSchema("modality", DataType.VARCHAR, max_length=60),
        FieldSchema("tile_id", DataType.VARCHAR, max_length=120),
        FieldSchema("patch_row", DataType.INT32),
        FieldSchema("patch_col", DataType.INT32),
        FieldSchema("ndvi", DataType.FLOAT),
    ]
    schema = CollectionSchema(fields, "TerraMind embeddings")
    coll = Collection(name, schema)
    coll.create_index(
        "vector", {"metric_type": "COSINE", "index_type": "IVF_FLAT", "params": {"nlist": 1024}}
    )
    coll.create_index("modality", {"index_type": "Trie"})
    return coll


def get_or_create_token_collection(name):
    if utility.has_collection(name):
        return Collection(name)
    fields = [
        FieldSchema("id", DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema("tokens", DataType.FLOAT_VECTOR, dim=TOKEN_DIM),
        FieldSchema("embedding_id", DataType.INT64),
        FieldSchema("lat", DataType.FLOAT),
        FieldSchema("lon", DataType.FLOAT),
        FieldSchema("date_start", DataType.VARCHAR, max_length=20),
        FieldSchema("date_end", DataType.VARCHAR, max_length=20),
        FieldSchema("modality", DataType.VARCHAR, max_length=60),
        FieldSchema("tile_id", DataType.VARCHAR, max_length=120),
        FieldSchema("patch_row", DataType.INT32),
        FieldSchema("patch_col", DataType.INT32),
    ]
    schema = CollectionSchema(fields, "TerraMind VQ-VAE tokens")
    coll = Collection(name, schema)
    coll.create_index(
        "tokens", {"metric_type": "L2", "index_type": "IVF_FLAT", "params": {"nlist": 512}}
    )
    coll.create_index("embedding_id", {"index_type": "STL_SORT"})
    coll.create_index("modality", {"index_type": "Trie"})
    return coll


def ensure_milvus(milvus_host, milvus_port, collection, token_collection):
    """Thread-safe lazy Milvus init. Returns (emb_coll, tok_coll)."""
    global _milvus_connected, _emb_coll, _tok_coll
    if _milvus_connected:
        return _emb_coll, _tok_coll
    with _milvus_lock:
        if _milvus_connected:
            return _emb_coll, _tok_coll
        for attempt in range(5):
            try:
                connections.connect("default", host=milvus_host, port=milvus_port, timeout=10)
                _emb_coll = get_or_create_embedding_collection(collection)
                _tok_coll = get_or_create_token_collection(token_collection)
                _milvus_connected = True
                return _emb_coll, _tok_coll
            except Exception as e:
                tqdm.write(f"  Milvus connect attempt {attempt + 1}/5: {e}")
                time.sleep(10)
        raise RuntimeError("Could not connect to Milvus after 5 attempts")


# ---------------------------------------------------------------------------
# Download + process one scene
# ---------------------------------------------------------------------------


def download_scene_bands(sensor, item, target_transform, target_w, target_h, target_crs):
    cfg = SENSOR_CFG[sensor]
    date_str = item.datetime.strftime("%Y-%m-%d")
    bands = []
    env = rasterio.Env(
        GDAL_HTTP_TIMEOUT=30,
        GDAL_HTTP_MAX_RETRY=3,
        GDAL_HTTP_RETRY_DELAY=5,
    )
    # Re-sign the item right before download (SAS tokens expire after ~30min)
    planetary_computer.sign_inplace(item)
    for asset_key in cfg["assets"]:
        if asset_key not in item.assets:
            bands.append(np.zeros((target_h, target_w), dtype=np.float32))
            continue
        url = item.assets[asset_key].href
        dst = np.zeros((target_h, target_w), dtype=np.float32)
        with env, rasterio.open(url) as src:
            rasterio.warp.reproject(
                source=rasterio.band(src, 1),
                destination=dst,
                dst_transform=target_transform,
                dst_crs=target_crs,
                resampling=Resampling.bilinear,
            )
        bands.append((dst * cfg["scale"]).astype(np.float32))
    return date_str, np.stack(bands, axis=0)


def bands_to_tif_bytes(band_data, transform, crs):
    if band_data.ndim == 2:
        band_data = band_data[np.newaxis]
    n_bands, h, w = band_data.shape
    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff",
            height=h,
            width=w,
            count=n_bands,
            dtype="float32",
            crs=crs,
            transform=transform,
        ) as ds:
            ds.write(band_data)
        return mem.read()


def call_infer(modality_key, tif_bytes):
    resp = requests.post(
        f"{API_URL}/infer",
        data=[("modality", modality_key)],
        files=[("file", (f"{modality_key}.tif", tif_bytes, "image/tiff"))],
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["modalities"][modality_key]


def process_single_scene(job):
    """
    Process one scene end-to-end:
      download -> patches -> TerraMind API -> insert embeddings + tokens -> done
    Returns (job_label, success, n_emb_rows, n_tok_rows, error_msg).
    """
    sensor, tile_idx, tile, item, year, milvus_host, milvus_port, collection, token_collection = job
    cfg = SENSOR_CFG[sensor]
    bbox = list(tile.bounds)
    date_label = item.datetime.strftime("%Y-%m-%d")
    job_label = f"{sensor} t{tile_idx:03d} {date_label}"

    try:
        # 1. Target grid
        minx, miny, maxx, maxy = bbox
        target_w = max(PATCH_SIZE, round((maxx - minx) / TARGET_RES_DEG))
        target_h = max(PATCH_SIZE, round((maxy - miny) / TARGET_RES_DEG))
        target_transform = from_bounds(minx, miny, maxx, maxy, target_w, target_h)
        target_crs = "EPSG:4326"

        # 2. Download
        date_str, scene_data = download_scene_bands(
            sensor, item, target_transform, target_w, target_h, target_crs
        )

        # 3. Pad to patch multiples
        _, h, w = scene_data.shape
        pad_h = (PATCH_SIZE - h % PATCH_SIZE) % PATCH_SIZE
        pad_w = (PATCH_SIZE - w % PATCH_SIZE) % PATCH_SIZE
        if pad_h or pad_w:
            scene_data = np.pad(
                scene_data, ((0, 0), (0, pad_h), (0, pad_w)), mode="constant", constant_values=0.0
            )

        n_rows = scene_data.shape[1] // PATCH_SIZE
        n_cols = scene_data.shape[2] // PATCH_SIZE
        tile_id = f"{sensor}_{year}_tile_{tile_idx:03d}"

        # 4. Process patches -> embeddings + tokens
        rows = []
        for r in range(n_rows):
            for c in range(n_cols):
                row_off = r * PATCH_SIZE
                col_off = c * PATCH_SIZE
                patch = scene_data[
                    :, row_off : row_off + PATCH_SIZE, col_off : col_off + PATCH_SIZE
                ]

                # Skip patches with NaN input data or mostly zeros
                if np.isnan(patch).any() or (patch == 0.0).mean() > 0.5:
                    continue

                patch_transform = target_transform * Affine.translation(col_off, row_off)
                lon, lat = transform_xy(
                    target_transform, row_off + PATCH_SIZE // 2, col_off + PATCH_SIZE // 2
                )

                ndvi = NO_NDVI
                if sensor == "s2l2a":
                    b8 = patch[NDVI_B8_IDX].astype(np.float32)
                    b4 = patch[NDVI_B4_IDX].astype(np.float32)
                    denom = b8 + b4
                    valid = denom > 0
                    if valid.any():
                        ndvi_arr = np.where(valid, (b8 - b4) / np.where(valid, denom, 1.0), 0.0)
                        ndvi = float(ndvi_arr[valid].mean())

                tif_bytes = bands_to_tif_bytes(
                    patch.astype(np.float32), patch_transform, target_crs
                )
                mod_data = call_infer(cfg["modality"], tif_bytes)

                if mod_data.get("embeddings") is None:
                    continue

                try:
                    emb = np.array(mod_data["embeddings"], dtype=np.float32)
                except (TypeError, ValueError):
                    # API returned None/corrupt entries under load — retry once
                    tif_bytes2 = bands_to_tif_bytes(
                        patch.astype(np.float32), patch_transform, target_crs
                    )
                    mod_data = call_infer(cfg["modality"], tif_bytes2)
                    if mod_data.get("embeddings") is None:
                        continue
                    try:
                        emb = np.array(mod_data["embeddings"], dtype=np.float32)
                    except (TypeError, ValueError):
                        continue

                vector = emb[0].mean(axis=0)

                # Skip patches with NaN/Inf embeddings
                if np.isnan(vector).any() or np.isinf(vector).any():
                    continue

                vector = vector.tolist()
                tokens = None
                if mod_data.get("tokens") is not None:
                    tok_arr = np.array(mod_data["tokens"]).flatten()
                    if not (np.isnan(tok_arr).any() or np.isinf(tok_arr).any()):
                        tokens = tok_arr.tolist()

                rows.append(
                    {
                        "lat": float(lat),
                        "lon": float(lon),
                        "vector": vector,
                        "modality": cfg["modality"],
                        "tokens": tokens,
                        "date_start": date_str,
                        "date_end": date_str,
                        "tile_id": tile_id,
                        "patch_row": r,
                        "patch_col": c,
                        "ndvi": ndvi,
                    }
                )

        del scene_data

        if not rows:
            return (job_label, True, 0, 0, "no valid patches")

        # 5. Insert into Milvus (embeddings + tokens)
        emb_coll, tok_coll = ensure_milvus(milvus_host, milvus_port, collection, token_collection)

        has_ndvi = any(f.name == "ndvi" for f in emb_coll.schema.fields)
        entities = [
            [r["vector"] for r in rows],
            [r["lat"] for r in rows],
            [r["lon"] for r in rows],
            [r["date_start"] for r in rows],
            [r["date_end"] for r in rows],
            [r["modality"] for r in rows],
            [r["tile_id"] for r in rows],
            [r["patch_row"] for r in rows],
            [r["patch_col"] for r in rows],
        ]
        if has_ndvi:
            entities.append([r["ndvi"] for r in rows])
        emb_ids = emb_coll.insert(entities).primary_keys

        # Insert tokens
        n_tok = 0
        tok_rows = [
            (r, eid) for r, eid in zip(rows, emb_ids, strict=False) if r["tokens"] is not None
        ]
        if tok_rows:
            tok_entities = [
                [[float(t) for t in r["tokens"]] for r, _ in tok_rows],
                [eid for _, eid in tok_rows],
                [r["lat"] for r, _ in tok_rows],
                [r["lon"] for r, _ in tok_rows],
                [r["date_start"] for r, _ in tok_rows],
                [r["date_end"] for r, _ in tok_rows],
                [r["modality"] for r, _ in tok_rows],
                [r["tile_id"] for r, _ in tok_rows],
                [r["patch_row"] for r, _ in tok_rows],
                [r["patch_col"] for r, _ in tok_rows],
            ]
            tok_coll.insert(tok_entities)
            n_tok = len(tok_rows)

        # 6. Verify: check embeddings are non-zero and insertion succeeded
        if not emb_ids:
            return (job_label, False, 0, 0, "insert returned no IDs")

        # Spot-check first embedding is non-zero
        first_vec = rows[0]["vector"]
        vec_norm = sum(v * v for v in first_vec) ** 0.5
        if vec_norm == 0.0:
            return (job_label, False, len(rows), n_tok, "zero embedding vector")

        return (
            job_label,
            True,
            len(rows),
            n_tok,
            f"emb={len(emb_ids)} tok={n_tok} norm={vec_norm:.2f}",
        )

    except Exception:
        import traceback

        tb = traceback.format_exc().strip().split("\n")
        # Last 3 lines of traceback for context
        short_tb = " | ".join(tb[-3:])
        return (job_label, False, 0, 0, short_tb)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_tiles(tiles_str, total):
    if not tiles_str:
        return list(range(total))
    if "-" in tiles_str and "," not in tiles_str:
        start, end = tiles_str.split("-", 1)
        return list(range(int(start), int(end) + 1))
    return [int(t) for t in tiles_str.split(",")]


def main():
    parser = argparse.ArgumentParser(description="Per-scene parallel pipeline")
    parser.add_argument("--num-workers", type=int, default=8)
    parser.add_argument("--start-year", type=int, default=2017)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--geojson", default="budapest.geojson")
    parser.add_argument("--sensors", default="s2l2a,s1grd")
    parser.add_argument("--tiles", default=None, help="e.g. '0-50' or '0,5,10'")
    parser.add_argument("--cloud-max", type=int, default=80)
    parser.add_argument("--collection", default="dyn_terra")
    parser.add_argument("--token-collection", default="dyn_terra_tokens")
    parser.add_argument("--milvus-host", default="localhost")
    parser.add_argument("--milvus-port", default="19530")
    args = parser.parse_args()

    sensors = [s.strip() for s in args.sensors.split(",")]
    tiles = create_grid(args.geojson)
    tile_indices = parse_tiles(args.tiles, len(tiles))
    years = list(range(args.start_year, args.end_year + 1))

    # ---- Phase 1: Query STAC to build the full scene list ----
    print(f"\n{'=' * 60}")
    print("Phase 1: Querying STAC catalog")
    print(f"  Tiles: {len(tile_indices)}, Years: {years[0]}-{years[-1]}, Sensors: {sensors}")
    print(f"{'=' * 60}")

    jobs = []
    stac_queries = [(t, y, s) for t in tile_indices for y in years for s in sensors]

    with tqdm(stac_queries, desc="Querying STAC", unit="query") as pbar:
        for tile_idx, year, sensor in pbar:
            tile = tiles[tile_idx]
            bbox = list(tile.bounds)
            items = query_items(sensor, bbox, f"{year}-01-01", f"{year}-12-31", args.cloud_max)
            for item in items:
                jobs.append(
                    (
                        sensor,
                        tile_idx,
                        tile,
                        item,
                        year,
                        args.milvus_host,
                        args.milvus_port,
                        args.collection,
                        args.token_collection,
                    )
                )
            pbar.set_postfix(scenes=len(jobs), last=f"t{tile_idx:03d}/{year}/{sensor}={len(items)}")

    total_stac = len(jobs)
    print(f"\nTotal scenes from STAC: {total_stac}")

    # ---- Phase 1b: Dedup — skip scenes already in Milvus ----
    print("\nChecking Milvus for already-processed scenes...")
    processed_keys = set()
    try:
        connections.connect("default", host=args.milvus_host, port=args.milvus_port, timeout=10)
        if utility.has_collection(args.collection):
            coll = Collection(args.collection)
            load_state = str(utility.load_state(args.collection))
            if "NotLoad" in load_state:
                coll.load()
                utility.wait_for_loading_complete(args.collection, timeout=120)
            elif "Loaded" not in load_state:
                import time as _time

                for _ in range(20):
                    if "Loaded" in str(utility.load_state(args.collection)):
                        break
                    _time.sleep(5)

            iterator = coll.query_iterator(
                expr="",
                output_fields=["tile_id", "date_start", "modality"],
                batch_size=10000,
            )
            while True:
                rows = iterator.next()
                if not rows:
                    break
                for r in rows:
                    processed_keys.add((r["tile_id"], r["date_start"], r["modality"]))
            iterator.close()
    except Exception as e:
        print(f"  Warning: Could not check Milvus for dedup: {e}")

    if processed_keys:
        before = len(jobs)
        jobs = [
            j
            for j in jobs
            if (
                f"{j[0]}_{j[4]}_tile_{j[1]:03d}",
                j[3].datetime.strftime("%Y-%m-%d"),
                SENSOR_CFG[j[0]]["modality"],
            )
            not in processed_keys
        ]
        skipped = before - len(jobs)
        print(f"  Already processed: {len(processed_keys)} unique (tile, date, modality) combos")
        print(f"  Skipping {skipped} scenes, {len(jobs)} remaining")
    else:
        print(f"  No existing data found, processing all {len(jobs)} scenes")

    total_jobs = len(jobs)
    print(f"Workers: {args.num_workers}")

    if total_jobs == 0:
        print("\nNothing to process — all scenes already in Milvus!")
        sys.exit(0)

    # ---- Phase 2: Process all scenes in parallel ----
    print(f"\n{'=' * 60}")
    print(f"Phase 2: Processing scenes ({args.num_workers} workers)")
    print(f"  Embeddings -> {args.collection}")
    print(f"  Tokens     -> {args.token_collection}")
    print(f"{'=' * 60}\n")

    t0 = time.time()
    total_emb_rows = 0
    total_tok_rows = 0
    failed_jobs = []
    scenes_since_flush = 0
    flush_every = 50  # flush every N scenes to make data queryable

    with ThreadPoolExecutor(max_workers=args.num_workers) as executor:
        futures = {executor.submit(process_single_scene, job): job for job in jobs}

        with tqdm(
            total=total_jobs,
            desc="Processing",
            unit="scene",
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {postfix}",
        ) as pbar:
            for future in as_completed(futures):
                job_label, success, n_emb, n_tok, msg = future.result()

                if success:
                    total_emb_rows += n_emb
                    total_tok_rows += n_tok
                    scenes_since_flush += 1
                    pbar.set_postfix(
                        emb=total_emb_rows,
                        tok=total_tok_rows,
                        fail=len(failed_jobs),
                        last=job_label,
                    )
                else:
                    failed_jobs.append((job_label, msg))
                    pbar.set_postfix(
                        emb=total_emb_rows,
                        tok=total_tok_rows,
                        fail=len(failed_jobs),
                        last=f"FAIL:{job_label}",
                    )
                    tqdm.write(f"  FAILED: {job_label}: {msg}")

                pbar.update(1)

                # Periodic flush so data becomes queryable
                if scenes_since_flush >= flush_every and _milvus_connected:
                    try:
                        _emb_coll.flush()
                        _tok_coll.flush()
                        scenes_since_flush = 0
                    except Exception:
                        pass

    # ---- Flush ----
    if _milvus_connected:
        tqdm.write("Flushing Milvus collections...")
        _emb_coll.flush()
        _tok_coll.flush()
        tqdm.write(f"  {args.collection}: {_emb_coll.num_entities} total rows")
        tqdm.write(f"  {args.token_collection}: {_tok_coll.num_entities} total rows")

    # ---- Summary ----
    elapsed = time.time() - t0
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = int(elapsed % 60)

    print(f"\n{'=' * 60}")
    print(f"DONE in {hours}h {minutes}m {seconds}s")
    print(f"  STAC total: {total_stac} scenes")
    print(f"  Skipped:    {total_stac - total_jobs} (already in Milvus)")
    print(f"  Processed:  {total_jobs - len(failed_jobs)}/{total_jobs} succeeded")
    print(f"  Embeddings: {total_emb_rows} rows -> {args.collection}")
    print(f"  Tokens:     {total_tok_rows} rows -> {args.token_collection}")
    if elapsed > 0:
        print(f"  Rate:       {(total_jobs - len(failed_jobs)) / elapsed * 3600:.0f} scenes/hr")

    if failed_jobs:
        print(f"\nFailed ({len(failed_jobs)}):")
        for label, msg in failed_jobs[:20]:
            print(f"  {label}: {msg}")
        if len(failed_jobs) > 20:
            print(f"  ... and {len(failed_jobs) - 20} more")

    print(f"{'=' * 60}")
    sys.exit(0 if not failed_jobs else 1)


if __name__ == "__main__":
    main()
