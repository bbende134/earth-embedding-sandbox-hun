"""
Step 0: Export annual stacked S2 L2A and S1 GRD acquisitions per tile to Drive.
One EE task per (sensor x year x tile) — all acquisition dates stacked as bands.

File naming: {sensor}_{YYYY}_tile_{i:03d}.tif  (EE may shard: -NNNN-NNNN.tif suffix)
  s2l2a: bands B1..B12 per date  -> untok_sen2l2a@224
  s1grd: bands VV,VH per date    -> untok_sen1grd@224

Band names in exported TIF: '{idx}_{YYYYMMDD}_{band}'  (from EE toBands())
  S2 example: '0_20170329_B1', '0_20170329_B2', ..., '1_20170403_B1', ...
  S1 example: '0_20170329_VV', '0_20170329_VH', '1_20170409_VV', ...

NDVI is derived from S2 bands during processing - not a separate export.
"""

import argparse
import json
import os
import time
from datetime import UTC, datetime

import ee
import geopandas as gpd
from shapely.geometry import box

MANIFEST_DIR = "data_multimodal"
DRIVE_FOLDER = "ee_multimodal_exports"
DEFAULT_GEOJSON = "budapest.geojson"
DEFAULT_START = "2017-03-28"
DEFAULT_END = "2026-03-27"
TILE_SIZE_DEG = 0.225

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


def get_years(start_date: str, end_date: str) -> list[int]:
    return list(range(int(start_date[:4]), int(end_date[:4]) + 1))


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


def get_year_dates(
    sensor: str, roi_ee, year: int, date_start: str | None = None, date_end: str | None = None
) -> list[str]:
    """Return sorted unique acquisition dates for a sensor within a calendar year."""
    cfg = SENSORS[sensor]
    filter_start = date_start if date_start and date_start > f"{year}-01-01" else f"{year}-01-01"
    filter_end = date_end if date_end and date_end < f"{year + 1}-01-01" else f"{year + 1}-01-01"
    col = (
        ee.ImageCollection(cfg["collection"])
        .filterBounds(roi_ee)
        .filterDate(filter_start, filter_end)
    )
    for f in cfg["extra_filters"]:
        col = col.filter(f)
    timestamps = col.aggregate_array("system:time_start").getInfo()
    return sorted({datetime.utcfromtimestamp(t / 1000).strftime("%Y-%m-%d") for t in timestamps})


def submit_annual_export(
    sensor: str,
    year: int,
    tile,
    tile_idx: int,
    task_name: str,
    date_start: str | None = None,
    date_end: str | None = None,
):
    """
    Stack all acquisitions for (sensor, year, tile) into one multi-band GeoTIFF.
    date_start/date_end optionally clamp the filter within the year.
    Band names in output: '{image_idx}_{YYYYMMDD}_{band}' (EE toBands() convention).
    Returns (task_id, sorted_dates) or (None, []) if no images exist.
    """
    cfg = SENSORS[sensor]
    coords = list(tile.exterior.coords)
    roi = ee.Geometry.Polygon(coords)

    filter_start = date_start if date_start and date_start > f"{year}-01-01" else f"{year}-01-01"
    filter_end = date_end if date_end and date_end < f"{year + 1}-01-01" else f"{year + 1}-01-01"

    col = (
        ee.ImageCollection(cfg["collection"]).filterBounds(roi).filterDate(filter_start, filter_end)
    )
    for f in cfg["extra_filters"]:
        col = col.filter(f)

    if col.size().getInfo() == 0:
        return None, []

    # Get ordered dates for the manifest
    timestamps = col.aggregate_array("system:time_start").getInfo()
    dates = sorted({datetime.utcfromtimestamp(t / 1000).strftime("%Y-%m-%d") for t in timestamps})

    # Rename each image's bands to encode the acquisition date, then stack
    bands_list = ee.List(cfg["bands"])

    def rename_with_date(img):
        date_str = img.date().format("YYYYMMdd")
        new_names = bands_list.map(lambda b: date_str.cat("_").cat(ee.String(b)))
        return img.select(cfg["bands"]).rename(new_names)

    stacked = col.map(rename_with_date).toBands().clip(roi).toFloat()

    task = ee.batch.Export.image.toDrive(
        image=stacked,
        description=task_name,
        folder=DRIVE_FOLDER,
        fileNamePrefix=task_name,
        region=roi,
        scale=10,
        maxPixels=1e13,
        fileFormat="GeoTIFF",
        formatOptions={"cloudOptimized": True},
    )
    task.start()
    return task.id, dates


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
    parser.add_argument("--dry-run", action="store_true", help="List tasks without submitting")
    parser.add_argument(
        "--test", action="store_true", help="Submit only the first (sensor, year, tile) combination"
    )
    parser.add_argument(
        "--force", action="store_true", help="Clear existing manifest and resubmit all tasks"
    )
    parser.add_argument(
        "--test-aoi",
        action="store_true",
        help="Use a single 224x224px (~0.02deg) micro-tile for quick end-to-end tests",
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

    if args.test_aoi:
        # Single 224px (~0.02deg) micro-tile centred on the AOI for fast end-to-end tests
        cx = (bounds[0] + bounds[2]) / 2
        cy = (bounds[1] + bounds[3]) / 2
        half = 0.01  # 0.02deg side ≈ 2.2km ≈ 224px at 10m
        from shapely.geometry import box as _box

        tiles = [_box(cx - half, cy - half, cx + half, cy + half)]
        print("  [test-aoi] Using single 224px micro-tile centred on AOI")
    else:
        tiles = create_grid(args.geojson)

    years = get_years(args.start, args.end)

    print(f"AOI:     {args.geojson} -> {len(tiles)} tile(s)")
    print(f"Range:   {args.start} -> {args.end}  ({len(years)} years)")
    print(f"Sensors: {sensors}")
    total_tasks = len(sensors) * len(years) * len(tiles)
    print(f"Tasks:   {total_tasks}  (one per sensor x year x tile)")
    print()

    if args.dry_run:
        print("DRY RUN — not submitting. Sampling first 2 years:")
        for sensor in sensors:
            for year in years[:2]:
                dates = get_year_dates(sensor, aoi_ee, year, args.start, args.end)
                print(
                    f"  {sensor} {year}: {len(dates)} dates  "
                    f"({dates[0] if dates else '-'} -> {dates[-1] if dates else '-'})"
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
    year_list = years[:1] if args.test else years
    for sensor in sensors:
        for year in year_list:
            tile_list = tiles[:1] if args.test else tiles
            for i, tile in enumerate(tile_list):
                task_name = f"{sensor}_{year}_tile_{i:03d}"
                if task_name in manifest["tiles"]:
                    print(f"  Already submitted: {task_name}")
                    continue

                task_id, dates = submit_annual_export(
                    sensor, year, tile, i, task_name, date_start=args.start, date_end=args.end
                )
                if task_id is None:
                    print(f"  No images for {task_name} — skipping.")
                    continue

                manifest["tiles"][task_name] = {
                    "task_id": task_id,
                    "sensor": sensor,
                    "year": year,
                    "dates": dates,
                    "bbox": list(tile.bounds),
                    "tile_idx": i,
                }
                print(f"  Submitted {task_name}  ({len(dates)} dates, task: {task_id})")
                n_submitted += 1

                if n_submitted % 5 == 0:
                    with open(manifest_path, "w") as f:
                        json.dump(manifest, f, indent=2)
                    time.sleep(1)

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\n✓ {n_submitted} tasks submitted. Manifest: {manifest_path}")
    print("  Monitor: https://code.earthengine.google.com/tasks")


if __name__ == "__main__":
    main()
