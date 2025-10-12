import logging
import os

import fsspec
import numpy as np
import xarray as xr
from dask.array.core import slices_from_chunks
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
from tqdm import tqdm

logger = logging.getLogger()

ZARR_ROOT = os.environ["reduced_archive"]  # e.g. "gcs://my-bucket/embeddings.zarr" # noqa: SIM112
PROJECT = os.environ["GCP_PROJECT_ID"]
EMB_DIM = 64
METRIC_TYPE = os.getenv("METRIC_TYPE", "IP")  # or "L2"
ZOOM_PYRAMID_LEVELS = [16, 32, 64, 128, 256]

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
COLLECTION = os.getenv("COLLECTION", "geo_embeddings")
BATCH = int(os.getenv("BATCH", "32768"))  # 32k * 64*4 bytes =~ 8MB


def ensure_collection():
    if utility.has_collection(COLLECTION):
        return Collection(COLLECTION)

    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="lat", dtype=DataType.FLOAT),
        FieldSchema(name="lon", dtype=DataType.FLOAT),
        FieldSchema(name="z", dtype=DataType.INT16),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMB_DIM),
    ]
    schema = CollectionSchema(fields, description="64D climate/sat embeddings keyed by z/lat/lon")
    col = Collection(name=COLLECTION, schema=schema)

    # Default IVF_FLAT index
    col.create_index(
        field_name="embedding",
        index_params={
            "index_type": "IVF_FLAT",
            "metric_type": METRIC_TYPE,
            "params": {"nlist": 4096},
        },
    )
    col.load()
    return col


def gcsfs():
    return fsspec.filesystem("gcs", project=PROJECT)


def reshape(block, z):
    """
    kwargs:
        col: the milvus collection to insert to
        z: the zoom level
    """

    df = (
        block.squeeze("time")
        .stack(xy=("X", "Y"))
        .transpose("xy", "features")
        .to_pandas()
        .fillna(0)  # fill NaN with 0
        .dropna()  # in case lat/lon are NaN, but they shouldn't be
    )
    print(f"Before dropna: {len(df)} records, has na: {df.isna().any().any()}")
    df = df.dropna()
    print(f"After dropna: {len(df)} records")
    df = (
        df.apply(lambda r: np.asarray(r.tolist(), dtype=np.float32, order="C"), axis=1)
        .reset_index()
        .rename(columns={0: "embedding", "X": "lon", "Y": "lat"})
    )
    print(f"Reshaped {len(df)} records for z={z}")
    df["z"] = z

    records = df.to_dict(orient="records")

    return records


def main():
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    col = ensure_collection()
    col.load()
    logger.info("got collection")

    records = []

    for level in ZOOM_PYRAMID_LEVELS:
        logger.info(f"doing zoom level {level}")
        zx = xr.open_zarr(ZARR_ROOT + f"_z{level}")

        for sl_tuple in tqdm(list(slices_from_chunks(zx["embeddings"].data.chunks))):
            isel_dict = dict(zip(zx["embeddings"].dims, sl_tuple, strict=False))
            # check intersection with geometry before bothering to insert
            records += reshape(zx["embeddings"].isel(isel_dict), z=level)

            if len(records) >= BATCH:
                logger.info(f"Inserting {len(records)} records into Milvus")
                col.insert(records)
                col.flush()
                records = []

    if records:
        logger.info(f"Inserting remaining {len(records)} records into Milvus")
        col.insert(records)
        col.flush()


if __name__ == "__main__":
    main()
