# %%
import math
from collections import defaultdict

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
# Get collection info
# collection_name = collections[0] if collections else None

for collection_name in collections:
    if collection_name:
        collection = Collection(collection_name)
        print(f"\nCollection: {collection_name}")
        print(f"  Num entities: {collection.num_entities}")
        print(f"  Schema: {collection.schema}")

# %%
# Sample data from collection
# for collection_name in collections:
#     if collection_name:
#         collection = Collection(collection_name)
#         collection.load()

#         # Get sample of first 10 records
#         sample_data = collection.query(
#             expr="",
#             output_fields=["*"],
#             limit=10
#         )

#         print(f"\nSample data from {collection_name}:")
#         for record in sample_data:
#             print(record)

# %%
# Delete records with all-zero embeddings from hungary_embeddings
collection_name = "hungary_with_neighbors_embeddings"
collection = Collection(collection_name)

# Use query_iterator to iterate over all records without offset limits
all_ids_to_delete = []

iterator = collection.query_iterator(expr="", output_fields=["id", "embedding"], batch_size=5000)

batch_count = 0
while True:
    batch = iterator.next()
    if not batch:
        iterator.close()
        break

    # Filter records where all embedding values are 0.0
    ids_to_delete = [
        record["id"] for record in batch if all(val == 0.0 for val in record["embedding"])
    ]

    # Validate that all records in ids_to_delete truly have zero embeddings
    print(f"Validating {len(ids_to_delete)} records with zero embeddings (batch {batch_count})...")
    for record in batch:
        if record["id"] in ids_to_delete:
            has_all_zeros = all(val == 0.0 for val in record["embedding"])
            if not has_all_zeros:
                print(
                    f"WARNING: Record {record['id']} marked for deletion but has non-zero values: {record['embedding'][:5]}..."
                )
                ids_to_delete.remove(record["id"])

    all_ids_to_delete.extend(ids_to_delete)
    print(
        f"Validation complete. {len(ids_to_delete)} records confirmed to have all-zero embeddings."
    )

    batch_count += 1
# %%
# Delete all collected records
if all_ids_to_delete:
    collection.delete(expr=f"id in {all_ids_to_delete}")
    print(f"Deleted {len(all_ids_to_delete)} records with zero embeddings from {collection_name}")
else:
    print("No zero embedding records found")

# %%
# Check for duplicate inputs based on similar lat/lon coordinates


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two coordinates in kilometers"""
    R = 6371  # Earth's radius in km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


collection_name = "hungary_with_neighbors_embeddings"
collection = Collection(collection_name)

# Check for exact lat/lon duplicates
location_to_ids = defaultdict(list)

iterator = collection.query_iterator(expr="", output_fields=["id", "lat", "lon"], batch_size=5000)

batch_count = 0
while True:
    batch = iterator.next()
    if not batch:
        iterator.close()
        break

    for record in batch:
        key = (record["lat"], record["lon"])
        location_to_ids[key].append(record["id"])

    batch_count += 1

exact_duplicates = {loc: ids for loc, ids in location_to_ids.items() if len(ids) > 1}

if exact_duplicates:
    print(
        f"\nFound {len(exact_duplicates)} locations with exact lat/lon duplicates in {collection_name}:"
    )
    for loc, ids in exact_duplicates.items():
        print(f"  Location: ({loc[0]}, {loc[1]})")
        print(f"    IDs: {ids}")
        print(f"    Count: {len(ids)}")
else:
    print(f"\nNo exact lat/lon duplicates found in {collection_name}")
# %%
# Collect all duplicate IDs
all_duplicate_ids = set()

# Add IDs from exact duplicates
for ids in exact_duplicates.values():
    all_duplicate_ids.update(ids)
# %%
# Find duplicates based on lat/lon proximity (original logic, but optimized)
records_by_location = defaultdict(list)
distance_threshold_km = 0.01  # Adjust threshold as needed

# Reset iterator
iterator = collection.query_iterator(expr="", output_fields=["id", "lat", "lon"], batch_size=5000)

all_records = []
while True:
    batch = iterator.next()
    if not batch:
        iterator.close()
        break
    all_records.extend(batch)

# Group records by rounded location for proximity check
rounded_location_to_records = defaultdict(list)
precision = 4  # decimal places for rounding
for record in all_records:
    key = (round(record["lat"], precision), round(record["lon"], precision))
    rounded_location_to_records[key].append(record)

# Find groups with multiple records at same rounded location
proximity_duplicates = []
for loc, records in rounded_location_to_records.items():
    if len(records) > 1:
        # Check pairwise distances within the group
        for i, rec1 in enumerate(records):
            for rec2 in records[i + 1 :]:
                distance = haversine_distance(rec1["lat"], rec1["lon"], rec2["lat"], rec2["lon"])
                if distance < distance_threshold_km:
                    proximity_duplicates.append(
                        {
                            "id1": rec1["id"],
                            "id2": rec2["id"],
                            "lat1": rec1["lat"],
                            "lon1": rec1["lon"],
                            "lat2": rec2["lat"],
                            "lon2": rec2["lon"],
                            "distance_km": distance,
                        }
                    )
                    all_duplicate_ids.add(rec1["id"])
                    all_duplicate_ids.add(rec2["id"])

if proximity_duplicates:
    print(
        f"\nFound {len(proximity_duplicates)} duplicate locations (within {distance_threshold_km} km) in {collection_name}:"
    )
    for dup in proximity_duplicates:
        print(f"  IDs: {dup['id1']}, {dup['id2']}")
        print(f"    Location 1: ({dup['lat1']}, {dup['lon1']})")
        print(f"    Location 2: ({dup['lat2']}, {dup['lon2']})")
        print(f"    Distance: {dup['distance_km']:.6f} km")
else:
    print(f"\nNo proximity-based duplicate locations found in {collection_name}")

# Extract only the duplicate IDs
duplicate_ids_list = sorted(list(all_duplicate_ids))
print(f"\nTotal duplicate IDs found: {len(duplicate_ids_list)}")
print("Duplicate IDs:", duplicate_ids_list)
# %%

# %%
# Delete all collected records
if exact_duplicates:
    collection.delete(expr=f"id in {all_ids_to_delete}")
    print(f"Deleted {len(all_ids_to_delete)} records with zero embeddings from {collection_name}")
else:
    print("No zero embedding records found")

# %%
# Find duplicates based on lat/lon proximity (original logic, but optimized)
records_by_location = defaultdict(list)
distance_threshold_km = 0.01  # Adjust threshold as needed

# Reset iterator
iterator = collection.query_iterator(expr="", output_fields=["id", "lat", "lon"], batch_size=5000)

all_records = []
while True:
    batch = iterator.next()
    if not batch:
        iterator.close()
        break
    all_records.extend(batch)

# Group records by rounded location for proximity check
rounded_location_to_records = defaultdict(list)
precision = 4  # decimal places for rounding
for record in all_records:
    key = (round(record["lat"], precision), round(record["lon"], precision))
    rounded_location_to_records[key].append(record)

# Find groups with multiple records at same rounded location
proximity_duplicates = []
for loc, records in rounded_location_to_records.items():
    if len(records) > 1:
        # Check pairwise distances within the group
        for i, rec1 in enumerate(records):
            for rec2 in records[i + 1 :]:
                distance = haversine_distance(rec1["lat"], rec1["lon"], rec2["lat"], rec2["lon"])
                if distance < distance_threshold_km:
                    proximity_duplicates.append(
                        {
                            "id1": rec1["id"],
                            "id2": rec2["id"],
                            "lat1": rec1["lat"],
                            "lon1": rec1["lon"],
                            "lat2": rec2["lat"],
                            "lon2": rec2["lon"],
                            "distance_km": distance,
                        }
                    )

if proximity_duplicates:
    print(
        f"\nFound {len(proximity_duplicates)} duplicate locations (within {distance_threshold_km} km) in {collection_name}:"
    )
    for dup in proximity_duplicates:
        print(f"  IDs: {dup['id1']}, {dup['id2']}")
        print(f"    Location 1: ({dup['lat1']}, {dup['lon1']})")
        print(f"    Location 2: ({dup['lat2']}, {dup['lon2']})")
        print(f"    Distance: {dup['distance_km']:.6f} km")
else:
    print(f"\nNo proximity-based duplicate locations found in {collection_name}")
collection_name = "hungary_embeddings"
collection = Collection(collection_name)

duplicate_ids = {}
iterator = collection.query_iterator(
    expr="", output_fields=["id", "lat", "lon", "z", "year"], batch_size=5000
)

batch_count = 0
while True:
    batch = iterator.next()
    if not batch:
        iterator.close()
        break

    for record in batch:
        # Create a key from the input fields
        input_val = (record["lat"], record["lon"], record["z"], record["year"])
        if input_val not in duplicate_ids:
            duplicate_ids[input_val] = []
        duplicate_ids[input_val].append(record["id"])

    batch_count += 1

duplicates_found = {k: v for k, v in duplicate_ids.items() if len(v) > 1}

if duplicates_found:
    print(f"\nFound {len(duplicates_found)} duplicate inputs in {collection_name}:")
    for input_val, ids in duplicates_found.items():
        print(f"  Input: {input_val}")
        print(f"    IDs: {ids}")
        print(f"    Count: {len(ids)}")
else:
    print(f"\nNo duplicate inputs found in {collection_name}")
# %%
# Close connection
connections.disconnect(alias="default")
print("\nDisconnected from Milvus")
