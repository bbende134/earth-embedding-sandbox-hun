import geopandas as gpd
import os

def main():
    f = "missing_areas_2017.geojson"
    if os.path.exists(f):
        gdf = gpd.read_file(f)
        print(f"Loaded {f}")
        print(f"Rows: {len(gdf)}")
        print(f"Bounds: {gdf.total_bounds}")
        print(f"Head: {gdf.head()}")
    else:
        print(f"{f} not found")

if __name__ == "__main__":
    main()
