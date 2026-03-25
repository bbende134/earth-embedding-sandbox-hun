"""
Step 0: Grid Hungary and Submit Export Tasks for 2018.
Splits Hungary into 25x25km tiles and submits batch exports to Earth Engine.
"""

import time

import ee
import geopandas as gpd
from shapely.geometry import box

# Initialize Earth Engine
try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()

YEAR = 2018
NAME_PREFIX = f"hun_{YEAR}_tile"
DRIVE_FOLDER = f"earth_engine_exports_hun_{YEAR}"


def create_grid(geojson_path, tile_size_deg=0.5):
    """Create a grid of tiles covering the geometry."""
    gdf = gpd.read_file(geojson_path)
    total_bounds = gdf.total_bounds  # minx, miny, maxx, maxy

    minx, miny, maxx, maxy = total_bounds

    tiles = []

    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            b = box(x, y, x + tile_size_deg, y + tile_size_deg)

            if gdf.intersects(b).any():
                tiles.append(b)

            y += tile_size_deg
        x += tile_size_deg

    print(f"Created {len(tiles)} tiles covering Hungary.")
    return tiles


def submit_exports(tiles, name_prefix=NAME_PREFIX):
    """Submit export tasks for each tile."""

    collection = (
        ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
        .filterDate(f"{YEAR}-01-01", f"{YEAR}-12-31")
        .mosaic()
    )

    tasks = []

    print(f"Submitting export tasks for {YEAR}...")
    for i, tile in enumerate(tiles):
        coords = list(tile.exterior.coords)
        roi = ee.Geometry.Polygon(coords)

        task_name = f"{name_prefix}_{i:03d}"

        task = ee.batch.Export.image.toDrive(
            image=collection,
            description=task_name,
            folder=DRIVE_FOLDER,
            fileNamePrefix=task_name,
            region=roi,
            scale=10,
            maxPixels=1e10,
            fileFormat="GeoTIFF",
            formatOptions={"cloudOptimized": True},
        )

        task.start()
        tasks.append(task.id)
        print(f"  Submitted {task_name} (ID: {task.id})")

        if i % 10 == 0:
            time.sleep(2)

    return tasks


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Export a single small test tile")
    parser.add_argument(
        "--geojson", type=str, default="hungary.geojson", help="Path to the GeoJSON file to grid."
    )
    args = parser.parse_args()

    if args.test:
        print(f"Running in TEST mode for {YEAR}: Exporting single small tile...")
        test_box = box(19.0402, 47.4979, 19.0417, 47.4989)
        tiles = [test_box]
        print("Created 1 test tile (100m x 100m).")
        submit_exports(tiles, name_prefix=f"hun_{YEAR}_TEST_tile")
    else:
        print(f"Generating grid for {args.geojson} ({YEAR})...")
        tiles = create_grid(args.geojson, tile_size_deg=0.225)
        submit_exports(tiles)

    print(f"\n✓ All {YEAR} tasks submitted!")
    print("Monitor at: https://code.earthengine.google.com/tasks")


if __name__ == "__main__":
    main()
