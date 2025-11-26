"""
Simple extraction script for Budapest 2024 data.
Bypasses the complex pipeline and directly extracts data to disk.
"""

import json
import sys

import ee
import xarray as xr
from shapely.geometry import shape

# Initialize Earth Engine
try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()

# Load Budapest AOI
with open("budapest_aoi.json") as f:
    geojson = json.load(f)
aoi = shape(geojson["geometry"])
aoi_ee = ee.Geometry(geojson["geometry"])

print(f"AOI bounds: {aoi.bounds}")

# Create image collection and mosaic
dataset = (
    ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
    .filterDate("2024-01-01", "2024-12-31")
    .filterBounds(aoi_ee)
    .mosaic()
)

print("Fetching data from Earth Engine...")
print("This will download the full resolution data for Budapest 2024.")
print("Area: ~30km x 30km at 10m resolution = ~3000x3000 pixels per band")
print("With 64 bands, this is approximately 2.3 GB of data.")

# Sample a single point first to verify data exists
point = ee.Geometry.Point([19.04, 47.5])
sample = (
    dataset.select("A00")
    .reduceRegion(reducer=ee.Reducer.first(), geometry=point, scale=10)
    .getInfo()
)

print(f"Sample value at Budapest center: {sample}")

if sample.get("A00") is None:
    print("ERROR: No data available at sample point!")
    sys.exit(1)

# For simplicity, let's extract just a smaller region first to test
# Budapest center: 19.04, 47.5
# Small box: ±0.05 degrees ~ 5km
small_aoi = ee.Geometry.Rectangle([19.0, 47.45, 19.08, 47.55])

print("\nExtracting small test region first (8km x 11km)...")

# Use geemap to export
try:
    import geemap

    # Export the small region
    geemap.ee_export_image(
        dataset, filename="budapest_2024_test.tif", scale=10, region=small_aoi, file_per_band=False
    )
    print("Export complete! Check budapest_2024_test.tif")

except ImportError:
    print("geemap not available, using alternative method...")

    # Alternative: use xee
    from xee import EarthEngineBackendEntrypoint

    ds = xr.open_dataset(
        dataset,
        engine=EarthEngineBackendEntrypoint,
        geometry=small_aoi,
        scale=10,
        chunks={"X": 256, "Y": 256},
    )

    print("Dataset loaded:")
    print(ds)

    # Save to NetCDF
    print("Saving to NetCDF...")
    ds.to_netcdf("budapest_2024_test.nc")
    print("Export complete! Check budapest_2024_test.nc")
