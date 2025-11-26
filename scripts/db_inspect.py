# %%

from pymilvus import Collection, connections, list_collections

# %%
# Connect to Milvus

connections.connect(alias="default", host="localhost", port=19530)

# %%
# List all collections

collections = list_collections()
print("Available collections:")
for collection_name in collections:
    print(f"  - {collection_name}")

# %%
# Print collection details

for collection_name in collections:
    collection = Collection(collection_name)
    print(f"\nCollection: {collection_name}")
    print(f"  Num entities: {collection.num_entities}")
    print(f"  Schema: {collection.schema}")

# %%
# Print collection stats

for collection_name in collections:
    collection = Collection(collection_name)
    print(f"\nCollection: {collection_name}")
    print(f"  Num entities: {collection.num_entities}")
    print(f"  Schema: {collection.schema}")
    # Query distinct years
    res = collection.query(expr="year >= 0", output_fields=["year"])
    years = sorted({r["year"] for r in res})
    print(f"Available years: {years}")

# %%
from hungary_plotting import plot_hungary_distribution

plot_hungary_distribution(
    2024,
    map_file="../hungary.geojson",
    collection_name="high_res_hun",
    style="hexbin",
)
# %%

# %%
# Check for all-zero embedding vectors

import numpy as np

collection = Collection("high_res_hun")
collection.load()  # Ensure collection is loaded

print(f"\nChecking {collection_name} for all-zero embeddings...")

# Use iterator for efficient batch processing
iterator = collection.query_iterator(expr="", output_fields=["id", "vector"], batch_size=5000)

zero_count = 0
total_checked = 0

while True:
    results = iterator.next()
    if not results:
        break

    for result in results:
        vector = np.array(result["vector"])
        if np.all(vector == 0):
            zero_count += 1
            print(f"  Found all-zero vector: ID={result['id']}")

    total_checked += len(results)

    # Print progress
    if total_checked % 10000 == 0:
        print(f"  Checked {total_checked} vectors...")

iterator.close()

print(f"  Total vectors checked: {total_checked}")
print(f"  All-zero vectors found: {zero_count}")
if zero_count > 0:
    print(f"  ⚠️  WARNING: {zero_count}/{total_checked} vectors are all zeros!")
else:
    print("  ✓ No all-zero vectors found")

# %%

# %%
# Query random location and find similar vectors

import random

collection = Collection("high_res_hun")
collection.load()

# Get a random entry
print("\nGetting random location...")
# Get min and max IDs first to sample efficiently
res_min = collection.query(expr="id >= 0", output_fields=["id"], limit=1, order_by="id")
res_max = collection.query(expr="id >= 0", output_fields=["id"], limit=1, order_by="id desc")

if res_min and res_max:
    min_id = res_min[0]["id"]
    max_id = res_max[0]["id"]

    # Pick a random ID range to sample from
    start_id = random.randint(min_id, max(min_id, max_id))

    random_results = collection.query(
        expr=f"id >= {start_id}", output_fields=["id", "lat", "lon", "vector"], limit=100
    )
else:
    random_results = []

if random_results:
    # Pick a random entry from the sample
    random_entry = random.choice(random_results)
    print(
        f"Random location: ID={random_entry['id']}, Lat={random_entry['lat']:.4f}, Lon={random_entry['lon']:.4f}"
    )

    # Search for top-k similar vectors
    top_k = 500
    print(f"\nSearching for top {top_k} most similar locations...")

    search_params = {"metric_type": "L2", "params": {"nprobe": 16}}
    results = collection.search(
        data=[random_entry["vector"]],
        anns_field="vector",
        param=search_params,
        limit=top_k,
        output_fields=["id", "lat", "lon", "year"],
    )

    print(f"\nTop {top_k} most similar locations:")
    for i, hit in enumerate(results[0]):
        distance = hit.distance
        cosine_similarity = 1 - (distance**2) / 2
        print(
            f"  {i + 1}. ID={hit.id}, Lat={hit.entity.get('lat'):.4f}, Lon={hit.entity.get('lon'):.4f}, "
            f"Year={hit.entity.get('year')}, Distance={distance:.4f}, Cosine Similarity={cosine_similarity:.8f}"
        )
else:
    print("No results found in collection")

# %%
# Plot the query location and its similar neighbors on a map

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np

if random_results and results:
    # Prepare data for plotting
    query_lat = random_entry["lat"]
    query_lon = random_entry["lon"]

    similar_lats = [hit.entity.get("lat") for hit in results[0]]
    similar_lons = [hit.entity.get("lon") for hit in results[0]]
    distances = [hit.distance for hit in results[0]]

    # Load Hungary map
    map_file = "../hungary.geojson"
    hungary_map = None
    if os.path.exists(map_file):
        try:
            hungary_map = gpd.read_file(map_file)
        except Exception as e:
            print(f"Warning: Error loading map file: {e}")

    # Create plot
    fig, ax = plt.subplots(figsize=(12, 8))

    # Plot Hungary background
    if hungary_map is not None:
        hungary_map.plot(ax=ax, color="#90EE90", edgecolor="black", alpha=0.5)

    # Plot similar locations (sized by inverse distance - closer = larger)
    max_distance = max(distances) if max(distances) > 0 else 1
    sizes = [100 * (1 - d / max_distance) + 20 for d in distances]  # Scale sizes
    scatter = ax.scatter(
        similar_lons,
        similar_lats,
        c=distances,
        cmap="RdYlGn",
        s=sizes,
        alpha=0.7,
        edgecolors="black",
        linewidth=1,
        label="Similar locations",
    )

    # Plot query location (large red star)
    ax.scatter(
        query_lon,
        query_lat,
        marker="*",
        s=500,
        c="red",
        edgecolors="black",
        linewidth=2,
        label="Query location",
        zorder=10,
    )

    # Add colorbar
    cbar = plt.colorbar(scatter, ax=ax, label="Distance (L2)")

    # Add legend
    ax.legend(loc="upper right")

    plt.title(f"Query Location and Top {top_k} Similar Neighbors")
    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    plt.grid(True, linestyle="--", alpha=0.3)

    # Auto-adjust limits to show all points with margin
    all_lons = [*similar_lons, query_lon]
    all_lats = [*similar_lats, query_lat]
    margin = 0.05
    ax.set_xlim(min(all_lons) - margin, max(all_lons) + margin)
    ax.set_ylim(min(all_lats) - margin, max(all_lats) + margin)

    plt.tight_layout()
    plt.show()


# %%
