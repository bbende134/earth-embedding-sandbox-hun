import logging
import os

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
        col = Collection(COLLECTION)
        # Check if 'year' field exists
        schema = col.schema
        field_names = [f.name for f in schema.fields]
        if "year" not in field_names:
            print(
                "Schema missing 'year' field. Collection needs to be recreated, but keeping existing data."
            )
            print(
                "Please manually drop and recreate the collection if you want to add the year field."
            )
            # Don't drop automatically - let user decide
            return col
        else:
            print("Collection exists with year field.")
            return col

    print("Creating new collection with year field.")
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="lat", dtype=DataType.FLOAT),
        FieldSchema(name="lon", dtype=DataType.FLOAT),
        FieldSchema(name="z", dtype=DataType.INT16),
        FieldSchema(name="year", dtype=DataType.INT16),  # New: year of the data (e.g., 2024)
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMB_DIM),
    ]
    schema = CollectionSchema(
        fields, description="64D climate/sat embeddings keyed by z/lat/lon/year"
    )
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
    # Set up GCS filesystem with authentication
    import gcsfs
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
        scopes=["https://www.googleapis.com/auth/devstorage.read_write"],
    )
    return gcsfs.GCSFileSystem(token=credentials)


def reshape(block, z, year):
    """
    kwargs:
        col: the milvus collection to insert to
        z: the zoom level
        year: the year of the data
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

    # Extract X and Y coordinates from the xy index
    df = df.reset_index()
    if "xy" in df.columns:
        # xy column contains tuples of (X, Y)
        import pandas as pd

        df[["X", "Y"]] = df["xy"].apply(
            lambda x: pd.Series([x[0], x[1]] if isinstance(x, tuple) else [x, x])
        )
        df = df.drop("xy", axis=1)

    print(f"Columns after reset_index: {list(df.columns)}")
    print(
        f"Sample X,Y values: X={df['X'].iloc[0] if 'X' in df.columns else 'N/A'}, Y={df['Y'].iloc[0] if 'Y' in df.columns else 'N/A'}"
    )

    # Convert UTM coordinates to lat/lon
    try:
        import pyproj

        transformer = pyproj.Transformer.from_crs("EPSG:32633", "EPSG:4326", always_xy=True)
        # Filter out any NaN or invalid coordinates
        valid_mask = ~(df["X"].isna() | df["Y"].isna())
        if valid_mask.sum() == 0:
            print("No valid coordinates to transform")
            return []

        valid_x = df.loc[valid_mask, "X"].values
        valid_y = df.loc[valid_mask, "Y"].values
        lon_vals, lat_vals = transformer.transform(valid_x, valid_y)

        df.loc[valid_mask, "lon"] = lon_vals
        df.loc[valid_mask, "lat"] = lat_vals

        # Remove rows with invalid coordinates
        df = df[valid_mask].copy()
        print(f"After coordinate transformation: {len(df)} records")
    except Exception as e:
        print(f"Error in coordinate transformation: {e}")
        import traceback

        traceback.print_exc()
        # Fallback: keep original coordinates (though they will be wrong)
        df["lon"] = df["X"]
        df["lat"] = df["Y"]

    # Extract features into embedding array
    feature_cols = [
        col
        for col in df.columns
        if isinstance(col, int) or (isinstance(col, str) and col.isdigit())
    ]
    df["embedding"] = df[feature_cols].values.tolist()
    df["embedding"] = df["embedding"].apply(lambda x: np.asarray(x, dtype=np.float32, order="C"))

    # Keep only the columns we need for Milvus
    df = df[["lat", "lon", "embedding"]].copy()

    print(f"Reshaped {len(df)} records for z={z}, year={year}")
    df["z"] = z
    df["year"] = year

    records = df.to_dict(orient="records")

    return records


def main():
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    col = ensure_collection()
    col.load()
    logger.info("got collection")

    # Get all processed areas
    import glob

    processed_geojsons = glob.glob(
        os.path.join(os.path.dirname(__file__), "../processed/*.geojson")
    )
    processed_areas = [os.path.basename(f).replace(".geojson", "") for f in processed_geojsons]

    print(f"Found {len(processed_areas)} processed areas to load")

    # Get years from environment or use default
    years = os.getenv("YEARS", "2017,2018,2019,2020,2021,2022,2023").split(",")
    years = [int(y.strip()) for y in years]

    records = []

    for area in processed_areas:
        for year in years:
            area_archive = f"gs://earth-embeddings-hungary-output/{area}_{year}_embeddings"

            # Check if this specific area/year archive exists before trying to load it
            try:
                fs = gcsfs()
                archive_exists = fs.exists(f"{area_archive}_z16")
                if not archive_exists:
                    logger.info(f"Archive {area}_{year} does not exist, skipping")
                    continue
            except Exception as e:
                logger.warning(f"Could not check if archive exists for {area}_{year}: {e}")
                continue

            logger.info(f"Loading area: {area} for year {year} from {area_archive}")

            for level in ZOOM_PYRAMID_LEVELS:
                try:
                    # Use authenticated GCS filesystem
                    store = fs.get_mapper(area_archive + f"_z{level}")
                    zx = xr.open_zarr(store)
                    logger.info(f"doing zoom level {level} for {area} year {year}")

                    # Use the year from the loop since we know it
                    logger.info(f"Using year {year} from archive name")

                    for sl_tuple in tqdm(
                        list(slices_from_chunks(zx["embeddings"].data.chunks)),
                        desc=f"{area} z{level}",
                    ):
                        isel_dict = dict(zip(zx["embeddings"].dims, sl_tuple, strict=False))
                        # check intersection with geometry before bothering to insert
                        records += reshape(zx["embeddings"].isel(isel_dict), z=level, year=year)

                        if len(records) >= BATCH:
                            logger.info(f"Inserting {len(records)} records into Milvus")
                            col.insert(records)
                            col.flush()
                            records = []
                except FileNotFoundError:
                    logger.warning(
                        f"Zoom level {level} not found for {area} year {year} (area may be too small), skipping"
                    )
                    continue
                except Exception as e:
                    logger.warning(f"Could not load {area} year {year} z{level}: {e}")
                    continue

    if records:
        logger.info(f"Inserting remaining {len(records)} records into Milvus")
        col.insert(records)
        col.flush()


if __name__ == "__main__":
    main()
