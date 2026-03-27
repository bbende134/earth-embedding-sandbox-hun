#!/usr/bin/env python3
"""
Visualize timeline coverage: EE-available acquisitions vs. Milvus-processed ones.

For each sensor/modality, queries:
  - Earth Engine: all unique acquisition dates in the date range
  - Milvus dyn_terra: all unique dates already processed

Generates a horizontal timeline plot:
  green  = processed in Milvus
  red    = available in EE but not yet processed
  Y-axis = modality (s2l2a / s1grd)
"""

import argparse
from datetime import datetime

import ee
import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from pymilvus import Collection, connections, utility

DEFAULT_GEOJSON = "budapest.geojson"
DEFAULT_START = "2017-03-28"
DEFAULT_END = "2026-03-27"
DEFAULT_OUTPUT = "debug/timeline_coverage.png"

EE_SENSORS = {
    "s2l2a": {
        "collection": "COPERNICUS/S2_SR_HARMONIZED",
        "modality": "untok_sen2l2a@224",
        "extra_filter": None,
        "color": "#4e9af1",
    },
    "s1grd": {
        "collection": "COPERNICUS/S1_GRD",
        "modality": "untok_sen1grd@224",
        "extra_filter": ("instrumentMode", "IW"),  # built after ee.Initialize()
        "color": "#f1a24e",
    },
}


# ---------------------------------------------------------------------------
# EE helpers
# ---------------------------------------------------------------------------


def fetch_ee_dates(sensor_cfg: dict, roi_ee, start_date: str, end_date: str) -> set[str]:
    col = (
        ee.ImageCollection(sensor_cfg["collection"])
        .filterBounds(roi_ee)
        .filterDate(start_date, end_date)
    )
    if sensor_cfg["extra_filter"]:
        k, v = sensor_cfg["extra_filter"]
        col = col.filter(ee.Filter.eq(k, v))
    timestamps = col.aggregate_array("system:time_start").getInfo()
    return {datetime.utcfromtimestamp(t / 1000).strftime("%Y-%m-%d") for t in timestamps}


# ---------------------------------------------------------------------------
# Milvus helpers
# ---------------------------------------------------------------------------


def fetch_milvus_dates(collection: str, modality: str, host: str, port: str) -> set[str]:
    connections.connect("default", host=host, port=port)
    if not utility.has_collection(collection):
        return set()
    coll = Collection(collection)
    coll.load()

    dates: set[str] = set()
    offset = 0
    batch_size = 16384
    while True:
        rows = coll.query(
            expr=f'modality == "{modality}"',
            output_fields=["date_start"],
            limit=batch_size,
            offset=offset,
        )
        for r in rows:
            dates.add(r["date_start"])
        if len(rows) < batch_size:
            break
        offset += batch_size
    return dates


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def date_to_num(date_str: str) -> float:
    return datetime.strptime(date_str, "%Y-%m-%d").timestamp() / 86400.0  # days since epoch


def plot_timeline(
    sensor_results: dict,  # sensor → {ee_dates, milvus_dates, color, modality}
    output_path: str,
    start_date: str,
    end_date: str,
):
    sensors = list(sensor_results.keys())
    n = len(sensors)

    fig, axes = plt.subplots(n, 1, figsize=(20, 3 * n), sharex=True)
    if n == 1:
        axes = [axes]

    x_min = date_to_num(start_date)
    x_max = date_to_num(end_date)

    for ax, sensor in zip(axes, sensors, strict=False):
        info = sensor_results[sensor]
        ee_dates = info["ee_dates"]
        milvus_dates = info["milvus_dates"]
        modality = info["modality"]

        missing = sorted(ee_dates - milvus_dates)
        done = sorted(ee_dates & milvus_dates)

        # Draw tick marks: red=missing, green=done
        for d in missing:
            x = date_to_num(d)
            ax.axvline(x, color="red", alpha=0.6, linewidth=1.2)
        for d in done:
            x = date_to_num(d)
            ax.axvline(x, color="green", alpha=0.7, linewidth=1.2)

        # Summary text
        total = len(ee_dates)
        processed = len(done)
        pct = 100 * processed / total if total else 0
        ax.set_title(
            f"{sensor}  ({modality})   {processed}/{total} processed  ({pct:.0f}%)",
            loc="left",
            fontsize=11,
        )
        ax.set_xlim(x_min, x_max)
        ax.set_yticks([])
        ax.set_ylabel(sensor, fontsize=10, rotation=0, labelpad=50, va="center")
        ax.grid(axis="x", linestyle="--", alpha=0.3)

    # X-axis: year ticks
    ax = axes[-1]
    start_year = int(start_date[:4])
    end_year = int(end_date[:4]) + 1
    year_ticks = []
    year_labels = []
    for year in range(start_year, end_year + 1):
        d = f"{year}-01-01"
        year_ticks.append(date_to_num(d))
        year_labels.append(str(year))
    ax.set_xticks(year_ticks)
    ax.set_xticklabels(year_labels, fontsize=9)
    ax.set_xlabel("Date", fontsize=10)

    # Legend
    green_patch = mpatches.Patch(color="green", alpha=0.7, label="Processed in Milvus")
    red_patch = mpatches.Patch(color="red", alpha=0.6, label="Available in EE, not yet processed")
    fig.legend(handles=[green_patch, red_patch], loc="upper right", fontsize=10)

    fig.suptitle(
        f"Timeline coverage  ({start_date} → {end_date})",
        fontsize=13,
        y=1.01,
    )
    fig.tight_layout()

    import os

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"✓ Timeline saved to {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default=DEFAULT_GEOJSON)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--collection", default="dyn_terra")
    parser.add_argument("--milvus-host", default="localhost")
    parser.add_argument("--milvus-port", default="19530")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--skip-ee", action="store_true", help="Skip EE query (show only what is in Milvus)"
    )
    args = parser.parse_args()

    # Init EE
    if not args.skip_ee:
        try:
            ee.Initialize()
        except Exception:
            ee.Authenticate()
            ee.Initialize()

        gdf = gpd.read_file(args.geojson)
        bounds = gdf.total_bounds
        roi_ee = ee.Geometry.BBox(bounds[0], bounds[1], bounds[2], bounds[3])

    sensor_results = {}

    for sensor, cfg in EE_SENSORS.items():
        print(f"\n[{sensor}]")

        if args.skip_ee:
            ee_dates = set()
            print("  EE query skipped.")
        else:
            print(f"  Fetching EE dates ({cfg['collection']})...")
            ee_dates = fetch_ee_dates(cfg, roi_ee, args.start, args.end)
            print(f"  EE dates: {len(ee_dates)}")

        print(f"  Fetching Milvus dates (modality={cfg['modality']})...")
        milvus_dates = fetch_milvus_dates(
            args.collection,
            cfg["modality"],
            args.milvus_host,
            args.milvus_port,
        )
        print(f"  Milvus dates: {len(milvus_dates)}")

        if ee_dates:
            missing = ee_dates - milvus_dates
            print(f"  Missing: {len(missing)}")

        sensor_results[sensor] = {
            "ee_dates": ee_dates,
            "milvus_dates": milvus_dates,
            "color": cfg["color"],
            "modality": cfg["modality"],
        }

    print()
    plot_timeline(sensor_results, args.output, args.start, args.end)


if __name__ == "__main__":
    main()
