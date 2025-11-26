"""
Export Budapest 2024 data using Earth Engine's batch export system.
This bypasses interactive API limits by running the export as a serverside task.
"""

import json
import time

import ee

# Initialize Earth Engine
try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()

# Load Budapest AOI
with open("budapest_aoi.json") as f:
    geojson = json.load(f)
aoi_ee = ee.Geometry(geojson["geometry"])

print(f"AOI: {geojson['geometry']['coordinates']}")

# Create 2024 mosaic
dataset = (
    ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
    .filterDate("2024-01-01", "2024-12-31")
    .filterBounds(aoi_ee)
    .mosaic()
)

print("Submitting export task to Google Drive...")
print("This will export ~2.5GB of data at 10m resolution")
print("")

# Export to Google Drive
task = ee.batch.Export.image.toDrive(
    image=dataset,
    description="budapest_2024_embeddings",
    folder="earth_engine_exports",  # Folder in Google Drive
    fileNamePrefix="budapest_2024",
    region=aoi_ee,
    scale=10,  # 10 meter resolution
    maxPixels=1e10,  # Allow up to 10 billion pixels
    fileFormat="GeoTIFF",
    formatOptions={"cloudOptimized": True},
)

task.start()

print(f"✓ Task submitted: {task.id}")
print("  Description: budapest_2024_embeddings")
print(f"  Status: {task.status()['state']}")
print("")
print("Monitor progress:")
print("  1. Check Earth Engine Tasks: https://code.earthengine.google.com/tasks")
print(f"  2. Or run: ee.data.getTaskStatus('{task.id}')")
print("")
print("Once complete:")
print("  1. Download budapest_2024.tif from Google Drive/earth_engine_exports/")
print("  2. Convert to Zarr with: rio convert budapest_2024.tif budapest_2024.zarr")
print("  3. Run consolidation pipeline")

# Monitor status for a bit
print("\nMonitoring task status...")
for i in range(10):
    time.sleep(5)
    status = task.status()
    state = status["state"]
    print(f"  [{i * 5}s] State: {state}")

    if state in ["COMPLETED", "FAILED", "CANCELLED"]:
        if state == "COMPLETED":
            print("\n✓ Export completed!")
        else:
            print(f"\n✗ Export {state}")
            if "error_message" in status:
                print(f"  Error: {status['error_message']}")
        break

    if state == "RUNNING":
        if "progress" in status:
            print(f"    Progress: {status.get('progress', 0) * 100:.1f}%")

if status["state"] == "READY":
    print("\n⏳ Task queued. Check status at: https://code.earthengine.google.com/tasks")
