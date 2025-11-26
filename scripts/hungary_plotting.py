import argparse
import os

import geopandas as gpd
import matplotlib.pyplot as plt
from pymilvus import Collection, connections, list_collections


def plot_hungary_distribution(
    year,
    collection_name="hungary_embeddings",
    map_file="hungary.geojson",
    save_file="hungary_distribution.png",
    zoom=None,
    style="kde",
):
    # Connect to Milvus
    try:
        connections.connect(alias="default", host="localhost", port=19530)
    except Exception:
        # Already connected or connection error handled later
        pass

    if collection_name not in list_collections():  # Fallback check
        pass

    try:
        collection = Collection(collection_name)
    except Exception:
        # Fallback to the other one if the first one fails or doesn't exist
        collection_name = "hungary_with_neighbors_embeddings"
        collection = Collection(collection_name)

    print(f"Querying collection: {collection_name} for year: {year}, zoom: {zoom}")

    # Query data
    expr = f"year == {year}"
    if zoom is not None:
        expr += f" and z == {zoom}"

    iterator = collection.query_iterator(expr=expr, output_fields=["lat", "lon"], batch_size=5000)

    lats = []
    lons = []

    # Hungary Bounding Box (approximate)
    lat_min, lat_max = 45.5, 49.0
    lon_min, lon_max = 16.0, 23.0

    while True:
        batch = iterator.next()
        if not batch:
            iterator.close()
            break

        for record in batch:
            # Filter by bounding box to exclude outliers like (0,0)
            if lat_min <= record["lat"] <= lat_max and lon_min <= record["lon"] <= lon_max:
                lats.append(record["lat"])
                lons.append(record["lon"])

    print(f"Found {len(lats)} valid points (filtered by bbox).")

    # Check Z levels
    z_levels = set()
    # We need to fetch z to check this.
    # But we only fetched lat/lon.
    # Let's assume we want to check z distribution if we fetched it.
    # For now, let's just proceed with plotting.

    if not lats:
        print("No data found for the given year.")
        return None

    # Load Hungary map
    hungary_map = None
    if not os.path.exists(map_file):
        # Try fallback location
        fallback_map = "processed/hungary_with_neighbors.geojson"
        if os.path.exists(fallback_map):
            print(f"Map file {map_file} not found, using fallback: {fallback_map}")
            map_file = fallback_map
        else:
            print(
                f"Warning: Map file {map_file} not found and fallback {fallback_map} also missing. Plotting without map overlay."
            )

    if os.path.exists(map_file):
        try:
            hungary_map = gpd.read_file(map_file)
        except Exception as e:
            print(f"Warning: Error loading map file: {e}")

    # Plotting
    fig, ax = plt.subplots(figsize=(12, 8))

    # Plot Hungary background (Green for sparse areas)
    if hungary_map is not None:
        hungary_map.plot(ax=ax, color="#90EE90", edgecolor="black", alpha=0.5)  # Light green

    if style == "kde":
        # Calculate KDE
        import numpy as np
        from scipy.stats import gaussian_kde

        # Define grid
        x_grid = np.linspace(lon_min, lon_max, 200)
        y_grid = np.linspace(lat_min, lat_max, 200)
        X, Y = np.meshgrid(x_grid, y_grid)
        positions = np.vstack([X.ravel(), Y.ravel()])
        values = np.vstack([lons, lats])

        print("Calculating KDE...")
        kernel = gaussian_kde(values)
        Z = np.reshape(kernel(positions).T, X.shape)

        # Plot KDE
        # Use contourf for filled contours
        # cmap="RdYlGn_r" means Green (low) -> Red (high)
        cf = ax.contourf(X, Y, Z, cmap="RdYlGn_r", alpha=0.8, levels=20)
        cb = fig.colorbar(cf, ax=ax, label="Density")
    else:
        # Use hexbin for distribution map
        # User wants: Red = dense, Green = sparse.
        # We use RdYlGn_r (Green -> Red)
        # mincnt=1 ensures empty bins are transparent, revealing the green background
        hb = ax.hexbin(
            lons, lats, gridsize=100, cmap="RdYlGn_r", mincnt=1, bins="log", alpha=0.8
        )
        cb = fig.colorbar(hb, ax=ax, label="Log Count")

    plt.title(f"Distribution of Coordinates for Year {year}")
    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    plt.grid(True, linestyle="--", alpha=0.3)

    # Set limits to bounding box + margin
    margin = 0.1
    ax.set_xlim(lon_min - margin, lon_max + margin)
    ax.set_ylim(lat_min - margin, lat_max + margin)

    if save_file:
        plt.savefig(save_file)
        print(f"Plot saved to {save_file}")
    else:
        plt.show()
    return save_file


def main():
    parser = argparse.ArgumentParser(
        description="Plot distribution of lat/lon coordinates filtered by year."
    )
    parser.add_argument("--year", type=int, required=True, help="Year to filter the data by.")
    parser.add_argument(
        "--map-file",
        type=str,
        default="hungary.geojson",
        help="Path to the Hungary map file (geojson).",
    )
    parser.add_argument(
        "--save-file",
        type=str,
        default="hungary_distribution.png",
        help="Path to save the plot (png).",
    )
    parser.add_argument("--zoom", type=int, help="Zoom level to filter by (e.g., 16, 32).")
    parser.add_argument(
        "--style",
        type=str,
        default="kde",
        choices=["kde", "hexbin"],
        help="Plotting style: 'kde' (continuous) or 'hexbin' (dots/bins).",
    )
    args = parser.parse_args()

    plot_hungary_distribution(
        args.year,
        map_file=args.map_file,
        save_file=args.save_file,
        zoom=args.zoom,
        style=args.style,
    )


if __name__ == "__main__":
    main()
