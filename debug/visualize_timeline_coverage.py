#!/usr/bin/env python3
"""
Visualize timeline coverage: STAC-available acquisitions vs. Milvus-processed ones.

For each sensor/modality, queries:
  - Planetary Computer STAC: all unique acquisition dates in the date range
  - Milvus dyn_terra: all unique dates already processed

Generates a horizontal timeline plot:
  green  = processed in Milvus
  red    = available in STAC but not yet processed
  Y-axis = modality (s2l2a / s1grd)
"""

import argparse
import os
from datetime import datetime

import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pystac_client
from pymilvus import Collection, connections, utility
from tqdm import tqdm

DEFAULT_GEOJSON = "budapest.geojson"
DEFAULT_START = "2017-01-01"
DEFAULT_END = "2026-03-27"
DEFAULT_OUTPUT = "debug/timeline_coverage.png"

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

SENSORS = {
    "s2l2a": {
        "stac_collection": "sentinel-2-l2a",
        "modality": "untok_sen2l2a@224",
        "cloud_filter": True,
        "cloud_max": 80,
        "color": "#4e9af1",
    },
    "s1grd": {
        "stac_collection": "sentinel-1-rtc",
        "modality": "untok_sen1grd@224",
        "cloud_filter": False,
        "cloud_max": None,
        "color": "#f1a24e",
    },
}


# ---------------------------------------------------------------------------
# STAC helpers
# ---------------------------------------------------------------------------


def fetch_stac_dates(sensor_cfg, bbox, start_date, end_date):
    catalog = pystac_client.Client.open(STAC_URL)
    search_kwargs = {
        "collections": [sensor_cfg["stac_collection"]],
        "bbox": bbox,
        "datetime": f"{start_date}/{end_date}",
    }
    if sensor_cfg["cloud_filter"]:
        search_kwargs["query"] = {"eo:cloud_cover": {"lt": sensor_cfg["cloud_max"]}}

    items = list(catalog.search(**search_kwargs).item_collection())
    dates = set()
    for item in items:
        dates.add(item.datetime.strftime("%Y-%m-%d"))
    return dates


# ---------------------------------------------------------------------------
# Milvus helpers
# ---------------------------------------------------------------------------


def fetch_milvus_dates(collection, modality, host, port):
    connections.connect("default", host=host, port=port)
    if not utility.has_collection(collection):
        return set()

    coll = Collection(collection)

    # Wait for collection to be queryable
    load_state = str(utility.load_state(collection))
    if "NotLoad" in load_state:
        coll.load()
        utility.wait_for_loading_complete(collection, timeout=120)
    elif "Loaded" not in load_state:
        # Loading/recovering — wait
        import time

        for _ in range(20):
            load_state = str(utility.load_state(collection))
            if "Loaded" in load_state:
                break
            time.sleep(5)

    # Use iterator to avoid offset+limit > 16384 cap
    dates = set()
    iterator = coll.query_iterator(
        expr=f'modality == "{modality}"',
        output_fields=["date_start"],
        batch_size=10000,
    )
    while True:
        rows = iterator.next()
        if not rows:
            break
        for r in rows:
            dates.add(r["date_start"])
    iterator.close()
    return dates


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def date_to_num(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d").timestamp() / 86400.0


def plot_timeline(sensor_results, output_path, start_date, end_date):
    sensors = list(sensor_results.keys())
    n = len(sensors)

    fig, axes = plt.subplots(n, 1, figsize=(20, 3 * n), sharex=True)
    if n == 1:
        axes = [axes]

    x_min = date_to_num(start_date)
    x_max = date_to_num(end_date)

    for ax, sensor in zip(axes, sensors, strict=False):
        info = sensor_results[sensor]
        stac_dates = info["stac_dates"]
        milvus_dates = info["milvus_dates"]
        modality = info["modality"]

        missing = sorted(stac_dates - milvus_dates)
        done = sorted(stac_dates & milvus_dates)
        extra = sorted(milvus_dates - stac_dates)  # in Milvus but not in STAC query

        for d in missing:
            ax.axvline(date_to_num(d), color="red", alpha=0.6, linewidth=1.2)
        for d in done:
            ax.axvline(date_to_num(d), color="green", alpha=0.7, linewidth=1.2)
        for d in extra:
            ax.axvline(date_to_num(d), color="blue", alpha=0.4, linewidth=0.8)

        total = len(stac_dates)
        processed = len(done)
        pct = 100 * processed / total if total else 0
        ax.set_title(
            f"{sensor}  ({modality})   {processed}/{total} processed  ({pct:.0f}%)"
            f"   [{len(extra)} extra in Milvus]",
            loc="left",
            fontsize=11,
        )
        ax.set_xlim(x_min, x_max)
        ax.set_yticks([])
        ax.set_ylabel(sensor, fontsize=10, rotation=0, labelpad=50, va="center")
        ax.grid(axis="x", linestyle="--", alpha=0.3)

    # X-axis year ticks
    ax = axes[-1]
    start_year = int(start_date[:4])
    end_year = int(end_date[:4]) + 1
    year_ticks = [date_to_num(f"{y}-01-01") for y in range(start_year, end_year + 1)]
    year_labels = [str(y) for y in range(start_year, end_year + 1)]
    ax.set_xticks(year_ticks)
    ax.set_xticklabels(year_labels, fontsize=9)
    ax.set_xlabel("Date", fontsize=10)

    # Legend
    fig.legend(
        handles=[
            mpatches.Patch(color="green", alpha=0.7, label="Processed (in STAC + Milvus)"),
            mpatches.Patch(color="red", alpha=0.6, label="Missing (in STAC, not in Milvus)"),
            mpatches.Patch(color="blue", alpha=0.4, label="Extra (in Milvus only)"),
        ],
        loc="upper right",
        fontsize=10,
    )

    fig.suptitle(f"Timeline coverage  ({start_date} -> {end_date})", fontsize=13, y=1.01)
    fig.tight_layout()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved to {output_path}")


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
        "--skip-stac", action="store_true", help="Skip STAC query (show only Milvus)"
    )
    args = parser.parse_args()

    gdf = gpd.read_file(args.geojson)
    bbox = list(gdf.total_bounds)

    sensor_results = {}

    for sensor, cfg in tqdm(SENSORS.items(), desc="Sensors"):
        print(f"\n[{sensor}]")

        if args.skip_stac:
            stac_dates = set()
            print("  STAC query skipped.")
        else:
            print(f"  Querying STAC ({cfg['stac_collection']})...")
            stac_dates = fetch_stac_dates(cfg, bbox, args.start, args.end)
            print(f"  STAC dates: {len(stac_dates)}")

        print(f"  Querying Milvus (modality={cfg['modality']})...")
        milvus_dates = fetch_milvus_dates(
            args.collection,
            cfg["modality"],
            args.milvus_host,
            args.milvus_port,
        )
        print(f"  Milvus dates: {len(milvus_dates)}")

        if stac_dates:
            missing = stac_dates - milvus_dates
            print(f"  Missing: {len(missing)}")

        sensor_results[sensor] = {
            "stac_dates": stac_dates,
            "milvus_dates": milvus_dates,
            "color": cfg["color"],
            "modality": cfg["modality"],
        }

    print()
    plot_timeline(sensor_results, args.output, args.start, args.end)


if __name__ == "__main__":
    main()
