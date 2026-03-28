"""
Unified per-scene tile processor.
Downloads one scene at a time, processes through TerraMind, inserts into Milvus, verifies.
No intermediate GeoTIFFs — everything stays in memory per-scene.

Usage:
    uv run python hungary_full_pipeline/process_tile.py \
        --tile-idx 0 --year 2017 --geojson budapest.geojson

    # Specific sensors
    uv run python hungary_full_pipeline/process_tile.py \
        --tile-idx 42 --year 2021 --sensors s2l2a

    # Dry run (no Milvus, no API)
    uv run python hungary_full_pipeline/process_tile.py \
        --tile-idx 0 --year 2021 --dry-run
"""

import argparse
import sys
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
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from rasterio.transform import xy as transform_xy
from shapely.geometry import box

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
API_URL = "http://192.168.242.180:8000"
PATCH_SIZE = 224
EMBEDDING_DIM = 1024
TOKEN_DIM = 196
MAX_WORKERS = 8
TARGET_RES_DEG = 10 / 111320

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

NDVI_B8_IDX = 7
NDVI_B4_IDX = 3
NO_NDVI = -999.0


# ---------------------------------------------------------------------------
# Grid
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


# ---------------------------------------------------------------------------
# STAC query
# ---------------------------------------------------------------------------


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
# Download one scene's bands into memory
# ---------------------------------------------------------------------------


def download_scene_bands(sensor, item, target_transform, target_w, target_h, target_crs):
    """Download all bands for one STAC item. Returns (date_str, np.ndarray (n_bands, H, W))."""
    cfg = SENSOR_CFG[sensor]
    date_str = item.datetime.strftime("%Y-%m-%d")
    bands = []

    env = rasterio.Env(
        GDAL_HTTP_TIMEOUT=30,
        GDAL_HTTP_MAX_RETRY=3,
        GDAL_HTTP_RETRY_DELAY=5,
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
    )

    for asset_key, _band_name in zip(cfg["assets"], cfg["band_names"], strict=False):
        if asset_key not in item.assets:
            bands.append(np.zeros((target_h, target_w), dtype=np.float32))
            continue
        url = planetary_computer.sign(item.assets[asset_key].href)
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


# ---------------------------------------------------------------------------
# TerraMind API
# ---------------------------------------------------------------------------


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


def call_infer(modality_key, tif_bytes, api_url):
    resp = requests.post(
        f"{api_url}/infer",
        data=[("modality", modality_key)],
        files=[("file", (f"{modality_key}.tif", tif_bytes, "image/tiff"))],
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["modalities"][modality_key]


def process_patch(
    patch_row,
    patch_col,
    date_data,
    src_transform,
    src_crs,
    sensor,
    modality_key,
    date_str,
    tile_id,
    api_url,
):
    row_off = patch_row * PATCH_SIZE
    col_off = patch_col * PATCH_SIZE
    patch = date_data[:, row_off : row_off + PATCH_SIZE, col_off : col_off + PATCH_SIZE]

    if (patch == 0.0).mean() > 0.5:
        return None

    patch_transform = src_transform * Affine.translation(col_off, row_off)
    lon, lat = transform_xy(src_transform, row_off + PATCH_SIZE // 2, col_off + PATCH_SIZE // 2)

    ndvi = NO_NDVI
    if sensor == "s2l2a":
        b8 = patch[NDVI_B8_IDX].astype(np.float32)
        b4 = patch[NDVI_B4_IDX].astype(np.float32)
        denom = b8 + b4
        valid = denom > 0
        if valid.any():
            ndvi_arr = np.where(valid, (b8 - b4) / np.where(valid, denom, 1.0), 0.0)
            ndvi = float(ndvi_arr[valid].mean())

    tif_bytes = bands_to_tif_bytes(patch.astype(np.float32), patch_transform, src_crs)
    mod_data = call_infer(modality_key, tif_bytes, api_url)

    if mod_data.get("embeddings") is None:
        return None

    emb = np.array(mod_data["embeddings"])
    vector = emb[0].mean(axis=0).tolist()

    tokens = None
    if mod_data.get("tokens") is not None:
        tokens = np.array(mod_data["tokens"]).flatten().tolist()

    return {
        "lat": float(lat),
        "lon": float(lon),
        "vector": vector,
        "modality": modality_key,
        "tokens": tokens,
        "date_start": date_str,
        "date_end": date_str,
        "tile_id": tile_id,
        "patch_row": patch_row,
        "patch_col": patch_col,
        "ndvi": ndvi,
    }


# ---------------------------------------------------------------------------
# Milvus
# ---------------------------------------------------------------------------


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
    print(f"  Created collection: {name}")
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
    print(f"  Created collection: {name}")
    return coll


def insert_rows(emb_coll, tok_coll, rows, batch_size=500):
    """Insert embedding + token rows into Milvus in batches."""
    has_ndvi = any(f.name == "ndvi" for f in emb_coll.schema.fields)
    total_emb = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        entities = [
            [r["vector"] for r in batch],
            [r["lat"] for r in batch],
            [r["lon"] for r in batch],
            [r["date_start"] for r in batch],
            [r["date_end"] for r in batch],
            [r["modality"] for r in batch],
            [r["tile_id"] for r in batch],
            [r["patch_row"] for r in batch],
            [r["patch_col"] for r in batch],
        ]
        if has_ndvi:
            entities.append([r["ndvi"] for r in batch])
        emb_ids = emb_coll.insert(entities).primary_keys
        total_emb += len(emb_ids)

        # Tokens
        tok_rows = [
            (r, eid) for r, eid in zip(batch, emb_ids, strict=False) if r["tokens"] is not None
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

    return total_emb


def verify_tile(emb_coll, tile_id):
    """Check that at least one row exists for this tile_id."""
    emb_coll.flush()
    res = emb_coll.query(expr=f'tile_id == "{tile_id}"', output_fields=["id"], limit=1)
    return len(res) > 0


# ---------------------------------------------------------------------------
# Process one scene for one sensor
# ---------------------------------------------------------------------------


def process_scene(
    sensor,
    item,
    tile_idx,
    tile,
    emb_coll,
    tok_coll,
    target_transform,
    target_w,
    target_h,
    target_crs,
    api_url,
    max_workers,
    dry_run,
):
    """Download, process, insert, verify one scene. Returns (n_rows, n_failed)."""
    cfg = SENSOR_CFG[sensor]
    tile_id = f"{sensor}_{item.datetime.year}_tile_{tile_idx:03d}"
    date_str, scene_data = download_scene_bands(
        sensor, item, target_transform, target_w, target_h, target_crs
    )

    # Pad to PATCH_SIZE multiples
    _, h, w = scene_data.shape
    pad_h = (PATCH_SIZE - h % PATCH_SIZE) % PATCH_SIZE
    pad_w = (PATCH_SIZE - w % PATCH_SIZE) % PATCH_SIZE
    if pad_h or pad_w:
        scene_data = np.pad(
            scene_data, ((0, 0), (0, pad_h), (0, pad_w)), mode="constant", constant_values=0.0
        )

    n_rows = scene_data.shape[1] // PATCH_SIZE
    n_cols = scene_data.shape[2] // PATCH_SIZE
    patch_coords = [(r, c) for r in range(n_rows) for c in range(n_cols)]

    rows = []
    failed = 0

    if dry_run:
        return len(patch_coords), 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                process_patch,
                r,
                c,
                scene_data,
                target_transform,
                target_crs,
                sensor,
                cfg["modality"],
                date_str,
                tile_id,
                api_url,
            ): (r, c)
            for r, c in patch_coords
        }
        for future in as_completed(futures):
            r, c = futures[future]
            try:
                row = future.result()
                if row:
                    rows.append(row)
            except Exception as e:
                failed += 1
                print(f"    Patch ({r},{c}) failed: {e}")

    if rows:
        insert_rows(emb_coll, tok_coll, rows)

    del scene_data
    return len(rows), failed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Per-scene tile processor")
    parser.add_argument("--tile-idx", type=int, required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--geojson", default="budapest.geojson")
    parser.add_argument("--sensors", default="s2l2a,s1grd")
    parser.add_argument("--cloud-max", type=int, default=80)
    parser.add_argument("--collection", default="dyn_terra")
    parser.add_argument("--token-collection", default="dyn_terra_tokens")
    parser.add_argument("--api-url", default=API_URL)
    parser.add_argument("--milvus-host", default="localhost")
    parser.add_argument("--milvus-port", default="19530")
    parser.add_argument("--max-workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sensors = [s.strip() for s in args.sensors.split(",")]
    tiles = create_grid(args.geojson)

    if args.tile_idx < 0 or args.tile_idx >= len(tiles):
        print(f"Error: tile-idx {args.tile_idx} out of range [0, {len(tiles) - 1}]")
        sys.exit(1)

    tile = tiles[args.tile_idx]
    bbox = list(tile.bounds)
    date_start = f"{args.year}-01-01"
    date_end = f"{args.year}-12-31"

    print(f"Tile {args.tile_idx:03d}  bbox={[round(b, 4) for b in bbox]}")
    print(f"Year {args.year}  sensors={sensors}")

    # Target grid
    minx, miny, maxx, maxy = bbox
    target_w = max(PATCH_SIZE, round((maxx - minx) / TARGET_RES_DEG))
    target_h = max(PATCH_SIZE, round((maxy - miny) / TARGET_RES_DEG))
    target_transform = from_bounds(minx, miny, maxx, maxy, target_w, target_h)
    target_crs = "EPSG:4326"
    print(f"Grid: {target_w}x{target_h}px")

    # Milvus connection
    emb_coll = tok_coll = None
    if not args.dry_run:
        for attempt in range(5):
            try:
                connections.connect(
                    "default", host=args.milvus_host, port=args.milvus_port, timeout=10
                )
                emb_coll = get_or_create_embedding_collection(args.collection)
                tok_coll = get_or_create_token_collection(args.token_collection)
                break
            except Exception as e:
                print(f"  Milvus connection attempt {attempt + 1}/5 failed: {e}")
                time.sleep(10)
        else:
            print("ERROR: Could not connect to Milvus after 5 attempts")
            sys.exit(1)

    total_rows = 0
    total_failed = 0

    for sensor in sensors:
        print(f"\n--- {sensor} ---")
        items = query_items(sensor, bbox, date_start, date_end, args.cloud_max)
        print(f"  {len(items)} scenes")

        if not items:
            continue

        for i, item in enumerate(items):
            scene_label = f"  [{i + 1}/{len(items)}] {item.datetime.date()}"
            t0 = time.time()

            try:
                n_rows, n_failed = process_scene(
                    sensor,
                    item,
                    args.tile_idx,
                    tile,
                    emb_coll,
                    tok_coll,
                    target_transform,
                    target_w,
                    target_h,
                    target_crs,
                    args.api_url,
                    args.max_workers,
                    args.dry_run,
                )
                elapsed = time.time() - t0
                total_rows += n_rows
                total_failed += n_failed
                print(f"{scene_label}  +{n_rows} rows  ({elapsed:.1f}s)")
            except Exception as e:
                elapsed = time.time() - t0
                total_failed += 1
                print(f"{scene_label}  FAILED ({elapsed:.1f}s): {e}")

    # Final flush + verify
    if not args.dry_run and total_rows > 0:
        emb_coll.flush()
        tok_coll.flush()
        tile_id = f"{sensors[0]}_{args.year}_tile_{args.tile_idx:03d}"
        verified = verify_tile(emb_coll, tile_id)
        status = "VERIFIED" if verified else "NOT FOUND (may still be indexing)"
        print(f"\n{status} in Milvus")
        print(f"Totals: {total_rows} rows inserted, {total_failed} failed")
        print(
            f"  {args.collection}: {emb_coll.num_entities} | {args.token_collection}: {tok_coll.num_entities}"
        )
    else:
        print(f"\nDone. {total_rows} rows, {total_failed} failed")


if __name__ == "__main__":
    main()
