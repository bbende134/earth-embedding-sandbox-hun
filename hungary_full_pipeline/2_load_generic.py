"""
Generic loader script for Milvus.
Loads a processed Zarr archive into a specified collection.
"""

import argparse

import numpy as np
import xarray as xr
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, required=True, help="Path to processed Zarr archive")
    parser.add_argument("--collection", type=str, required=True, help="Milvus collection name")
    parser.add_argument("--host", type=str, default="localhost")
    parser.add_argument("--port", type=str, default="19530")
    parser.add_argument("--batch_size", type=int, default=10000)
    args = parser.parse_args()

    print(f"Connecting to Milvus at {args.host}:{args.port}...")
    connections.connect("default", host=args.host, port=args.port)

    # Create collection if not exists
    if not utility.has_collection(args.collection):
        print(f"Creating collection {args.collection}...")
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=64),
            FieldSchema(name="lat", dtype=DataType.FLOAT),
            FieldSchema(name="lon", dtype=DataType.FLOAT),
            FieldSchema(name="year", dtype=DataType.INT64),
            FieldSchema(name="z", dtype=DataType.INT64),
        ]
        schema = CollectionSchema(fields, "Hungary High Res Embeddings")
        coll = Collection(args.collection, schema)

        # Create index
        print("Creating index...")
        index_params = {"metric_type": "L2", "index_type": "IVF_FLAT", "params": {"nlist": 1024}}
        coll.create_index(field_name="vector", index_params=index_params)
    else:
        coll = Collection(args.collection)
        print(f"Using existing collection: {args.collection}")

    # Load Zarr
    print(f"Loading {args.input}...")
    ds = xr.open_zarr(args.input)

    # Check for zoom levels in attributes, default to [16] if not found (base level)
    zoom_levels = ds.attrs.get("zoom_levels", [16])
    if isinstance(zoom_levels, int):
        zoom_levels = [zoom_levels]

    print(f"Found zoom levels: {zoom_levels}")

    for z in zoom_levels:
        print(f"Processing zoom level {z}...")

        # Handle group structure if present (e.g. if 2_reduce.py creates groups)
        # If the input zarr has groups like 'z16', 'z32', etc.
        try:
            ds_z = xr.open_zarr(args.input, group=f"z{z}")
        except Exception:
            # Fallback: assume root dataset is the only level (likely z16/base)
            if z == 16 or len(zoom_levels) == 1:
                ds_z = ds
            else:
                print(f"Warning: Could not open group z{z}, skipping.")
                continue

        # Extract data
        # Check for 'embeddings' variable (consolidated format) or A00...A63 (raw format)
        if "embeddings" in ds_z:
            print("Found 'embeddings' variable. Using consolidated format.")
            # Shape is (features, time, Y, X) or (time, Y, X, features) depending on how it was saved
            # Based on consolidate.py: (features, time, Y, X)

            da = ds_z["embeddings"]
            # We want (time, Y, X, features)
            if da.dims[0] == "features":
                da = da.transpose(..., "features")

        else:
            # Check if we have A00...A63
            band_vars = [f"A{i:02d}" for i in range(64)]
            if not all(v in ds_z for v in band_vars):
                print(f"Error: Missing band variables in z{z}. Skipping.")
                continue

            # Stack bands into a 'vector' dimension
            da = ds_z.to_array(dim="band").transpose(..., "band")

        # Now shape is (..., 64)

        # We only want valid data (not NaN)

        # We only want valid data (not NaN)
        # This is tricky with dask lazy loading.

        # Simplified approach for now: Load into memory if it fits, or chunk.
        # 50km x 50km x 64 floats = ~600MB. It fits in memory easily.

        print("Loading data into memory...")
        try:
            data = da.compute()  # Load into numpy
        except Exception as e:
            print(f"Error loading data: {e}")
            continue

        # Flatten: (time*Y*X, 64)
        vectors = data.values.reshape(-1, 64)

        # Get coordinates
        # We need lat/lon for each pixel.
        # X and Y are coordinates in the dataset
        X = ds_z.coords["X"].values
        Y = ds_z.coords["Y"].values

        # Create meshgrid
        XX, YY = np.meshgrid(X, Y)
        lons = XX.flatten()
        lats = YY.flatten()

        # Filter NaNs
        valid_mask = ~np.isnan(vectors).any(axis=1)

        valid_vectors = vectors[valid_mask]
        valid_lats = lats[valid_mask]
        valid_lons = lons[valid_mask]

        count = len(valid_vectors)
        print(f"Found {count} valid vectors.")

        if count == 0:
            continue

        # Batch insert
        for i in range(0, count, args.batch_size):
            end = min(i + args.batch_size, count)
            batch_vectors = valid_vectors[i:end].tolist()
            batch_lats = valid_lats[i:end].tolist()
            batch_lons = valid_lons[i:end].tolist()

            entities = [
                batch_vectors,
                batch_lats,
                batch_lons,
                [2021] * (end - i),  # Year
                [z] * (end - i),  # Zoom
            ]

            coll.insert(entities)
            print(f"Inserted batch {i}-{end}/{count}", end="\r")

        print(f"\nFinished zoom level {z}")

    # Flush
    coll.flush()
    print(f"Done. Collection {args.collection} row count: {coll.num_entities}")


if __name__ == "__main__":
    main()
