"""
Step 0: Export individual S2 L2A scenes and S1 GRD acquisitions per tile to Drive.
One EE task per (sensor x acquisition_date x tile) - no composites.

File naming: {sensor}_{YYYY-MM-DD}_tile_{i:03d}.tif
  s2l2a:  bands B1,B2,B3,B4,B5,B6,B7,B8,B8A,B9,B11,B12  (12ch -> untok_sen2l2a@224)
  s1grd:  bands VV, VH                                    ( 2ch -> untok_sen1grd@224)

NDVI is derived from S2 bands during processing - not a separate export.
"""

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta

import ee
import geopandas as gpd
from shapely.geometry import box

MANIFEST_DIR = "data_multimodal"
DRIVE_FOLDER = "ee_multimodal_exports"
DEFAULT_GEOJSON = "budapest.geojson"
DEFAULT_START = "2017-03-28"
DEFAULT_END = "2026-03-27"
TILE_SIZE_DEG = 0.225
SAMPLE_SIZE = 5

S2_BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]

# extra_filters built lazily after ee.Initialize()
SENSORS = {
    "s2l2a": {
        "collection": "COPERNICUS/S2_SR_HARMONIZED",
        "bands": S2_BANDS,
        "extra_filters": [],
    },
    "s1grd": {
        "collection": "COPERNICUS/S1_GRD",
        "bands": ["VV", "VH"],
        "extra_filters": None,  # populated after ee.Initialize()
    },
}

try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()

SENSORS["s1grd"]["extra_filters"] = [ee.Filter.eq("instrumentMode", "IW")]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_scene_dates(sensor, roi_ee, start_date, end_date):
    """Return sorted list of unique acquisition date strings for a sensor."""
    cfg = SENSORS[sensor]
    col = (
        ee.ImageCollection(cfg["collection"]).filterBounds(roi_ee).filterDate(start_date, end_date)
    )
    for f in cfg["extra_filters"]:
        col = col.filter(f)
    timestamps = col.aggregate_array("system:time_start").getInfo()
    return sorted({datetime.utcfromtimestamp(t / 1000).strftime("%Y-%m-%d") for t in timestamps})


def create_grid(geojson_path, tile_size_deg=TILE_SIZE_DEG):
    gdf = gpd.read_file(geojson_path)
    minx, miny, maxx, maxy = gdf.total_bounds
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
    return tiles


def submit_scene_export(sensor, date_str, tile, task_name):
    cfg = SENSORS[sensor]
    coords = list(tile.exterior.coords)
    roi = ee.Geometry.Polygon(coords)
    date_next = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    col = ee.ImageCollection(cfg["collection"]).filterBounds(roi).filterDate(date_str, date_next)
    for f in cfg["extra_filters"]:
        col = col.filter(f)

    # Mosaic same-day scenes (handles overlapping swaths), clip, cast
    image = col.select(cfg["bands"]).median().clip(roi).toFloat()

    task = ee.batch.Export.image.toDrive(
        image=image,
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
    return task.id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default=DEFAULT_GEOJSON)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument(
        "--sensors", default="s2l2a,s1grd", help="Comma-separated sensors to export (s2l2a, s1grd)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List available scenes without submitting EE tasks"
    )
    parser.add_argument(
        "--test", action="store_true", help="Submit only the first scene per sensor"
    )
    parser.add_argument(
        "--force", action="store_true", help="Clear existing manifest and resubmit all tasks"
    )
    args = parser.parse_args()

    sensors = [s.strip() for s in args.sensors.split(",")]
    for s in sensors:
        if s not in SENSORS:
            print(f"Unknown sensor '{s}'. Choose from: {list(SENSORS)}")
            return

    gdf = gpd.read_file(args.geojson)
    bounds = gdf.total_bounds
    aoi_ee = ee.Geometry.BBox(bounds[0], bounds[1], bounds[2], bounds[3])
    tiles = create_grid(args.geojson)

    print(f"AOI: {args.geojson} → {len(tiles)} tile(s)")
    print(f"Range: {args.start} → {args.end}")
    print()

    # Collect available dates per sensor
    sensor_dates = {}
    max_tasks_warning = 2000
    for sensor in sensors:
        dates = get_scene_dates(sensor, aoi_ee, args.start, args.end)
        sensor_dates[sensor] = dates
        first_date = dates[0] if dates else "-"
        last_date = dates[-1] if dates else "-"
        print(f"  {sensor}: {len(dates)} acquisition dates  ({first_date} -> {last_date})")

    total_tasks = sum(len(d) for d in sensor_dates.values()) * len(tiles)
    print(f"\n  Total EE tasks to submit: {total_tasks}")
    if total_tasks > max_tasks_warning and not args.dry_run:
        print("  WARNING: >2000 tasks. Use --start/--end to limit the range.")

    if args.dry_run:
        print("\nDRY RUN — not submitting. Sample dates:")
        for sensor, dates in sensor_dates.items():
            print(
                f"  {sensor}: {dates[:SAMPLE_SIZE]} "
                f"{'...' if len(dates) > SAMPLE_SIZE else ''}"
            )
        return

    # Load / init manifest
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    manifest_path = os.path.join(MANIFEST_DIR, "manifest.json")
    if args.force and os.path.exists(manifest_path):
        os.remove(manifest_path)
        print("  --force: cleared existing manifest.")
    if os.path.exists(manifest_path) and not args.force:
        with open(manifest_path) as f:
            manifest = json.load(f)
    else:
        manifest = {
            "submitted_at": datetime.now(UTC).isoformat(),
            "geojson": args.geojson,
            "drive_folder": DRIVE_FOLDER,
            "tiles": {},
        }

    n_submitted = 0
    for sensor, dates in sensor_dates.items():
        export_dates = dates[:1] if args.test else dates
        for date_str in export_dates:
            for i, tile in enumerate(tiles):
                task_name = f"{sensor}_{date_str}_tile_{i:03d}"
                if task_name in manifest["tiles"]:
                    print(f"  Already submitted: {task_name}")
                    continue
                task_id = submit_scene_export(sensor, date_str, tile, task_name)
                manifest["tiles"][task_name] = {
                    "task_id": task_id,
                    "sensor": sensor,
                    "acquisition_date": date_str,
                    "bbox": list(tile.bounds),
                    "tile_idx": i,
                }
                print(f"  Submitted {task_name}  (task: {task_id})")
                n_submitted += 1
                if n_submitted % 10 == 0:
                    with open(manifest_path, "w") as f:
                        json.dump(manifest, f, indent=2)
                    time.sleep(1)

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\n✓ {n_submitted} tasks submitted. Manifest: {manifest_path}")
    print("  Monitor: https://code.earthengine.google.com/tasks")


if __name__ == "__main__":
    main()
