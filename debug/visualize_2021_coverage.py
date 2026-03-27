#!/usr/bin/env python3
"""
Visualize Milvus Coverage for 2021
Recreates the geographic export grid and queries Milvus for each tile's bounding box.
Generates a coverage map PNG showing what has been successfully processed and loaded.
"""

import argparse
import os
import time

import geopandas as gpd
import matplotlib.pyplot as plt
from pymilvus import Collection, connections, utility
from shapely.geometry import box


def create_grid(geojson_path, tile_size_deg=0.225):
    """Create a grid of tiles covering the geometry (Identical to 0_export_grid.py)."""
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default="hungary.geojson", help="Path to boundary")
    parser.add_argument("--collection", default="high_res_hun_2021", help="Milvus collection")
    parser.add_argument("--output", default="debug/coverage_2018.png", help="Output PNG path")
    args = parser.parse_args()

    if not os.path.exists(args.geojson):
        print(f"ERROR: {args.geojson} not found!")
        return

    # 1. Connect to Milvus
    print(f"Connecting to Milvus (collection: {args.collection})...")
    connections.connect("default", host="localhost", port="19530")

    if not utility.has_collection(args.collection):
        print(f"ERROR: Collection {args.collection} does not exist yet!")
        return

    # Release all other loaded collections to free memory
    for other in utility.list_collections():
        if other != args.collection:
            other_state = str(utility.load_state(other))
            if "NotLoad" not in other_state:
                print(f"Releasing {other} to free memory...")
                Collection(other).release()

    col = Collection(args.collection)
    print("Waiting for collection to be ready...")
    for _attempt in range(20):
        load_state = str(utility.load_state(args.collection))
        print(f"  Load state: {load_state}")
        if "Loaded" in load_state:
            break
        if "NotLoad" in load_state:
            col.load()
        time.sleep(10)
    else:
        print("ERROR: Collection not ready after 200s. Aborting.")
        return
    print(f"Total entities: {col.num_entities}")

    # 2. Recreate the Grid
    print("Recreating export grid...")
    tiles = create_grid(args.geojson, tile_size_deg=0.225)
    print(f"Total tiles in grid: {len(tiles)}")

    covered_tiles = []
    missing_tiles = []

    # 3. Query Milvus for each tile's bounds
    print("Querying Milvus for coverage (this may take a few seconds)...")
    for i, t in enumerate(tiles):
        minx, miny, maxx, maxy = t.bounds

        # Query if any point exists within this box
        # PyMilvus expression syntax: lat and lon bounds
        expr = f"lat >= {miny} && lat <= {maxy} && lon >= {minx} && lon <= {maxx}"

        try:
            res = col.query(expr=expr, output_fields=["id"], limit=1)
            if len(res) > 0:
                covered_tiles.append(t)
            else:
                missing_tiles.append(t)
        except Exception as e:
            print(f"Query error on tile {i}: {e}")
            missing_tiles.append(t)

        if i > 0 and i % 50 == 0:
            print(f"  Processed {i}/{len(tiles)} tiles...")

    print("\nCoverage check complete:")
    print(f"  - Covered (Loaded): {len(covered_tiles)}")
    print(f"  - Missing (Pending): {len(missing_tiles)}")

    # 4. Plotting
    print(f"Generating coverage map: {args.output}...")
    gdf = gpd.read_file(args.geojson)

    fig, ax = plt.subplots(figsize=(12, 8))

    # Plot base map
    gdf.plot(ax=ax, color="lightgrey", edgecolor="black", alpha=0.5)

    # Plot Missing Tiles (Red)
    if missing_tiles:
        missing_gdf = gpd.GeoDataFrame({"geometry": missing_tiles}, crs=gdf.crs)
        missing_gdf.plot(ax=ax, color="red", alpha=0.3, edgecolor="darkred", label="Pending")

    # Plot Covered Tiles (Green)
    if covered_tiles:
        covered_gdf = gpd.GeoDataFrame({"geometry": covered_tiles}, crs=gdf.crs)
        covered_gdf.plot(ax=ax, color="green", alpha=0.5, edgecolor="darkgreen", label="Loaded")

    title = (
        f"Milvus Collection Coverage: {args.collection}\n"
        f"Loaded: {len(covered_tiles)} / {len(tiles)} tiles"
    )
    plt.title(title)

    # Custom legend
    import matplotlib.patches as mpatches

    green_patch = mpatches.Patch(color="green", alpha=0.5, label="Loaded in Milvus")
    red_patch = mpatches.Patch(color="red", alpha=0.3, label="Pending Extraction")
    plt.legend(handles=[green_patch, red_patch], loc="lower left")

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    plt.savefig(args.output, dpi=300, bbox_inches="tight")
    print(f"✓ Coverage map saved to {args.output}")


if __name__ == "__main__":
    main()
