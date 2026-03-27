"""
Step 2: Process a single-sensor GeoTIFF through TerraMind API and load into Milvus.

Filename encodes the sensor and acquisition date:
  s2l2a_{YYYY-MM-DD}_tile_{i:03d}.tif  ->  untok_sen2l2a@224  (12 bands: B1..B12)
  s1grd_{YYYY-MM-DD}_tile_{i:03d}.tif  ->  untok_sen1grd@224  ( 2 bands: VV, VH)

S2 band order (TerraMind expects exactly this sequence):
  0: B1  (Coastal Aerosol 60m)
  1: B2  (Blue 10m)
  2: B3  (Green 10m)
  3: B4  (Red 10m)
  4: B5  (Vegetation Red Edge 1 20m)
  5: B6  (Vegetation Red Edge 2 20m)
  6: B7  (Vegetation Red Edge 3 20m)
  7: B8  (NIR 10m)
  8: B8A (Narrow NIR 20m)
  9: B9  (Water Vapor 60m)
  10: B11 (SWIR 1 20m)
  11: B12 (SWIR 2 20m)

NDVI (B8-B4)/(B8+B4) is derived from S2 bands and stored as a mean-patch scalar.
VQ-VAE tokens (when returned) are stored in dyn_terra_tokens.
"""

import argparse
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import rasterio
import requests
from affine import Affine
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
from rasterio.io import MemoryFile
from rasterio.transform import xy as transform_xy

API_URL = "http://192.168.242.180:8000"
PATCH_SIZE = 224
MAX_WORKERS = 8
EMBEDDING_DIM = 1024

# Sensor name (from filename prefix) → API modality key
SENSOR_MODALITY = {
    "s2l2a": "untok_sen2l2a@224",
    "s1grd": "untok_sen1grd@224",
}

# S2 band indices for NDVI: B8=index 7, B4=index 3
NDVI_B8_IDX = 7
NDVI_B4_IDX = 3

NO_NDVI = -999.0  # sentinel for non-S2 rows

# Threshold for skipping zero-padded patches (>50% zeros)
ZERO_PADDING_THRESHOLD = 0.5

# Number of dimensions for 2D arrays
NDIM_2D = 2


# ---------------------------------------------------------------------------
# Milvus schema
# ---------------------------------------------------------------------------


def get_or_create_embedding_collection(name):
    """One row per (patch x modality). Includes ndvi scalar for S2 rows."""
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
        FieldSchema("ndvi", DataType.FLOAT),  # mean NDVI; NO_NDVI for non-S2
    ]
    schema = CollectionSchema(fields, "TerraMind embeddings - one row per patch per modality")
    coll = Collection(name, schema)
    coll.create_index(
        "vector", {"metric_type": "COSINE", "index_type": "IVF_FLAT", "params": {"nlist": 1024}}
    )
    coll.create_index("modality", {"index_type": "Trie"})
    print(f"Created collection: {name}")
    return coll


TOKEN_DIM = 196  # 14x14 VQ-VAE grid, flattened


def get_or_create_token_collection(name):
    """One row per (patch x tokenizer-modality). embedding_id -> dyn_terra.id."""
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
    schema = CollectionSchema(
        fields, "TerraMind VQ-VAE token grids - one row per patch per tokenizer modality"
    )
    coll = Collection(name, schema)
    coll.create_index(
        "tokens", {"metric_type": "L2", "index_type": "IVF_FLAT", "params": {"nlist": 512}}
    )
    coll.create_index("embedding_id", {"index_type": "STL_SORT"})
    coll.create_index("modality", {"index_type": "Trie"})
    print(f"Created collection: {name}")
    return coll


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------


def parse_filename(path: str) -> tuple[str, str]:
    """Returns (sensor, acquisition_date) from filename like s2l2a_2021-05-15_tile_003.tif."""
    name = os.path.basename(path)
    m = re.match(r"^(s2l2a|s1grd)_(\d{4}-\d{2}-\d{2})_tile_\d+", name)
    if not m:
        raise ValueError(
            f"Cannot parse sensor/date from filename: {name}\n"
            "Expected format: {{sensor}}_{{YYYY-MM-DD}}_tile_{{NNN}}.tif"
        )
    return m.group(1), m.group(2)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def bands_to_tif_bytes(band_data: np.ndarray, transform: Affine, crs) -> bytes:
    if band_data.ndim == NDIM_2D:
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


def call_infer_single(modality_key: str, tif_bytes: bytes, api_url: str) -> dict:
    """POST a single modality to /infer. Returns that modality's result dict."""
    resp = requests.post(
        f"{api_url}/infer",
        data=[("modality", modality_key)],
        files=[("file", (f"{modality_key}.tif", tif_bytes, "image/tiff"))],
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["modalities"][modality_key]


# ---------------------------------------------------------------------------
# Per-patch processing (runs in thread pool)
# ---------------------------------------------------------------------------


def process_patch(
    patch_row: int,
    patch_col: int,
    src_data: np.ndarray,
    src_transform: Affine,
    src_crs,
    sensor: str,
    modality_key: str,
    date_str: str,
    tile_id: str,
    api_url: str,
) -> dict | None:
    """
    Returns a result dict for one patch, or None if the patch is mostly NoData.
    """
    row_off = patch_row * PATCH_SIZE
    col_off = patch_col * PATCH_SIZE

    patch = src_data[:, row_off : row_off + PATCH_SIZE, col_off : col_off + PATCH_SIZE]

    # Skip patches that are majority zero-padding
    if (patch == 0.0).mean() > ZERO_PADDING_THRESHOLD:
        return None

    patch_transform = src_transform * Affine.translation(col_off, row_off)
    lon, lat = transform_xy(src_transform, row_off + PATCH_SIZE // 2, col_off + PATCH_SIZE // 2)

    # Compute NDVI for S2 patches
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
    mod_data = call_infer_single(modality_key, tif_bytes, api_url)

    if mod_data.get("embeddings") is None:
        return None

    emb = np.array(mod_data["embeddings"])  # (1, 196, 1024)
    vector = emb[0].mean(axis=0).tolist()  # mean-pool spatial dim → (1024,)

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
# Milvus insert helpers
# ---------------------------------------------------------------------------


def _has_ndvi_field(coll: Collection) -> bool:
    return any(f.name == "ndvi" for f in coll.schema.fields)


def insert_embeddings(coll: Collection, rows: list[dict]) -> list[int]:
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
    if _has_ndvi_field(coll):
        entities.append([r["ndvi"] for r in rows])
    return coll.insert(entities).primary_keys


def insert_tokens(coll: Collection, rows: list[dict], embedding_ids: list[int]):
    tok_rows = [
        (r, eid) for r, eid in zip(rows, embedding_ids, strict=False) if r["tokens"] is not None
    ]
    if not tok_rows:
        return
    entities = [
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
    coll.insert(entities)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to sensor GeoTIFF")
    parser.add_argument("--api-url", default=API_URL)
    parser.add_argument("--collection", default="dyn_terra")
    parser.add_argument("--token-collection", default="dyn_terra_tokens")
    parser.add_argument("--max-workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--milvus-host", default="localhost")
    parser.add_argument("--milvus-port", default="19530")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sensor, date_str = parse_filename(args.input)
    modality_key = SENSOR_MODALITY[sensor]
    tile_id = os.path.basename(args.input).replace(".tif", "")

    print(f"File:     {args.input}")
    print(f"Sensor:   {sensor}  →  {modality_key}")
    print(f"Date:     {date_str}")
    print(f"Tile ID:  {tile_id}")

    if not args.dry_run:
        print(f"Connecting to Milvus at {args.milvus_host}:{args.milvus_port}...")
        connections.connect("default", host=args.milvus_host, port=args.milvus_port)
        emb_coll = get_or_create_embedding_collection(args.collection)
        tok_coll = get_or_create_token_collection(args.token_collection)

    print(f"Opening {args.input}...")
    with rasterio.open(args.input) as src:
        print(f"  {src.width}x{src.height}px, {src.count} bands, CRS: {src.crs}")
        src_data = src.read().astype(np.float32)
        src_transform = src.transform
        src_crs = src.crs

    # Pad to next multiple of PATCH_SIZE so edge patches aren't dropped
    _, h, w = src_data.shape
    pad_h = (PATCH_SIZE - h % PATCH_SIZE) % PATCH_SIZE
    pad_w = (PATCH_SIZE - w % PATCH_SIZE) % PATCH_SIZE
    if pad_h or pad_w:
        src_data = np.pad(
            src_data, ((0, 0), (0, pad_h), (0, pad_w)), mode="constant", constant_values=0.0
        )

    n_rows = src_data.shape[1] // PATCH_SIZE
    n_cols = src_data.shape[2] // PATCH_SIZE
    patch_coords = [(r, c) for r in range(n_rows) for c in range(n_cols)]
    print(f"  {n_rows}x{n_cols} = {len(patch_coords)} patches")

    all_rows: list[dict] = []
    failed = 0

    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(
                process_patch,
                r,
                c,
                src_data,
                src_transform,
                src_crs,
                sensor,
                modality_key,
                date_str,
                tile_id,
                args.api_url,
            ): (r, c)
            for r, c in patch_coords
        }
        for i, future in enumerate(as_completed(futures)):
            r, c = futures[future]
            try:
                row = future.result()
                if row:
                    all_rows.append(row)
            except Exception as e:
                failed += 1
                print(f"\nPatch ({r},{c}) failed: {e}")
            if (i + 1) % 50 == 0 or (i + 1) == len(futures):
                print(f"  {i + 1}/{len(futures)} patches, {len(all_rows)} rows so far", end="\r")

    print(f"\n  Done: {len(all_rows)} rows ({failed} patches failed)")

    if args.dry_run:
        print("[dry-run] Skipping Milvus insert.")
        return

    print("Inserting into Milvus...")
    for i in range(0, len(all_rows), args.batch_size):
        batch = all_rows[i : i + args.batch_size]
        emb_ids = insert_embeddings(emb_coll, batch)
        insert_tokens(tok_coll, batch, emb_ids)
        print(f"  {min(i + args.batch_size, len(all_rows))}/{len(all_rows)}", end="\r")

    emb_coll.flush()
    tok_coll.flush()
    print(
        f"\n✓ {args.collection}: {emb_coll.num_entities} rows | "
        f"{args.token_collection}: {tok_coll.num_entities} rows"
    )


if __name__ == "__main__":
    main()
