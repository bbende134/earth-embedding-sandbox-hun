import logging
import os

import fsspec
import numpy as np
import xarray as xr
from dask.array.core import slices_from_chunks
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
from tqdm import tqdm

logger = logging.getLogger()

ZARR_ROOT = os.environ["ZARR_ROOT"]  # e.g. "gcs://my-bucket/embeddings.zarr"
PROJECT = os.environ["GCP_PROJECT_ID"]
EMB_DIM = 64
METRIC_TYPE = os.getenv("METRIC_TYPE", "IP")  # or "L2"
COLLECTION = "geo_embeddings"
ZOOM_PYRAMID_LEVELS = [8, 16, 32, 64, 128, 256]

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
COLLECTION = os.getenv("COLLECTION", "geo_embeddings")
BATCH = int(os.getenv("BATCH", "8192"))


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


def reshape_and_insert(block, col, z):
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
        .dropna()
        .apply(lambda r: r.tolist(), axis=1)
        .reset_index()
        .rename(columns={0: "embedding", "X": "lon", "Y": "lat"})
    )
    df["z"] = z

    # print (df)

    records = df.to_dict(orient="records")

    try:
        col.insert(records)
        col.flush()
        return True
    except Exception as e:
        logger.error(f"Error inserting records into Milvus: {e}")
        print(df["embedding"].apply(lambda x: all(np.isnan(np.array(x)))))
        print(df)
        raise e


def main():
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    col = ensure_collection()
    col.load()
    logger.info("got collection")

    for level in ZOOM_PYRAMID_LEVELS:
        logger.info(f"doing zoom level {level}")
        zx = xr.open_zarr(ZARR_ROOT + f"_z{level}")

        for sl_tuple in tqdm(list(slices_from_chunks(zx["embeddings"].data.chunks))):
            isel_dict = dict(zip(zx["embeddings"].dims, sl_tuple, strict=False))
            # check intersection with geometry before bothering to insert
            reshape_and_insert(zx["embeddings"].isel(isel_dict), col=col, z=level)


if __name__ == "__main__":
    main()
