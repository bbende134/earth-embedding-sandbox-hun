import logging
import os

import numpy as np
import xarray as xr
from dask.array.core import slices_from_chunks
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
from tqdm import tqdm

# Configuration
ZARR_PATH = "budapest_2024_reduced.zarr"
COLLECTION = "hungary_embeddings"
MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
BATCH = 10000
ZOOM_LEVELS = [8, 16, 32, 64, 128, 256]  # z8 is produced by consolidate, others by reduce

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def ensure_collection():
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    if utility.has_collection(COLLECTION):
        col = Collection(COLLECTION)
        print(f"Collection {COLLECTION} exists.")
        return col
    else:
        print(f"Collection {COLLECTION} does not exist. Creating...")
        # Define schema based on 3_load_into_db.py
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="lat", dtype=DataType.FLOAT),
            FieldSchema(name="lon", dtype=DataType.FLOAT),
            FieldSchema(name="z", dtype=DataType.INT16),
            FieldSchema(name="year", dtype=DataType.INT16),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=64),
        ]
        schema = CollectionSchema(fields, description="64D climate/sat embeddings")
        col = Collection(name=COLLECTION, schema=schema)
        col.create_index(
            field_name="embedding",
            index_params={"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 4096}},
        )
        col.load()
        return col


def reshape(block, z, year):
    # Logic adapted from pipeline/3_load_into_db.py
    df = (
        block.squeeze("time")
        .stack(xy=("X", "Y"))
        .transpose("xy", "features")
        .to_pandas()
        .fillna(0)
        .dropna()
    )

    if df.empty:
        return []

    df = df.reset_index()
    if "xy" in df.columns:
        df[["X", "Y"]] = df["xy"].apply(
            lambda x: pd.Series([x[0], x[1]] if isinstance(x, tuple) else [x, x])
        )
        df = df.drop("xy", axis=1)

    # Coordinate transformation
    import pyproj

    # Assuming the same UTM zone as extraction: EPSG:32634
    transformer = pyproj.Transformer.from_crs("EPSG:32634", "EPSG:4326", always_xy=True)

    valid_mask = ~(df["X"].isna() | df["Y"].isna())
    if valid_mask.sum() == 0:
        return []

    valid_x = df.loc[valid_mask, "X"].values
    valid_y = df.loc[valid_mask, "Y"].values
    lon_vals, lat_vals = transformer.transform(valid_x, valid_y)

    df.loc[valid_mask, "lon"] = lon_vals
    df.loc[valid_mask, "lat"] = lat_vals
    df = df[valid_mask].copy()

    # Features to embedding
    feature_cols = [
        c for c in df.columns if isinstance(c, int) or (isinstance(c, str) and c.isdigit())
    ]
    df["embedding"] = df[feature_cols].values.tolist()
    df["embedding"] = df["embedding"].apply(lambda x: np.asarray(x, dtype=np.float32))

    df = df[["lat", "lon", "embedding"]].copy()
    df["z"] = z
    df["year"] = year

    return df.to_dict(orient="records")


def main():
    import pandas as pd  # Ensure pandas is available inside reshape if needed, or global

    global pd
    import pandas as pd

    col = ensure_collection()

    year = 2024

    for level in ZOOM_LEVELS:
        zarr_path = f"{ZARR_PATH}_z{level}"
        if not os.path.exists(zarr_path):
            print(f"Zarr path {zarr_path} does not exist. Skipping z{level}.")
            continue

        print(f"Processing z{level} from {zarr_path}...")
        try:
            zx = xr.open_zarr(zarr_path)

            records = []
            for sl_tuple in tqdm(list(slices_from_chunks(zx["embeddings"].data.chunks))):
                isel_dict = dict(zip(zx["embeddings"].dims, sl_tuple, strict=False))
                records += reshape(zx["embeddings"].isel(isel_dict), z=level, year=year)

                if len(records) >= BATCH:
                    print(f"Inserting {len(records)} records...")
                    col.insert(records)
                    records = []

            if records:
                print(f"Inserting remaining {len(records)} records...")
                col.insert(records)

            col.flush()

        except Exception as e:
            print(f"Error processing z{level}: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    main()
