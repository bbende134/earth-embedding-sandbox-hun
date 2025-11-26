"""
Step 1: Convert merged GeoTIFF to Zarr format.
This converts the Earth Engine GeoTIFF export to the Zarr format
expected by the consolidation pipeline.
"""

import argparse

import rioxarray as rxr
import xarray as xr


def main():
    parser = argparse.ArgumentParser(description="Convert GeoTIFF to Zarr")
    parser.add_argument(
        "--input", type=str, default="data/budapest_2024.tif", help="Input GeoTIFF path"
    )
    parser.add_argument(
        "--output", type=str, default="budapest_2024_raw.zarr", help="Output Zarr archive path"
    )
    args = parser.parse_args()

    print(f"Loading {args.input}...")
    da = rxr.open_rasterio(args.input, chunks={"band": 1, "x": 1024, "y": 1024})

    print("Dataset loaded:")
    print(f"  Bands: {da.sizes['band']}")
    print(f"  X: {da.sizes['x']}")
    print(f"  Y: {da.sizes['y']}")

    # Rename dimensions to match pipeline expectations
    # GeoTIFF: band, y, x → Pipeline: time, Y, X with bands as variables
    da = da.rename({"band": "features", "y": "Y", "x": "X"})

    # Add time dimension (single timestep)
    da = da.expand_dims({"time": [0]})

    # Assign feature names A00-A63
    da = da.assign_coords(features=[f"A{i:02d}" for i in range(da.sizes["features"])])

    # Cast to float32 to save space (source is float64)
    print("Casting to float32...")
    da = da.astype("float32")

    # Convert to Dataset with individual band variables
    print("\nConverting to Dataset format...")
    ds_bands = {}
    for i in range(da.sizes["features"]):
        band = f"A{i:02d}"
        ds_bands[band] = da.isel(features=i).drop_vars("features")

    ds = xr.Dataset(ds_bands)

    print("\nFinal dataset structure:")
    print(ds)

    # Write to Zarr
    print(f"\nWriting to {args.output}...")
    from dask.diagnostics import ProgressBar
    with ProgressBar():
        ds.to_zarr(args.output, mode="w")

    print("✓ Conversion complete!")
    print("\nNext steps:")
    print(
        f"  1. Consolidate: uv run python pipeline/1_consolidate.py --raw_archive {args.output} --reduced_archive budapest_2024_reduced.zarr"
    )
    print(
        "  2. Reduce: uv run python pipeline/2_reduce.py --input budapest_2024_reduced.zarr_z8 --output budapest_2024_reduced.zarr"
    )
    print("  3. Load: uv run python scripts/load_budapest_2024.py")

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
