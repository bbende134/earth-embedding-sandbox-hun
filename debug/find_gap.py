import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from pymilvus import Collection, connections
from shapely.geometry import box


def main():
    # Connect to Milvus
    try:
        connections.connect(alias="default", host="localhost", port=19530)
    except Exception as e:
        print(f"Failed to connect to Milvus: {e}")
        return

    collection_name = "high_res_hun"
    try:
        collection = Collection(collection_name)
        collection.load()
    except Exception as e:
        print(f"Failed to load collection {collection_name}: {e}")
        return

    print(f"Collection {collection_name} loaded. Num entities: {collection.num_entities}")

    # Load Hungary map
    map_file = "hungary.geojson"
    try:
        hungary_gdf = gpd.read_file(map_file)
        hungary_poly = hungary_gdf.geometry.iloc[0]
    except Exception as e:
        print(f"Failed to load {map_file}: {e}")
        return

    # Define grid
    minx, miny, maxx, maxy = hungary_gdf.total_bounds
    step = 0.1  # approx 11km

    x_range = np.arange(minx, maxx, step)
    y_range = np.arange(miny, maxy, step)

    print("Scanning grid for gaps...")
    for x in x_range:
        for y in y_range:
            b = box(x, y, x + step, y + step)

            # Check if box intersects Hungary significantly (e.g. centroid)
            if not b.centroid.within(hungary_poly):
                continue

            # Query Milvus for points in this box
            # We can't easily do a range query on lat/lon without partitions or scalar index
            # But we can try to just fetch all data and bin it in python if dataset is small enough
            # OR use the iterator to fetch all and bin.

    # Fetching all data might be slow if millions of points.
    # Let's try fetching all lat/lon only.
    iterator = collection.query_iterator(
        expr="year == 2024", output_fields=["lat", "lon"], batch_size=10000
    )

    points = []
    while True:
        res = iterator.next()
        if not res:
            break
        for r in res:
            points.append((r["lon"], r["lat"]))

    print(f"Fetched {len(points)} points.")

    if not points:
        print("No points found for 2024.")
        return

    # Binning
    # Create a 2D histogram
    heatmap, xedges, yedges = np.histogram2d(
        [p[0] for p in points],
        [p[1] for p in points],
        bins=[len(x_range), len(y_range)],
        range=[[minx, maxx], [miny, maxy]],
    )

    # Find empty bins that are inside Hungary
    gaps = []
    for i in range(len(x_range)):
        for j in range(len(y_range)):
            if heatmap[i, j] == 0:
                # Check if this bin is inside Hungary
                # Construct box
                b = box(xedges[i], yedges[j], xedges[i + 1], yedges[j + 1])
                if b.centroid.within(hungary_poly):
                    gaps.append(b)

    print(f"Found {len(gaps)} empty grid cells inside Hungary.")

    if not gaps:
        print("No gaps found.")
        return

    # Merge gaps? Or just take the bounding box of all gaps?
    # Let's create a GeoDataFrame of gaps
    gaps_gdf = gpd.GeoDataFrame({"geometry": gaps}, crs=hungary_gdf.crs)

    # Dissolve to find connected components
    dissolved = gaps_gdf.dissolve()

    # Get the largest gap
    if dissolved.empty:
        print("No dissolved gaps.")
        return

    largest_gap = dissolved.geometry.iloc[0]  # Might be MultiPolygon

    print(f"Largest gap geometry: {largest_gap}")

    # Save gap to file
    gaps_gdf.to_file("debug/detected_gaps.geojson", driver="GeoJSON")
    print("Saved debug/detected_gaps.geojson")

    # Plot
    _fig, ax = plt.subplots(figsize=(12, 8))
    hungary_gdf.plot(ax=ax, color="lightgreen", alpha=0.5, edgecolor="black")
    gaps_gdf.plot(ax=ax, color="red", alpha=0.7)
    plt.title("Detected Data Gaps (Red)")
    plt.savefig("debug/detected_gaps.png")
    print("Saved debug/detected_gaps.png")


if __name__ == "__main__":
    main()
