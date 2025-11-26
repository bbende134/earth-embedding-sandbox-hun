"""
Minimal extraction script for Budapest 2024 using standard xarray-beam without custom shimming.
This avoids the validation failure by using the standard pipeline components.
"""

import json

import apache_beam as beam
import ee
import pyproj
import xarray as xr
import xarray_beam as xbeam
from apache_beam.options.pipeline_options import PipelineOptions
from shapely.geometry import shape
from shapely.ops import transform
from xee import EarthEngineBackendEntrypoint

# Initialize Earth Engine
try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()

# Configuration
INPUT_GEOJSON = "budapest_aoi.json"
OUTPUT_ZARR = "budapest_2024_minimal.zarr"
UTM_ZONE = "EPSG:32634"
SCALE = 10
START_DATE = "2024-01-01"
END_DATE = "2024-12-31"
CHUNKS = {"time": 1, "X": 256, "Y": 256}  # Smaller chunks for reliability

# Load AOI
with open(INPUT_GEOJSON) as f:
    geojson = json.load(f)
aoi = shape(geojson["geometry"])

# Reproject to UTM
reproject = pyproj.Transformer.from_crs("EPSG:4326", UTM_ZONE, always_xy=True).transform
aoi_utm = transform(reproject, aoi)
minx, _miny, _maxx, maxy = aoi_utm.bounds

# Create affine transform
affine = [SCALE, 0, minx, 0, -SCALE, maxy]

# Create EE geometry
aoi_ee = ee.Geometry(geojson["geometry"])

print(f"AOI bounds (UTM): {aoi_utm.bounds}")
print(f"Affine transform: {affine}")

# Create image (WITHOUT .clip())
im = (
    ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
    .filterDate(START_DATE, END_DATE)
    .filterBounds(aoi_ee)
    .mosaic()
)

print("Opening xarray dataset...")
ds = xr.open_dataset(
    im,
    engine=EarthEngineBackendEntrypoint,
    projection=ee.Projection(UTM_ZONE, transform=affine),
    geometry=list(aoi.bounds),
    scale=SCALE,
    chunks=CHUNKS,
)

print("Dataset:")
print(ds)

template = xbeam.make_template(ds)
print("\nTemplate:")
print(template)

# Run simple pipeline WITHOUT custom shimming
print(f"\nWriting to {OUTPUT_ZARR}...")

options = PipelineOptions()
with beam.Pipeline(options=options) as root:
    _ = (
        root
        | xbeam.DatasetToChunks(ds, chunks=CHUNKS)  # Standard, no custom shim
        | xbeam.ChunksToZarr(OUTPUT_ZARR, template=template, zarr_chunks=CHUNKS)
    )

print("Done!")
