"""
STAC alternative to 0_export_multimodal.py + 1_download_multimodal.py.
Downloads S2 L2A and S1 RTC directly from Microsoft Planetary Computer — no GEE, no Drive.

Output: same annual stacked GeoTIFF format as the GEE pipeline:
  data_multimodal/{sensor}_{YYYY}_tile_{i:03d}.tif
  Band descriptions: '{idx}_{YYYYMMDD}_{band}' — parsed by 2_process_multimodal.py

Then optionally pipes straight into 2_process_multimodal.py.

Note on S1: Planetary Computer provides sentinel-1-rtc (Radiometrically Terrain Corrected),
which is equivalent to GRD with terrain correction applied. Values are in linear power scale.
"""

import argparse
import os
import subprocess

import geopandas as gpd
import numpy as np
import planetary_computer
import pystac_client
import rasterio
import rasterio.warp
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from shapely.geometry import box

DEFAULT_GEOJSON = "budapest.geojson"
DEFAULT_START = "2021-06-01"
DEFAULT_END = "2021-07-01"
DEFAULT_OUT_DIR = "data_multimodal"
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
TARGET_RES_DEG = 10 / 111320  # ~10 m in WGS84 degrees (latitude axis)

# Per-sensor config: STAC collection, asset keys, TerraMind band names, scale factor
SENSOR_CFG = {
    "s2l2a": {
        "collection": "sentinel-2-l2a",
        "assets": [
            "B01",
            "B02",
            "B03",
            "B04",
            "B05",
            "B06",
            "B07",
            "B08",
            "B8A",
            "B09",
            "B11",
            "B12",
        ],
        "band_names": ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"],
        "scale": 1 / 10000.0,  # DN → reflectance
        "cloud_filter": True,
    },
    "s1grd": {
        "collection": "sentinel-1-rtc",
        "assets": ["vv", "vh"],
        "band_names": ["VV", "VH"],
        "scale": 1.0,  # already in linear power
        "cloud_filter": False,
    },
}


# ---------------------------------------------------------------------------
# Grid helpers (mirrors 0_export_multimodal.py)
# ---------------------------------------------------------------------------


def create_grid(geojson_path, tile_size_deg=0.225):
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


def micro_tile(geojson_path, half_deg=0.01):
    """Single 224px micro-tile centred on the AOI for quick tests."""
    gdf = gpd.read_file(geojson_path)
    minx, miny, maxx, maxy = gdf.total_bounds
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    return [box(cx - half_deg, cy - half_deg, cx + half_deg, cy + half_deg)]


# ---------------------------------------------------------------------------
# STAC query
# ---------------------------------------------------------------------------


def query_items(sensor: str, bbox, date_start, date_end, cloud_cover_max=80):
    """Return STAC items for a sensor, sorted by date."""
    cfg = SENSOR_CFG[sensor]
    catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)

    search_kwargs = {
        "collections": [cfg["collection"]],
        "bbox": bbox,
        "datetime": f"{date_start}/{date_end}",
    }
    if cfg["cloud_filter"]:
        search_kwargs["query"] = {"eo:cloud_cover": {"lt": cloud_cover_max}}

    items = list(catalog.search(**search_kwargs).item_collection())
    items.sort(key=lambda i: i.datetime)

    # Deduplicate S1: keep one item per date (multiple passes per day possible)
    if sensor == "s1grd":
        seen, unique = set(), []
        for item in items:
            d = item.datetime.strftime("%Y%m%d")
            if d not in seen:
                seen.add(d)
                unique.append(item)
        items = unique

    return items


# ---------------------------------------------------------------------------
# Band download
# ---------------------------------------------------------------------------


def make_target_grid(bbox):
    """Return (transform, width, height, crs) for the target 10m WGS84 grid."""
    minx, miny, maxx, maxy = bbox
    width = max(224, round((maxx - minx) / TARGET_RES_DEG))
    height = max(224, round((maxy - miny) / TARGET_RES_DEG))
    transform = from_bounds(minx, miny, maxx, maxy, width, height)
    return transform, width, height, "EPSG:4326"


def read_band(
    signed_url: str, target_transform, target_width: int, target_height: int, target_crs: str
) -> np.ndarray:
    """Read one band COG, reprojecting to target grid. Returns float32 (H, W)."""
    dst = np.zeros((target_height, target_width), dtype=np.float32)
    env = rasterio.Env(
        GDAL_HTTP_TIMEOUT=30,
        GDAL_HTTP_MAX_RETRY=3,
        GDAL_HTTP_RETRY_DELAY=5,
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
    )
    with env, rasterio.open(signed_url) as src:
        rasterio.warp.reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            dst_transform=target_transform,
            dst_crs=target_crs,
            resampling=Resampling.bilinear,
        )
    return dst


def download_item_bands(
    sensor: str, item, target_transform, target_width, target_height, target_crs
):
    """
    Download all bands for one STAC item.
    Returns (yyyymmdd, np.ndarray (n_bands, H, W)).
    """
    cfg = SENSOR_CFG[sensor]
    date_str = item.datetime.strftime("%Y%m%d")
    bands = []

    for asset_key, band_name in zip(cfg["assets"], cfg["band_names"], strict=False):
        if asset_key not in item.assets:
            print(f"    Warning: {asset_key} missing for {date_str}, using zeros")
            bands.append(np.zeros((target_height, target_width), dtype=np.float32))
            continue
        url = planetary_computer.sign(item.assets[asset_key].href)
        arr = read_band(url, target_transform, target_width, target_height, target_crs)
        arr = (arr * cfg["scale"]).astype(np.float32)
        bands.append(arr)
        print(f"    {asset_key} ({band_name}) ✓", end="\r")

    print()
    return date_str, np.stack(bands, axis=0)


# ---------------------------------------------------------------------------
# Write stacked GeoTIFF
# ---------------------------------------------------------------------------


def write_stacked_tif(
    output_path: str,
    sensor: str,
    date_arrays: list[tuple[str, np.ndarray]],
    target_transform,
    target_crs: str,
):
    """
    Write annual stacked GeoTIFF with band descriptions matching GEE output:
      '{image_idx}_{YYYYMMDD}_{band}'  e.g. '0_20210601_B1', '0_20210601_VV'
    """
    cfg = SENSOR_CFG[sensor]
    band_names = cfg["band_names"]
    n_bands_per = len(band_names)
    total_bands = len(date_arrays) * n_bands_per
    h = date_arrays[0][1].shape[1]
    w = date_arrays[0][1].shape[2]

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=total_bands,
        dtype="float32",
        crs=target_crs,
        transform=target_transform,
        compress="deflate",
    ) as dst:
        band_idx = 1
        for img_idx, (yyyymmdd, arr) in enumerate(date_arrays):
            for b_idx, band_name in enumerate(band_names):
                dst.write(arr[b_idx], band_idx)
                dst.set_band_description(band_idx, f"{img_idx}_{yyyymmdd}_{band_name}")
                band_idx += 1

    print(f"  Written: {output_path}  ({total_bands} bands, {len(date_arrays)} dates)")


# ---------------------------------------------------------------------------
# Process one sensor × tile
# ---------------------------------------------------------------------------


def run_sensor_tile(
    sensor: str,
    tile_idx: int,
    tile,
    year: int,
    date_start: str,
    date_end: str,
    cloud_max: int,
    output_dir: str,
    process: bool,
    dry_run: bool,
):
    cfg = SENSOR_CFG[sensor]
    bbox = list(tile.bounds)
    print(f"\n[{sensor} | tile {tile_idx:03d}]  bbox={[round(b, 4) for b in bbox]}")

    cloud_note = f", cloud<{cloud_max}%" if cfg["cloud_filter"] else ""
    print(f"  Querying {cfg['collection']} ({date_start} -> {date_end}{cloud_note})...")
    items = query_items(sensor, bbox, date_start, date_end, cloud_max)
    print(f"  Found {len(items)} scenes")

    if not items:
        print("  No scenes — skipping.")
        return

    for item in items:
        cloud = item.properties.get("eo:cloud_cover")
        cloud_str = f"  cloud={cloud:.0f}%" if cloud is not None else ""
        print(f"    {item.datetime.date()}{cloud_str}")

    if dry_run:
        return

    target_transform, target_w, target_h, target_crs = make_target_grid(bbox)
    print(f"  Target grid: {target_w}x{target_h}px  CRS: {target_crs}")

    n_bands_per = len(cfg["band_names"])
    total_bands = len(items) * n_bands_per
    out_path = os.path.join(output_dir, f"{sensor}_{year}_tile_{tile_idx:03d}.tif")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    # Stream-write: open TIF once and write each scene as it's downloaded
    with rasterio.open(
        out_path,
        "w",
        driver="GTiff",
        height=target_h,
        width=target_w,
        count=total_bands,
        dtype="float32",
        crs=target_crs,
        transform=target_transform,
        compress="deflate",
    ) as dst:
        band_idx = 1
        for i, item in enumerate(items):
            print(f"  [{i + 1}/{len(items)}] {item.datetime.date()}")
            yyyymmdd, arr = download_item_bands(
                sensor, item, target_transform, target_w, target_h, target_crs
            )
            for b_idx, band_name in enumerate(cfg["band_names"]):
                dst.write(arr[b_idx], band_idx)
                dst.set_band_description(band_idx, f"{i}_{yyyymmdd}_{band_name}")
                band_idx += 1
            del arr  # Free memory immediately

    print(f"  Written: {out_path}  ({total_bands} bands, {len(items)} dates)")

    if process:
        print("  Processing through TerraMind...")
        subprocess.run(
            [
                "uv",
                "run",
                "python",
                "hungary_full_pipeline/2_process_multimodal.py",
                "--input",
                out_path,
            ],
            check=True,
        )
        os.remove(out_path)
        print(f"  Cleaned up {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default=DEFAULT_GEOJSON)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument(
        "--sensors", default="s2l2a,s1grd", help="Comma-separated sensors (s2l2a, s1grd)"
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--test-aoi", action="store_true", help="Use a single 224px micro-tile centred on the AOI"
    )
    parser.add_argument(
        "--cloud-max", type=int, default=80, help="Max cloud cover %% for S2 filtering"
    )
    parser.add_argument(
        "--process", action="store_true", help="Pipe output directly into 2_process_multimodal.py"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Query STAC and print items without downloading"
    )
    parser.add_argument(
        "--tile-idx", type=int, default=None, help="Process only a single tile (0-indexed)"
    )
    parser.add_argument(
        "--year", type=int, default=None, help="Override year (default: from --start)"
    )
    args = parser.parse_args()

    sensors = [s.strip() for s in args.sensors.split(",")]
    for s in sensors:
        if s not in SENSOR_CFG:
            print(f"Unknown sensor '{s}'. Choose from: {list(SENSOR_CFG)}")
            return

    tiles = micro_tile(args.geojson) if args.test_aoi else create_grid(args.geojson)
    if args.test_aoi:
        print("  [test-aoi] Single 224px micro-tile centred on AOI")
    print(f"  Tiles: {len(tiles)}  |  Sensors: {sensors}")

    year = args.year if args.year is not None else int(args.start[:4])
    os.makedirs(args.output_dir, exist_ok=True)

    # If --tile-idx specified, process only that tile
    if args.tile_idx is not None:
        if args.tile_idx < 0 or args.tile_idx >= len(tiles):
            print(f"Error: tile-idx {args.tile_idx} out of range [0, {len(tiles) - 1}]")
            return
        tile_indices = [args.tile_idx]
        print(f"  [single-tile] Processing only tile {args.tile_idx}")
    else:
        tile_indices = range(len(tiles))

    for tile_idx in tile_indices:
        tile = tiles[tile_idx]
        for sensor in sensors:
            run_sensor_tile(
                sensor,
                tile_idx,
                tile,
                year,
                args.start,
                args.end,
                args.cloud_max,
                args.output_dir,
                args.process,
                args.dry_run,
            )


if __name__ == "__main__":
    main()
