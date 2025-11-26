import os

import geopandas as gpd
import matplotlib.pyplot as plt
from shapely.geometry import box


def main():
    # Load Hungary map
    map_file = "hungary.geojson"
    if not os.path.exists(map_file):
        print(f"File {map_file} not found.")
        return

    gdf = gpd.read_file(map_file)

    # Create candidate missing area box
    # Guessing based on "Hortobágy" / Eastern gap
    # Try: Lon 21.0-22.0, Lat 47.5-48.0
    missing_box = box(21.0, 47.5, 22.0, 48.0)
    missing_gdf = gpd.GeoDataFrame({"geometry": [missing_box]}, crs=gdf.crs)

    # Plot
    _fig, ax = plt.subplots(figsize=(12, 8))
    gdf.plot(ax=ax, color="lightgreen", edgecolor="black", alpha=0.5)
    missing_gdf.plot(ax=ax, color="red", alpha=0.5, edgecolor="red")

    plt.title("Hungary with Candidate Missing Area (Red)")
    plt.savefig("debug/gap_check.png")
    print("Saved debug/gap_check.png")


if __name__ == "__main__":
    main()
