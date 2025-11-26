#!/usr/bin/env python3
"""
Query ALL 2017 data from Milvus without exceeding limits.
Uses efficient pagination to collect all records.
"""

import json

from pymilvus import Collection, connections


def query_all_2017_data():
    """Query all 2017 data using proper pagination."""
    print("🔗 Connecting to Milvus...")
    connections.connect(host="localhost", port="19530")
    col = Collection("hungary_with_neighbors_embeddings")
    col.load()

    print("📊 Querying all 2017 embeddings...\n")

    all_embeddings = []
    batch_size = 1000  # Smaller batches to stay well under the limit
    offset = 0

    while True:
        # Check if we would exceed the limit
        if offset + batch_size > 16384:
            # Adjust the last batch to stay within limit
            remaining = 16384 - offset
            if remaining <= 0:
                print(f"\n⚠️  Reached absolute Milvus limit at {len(all_embeddings)} records")
                break

            batch_size = remaining
            print(f"  Final batch adjusted to {batch_size} records (approaching limit)...")

        try:
            results = col.query(
                expr="year == 2017",
                output_fields=["lat", "lon", "z", "year"],
                limit=batch_size,
                offset=offset,
            )

            if not results:
                print("\n✅ No more records found. Pagination complete.")
                break

            all_embeddings.extend(results)
            offset += batch_size

            print(
                f"  Offset {offset - batch_size:5d}-{offset:5d}: Retrieved {len(results):4d} records (Total: {len(all_embeddings):6d})"
            )

            # If we got fewer results than requested, we've reached the end
            if len(results) < batch_size:
                print(f"\n✅ Reached end of data at {len(all_embeddings)} records")
                break

        except Exception as e:
            print(f"❌ Error during query: {e}")
            break

    return all_embeddings


def analyze_data(embeddings):
    """Analyze the collected data."""
    if not embeddings:
        print("❌ No data collected!")
        return

    print("\n" + "=" * 70)
    print("  DATA ANALYSIS")
    print("=" * 70 + "\n")

    # Basic stats
    print(f"Total records collected: {len(embeddings)}\n")

    # Unique locations
    unique_locations = set()
    for emb in embeddings:
        key = (round(emb["lat"], 6), round(emb["lon"], 6), emb["z"])
        unique_locations.add(key)

    duplicates = len(embeddings) - len(unique_locations)
    print(f"Unique locations: {len(unique_locations)}")
    print(f"Duplicate records: {duplicates}")
    if duplicates > 0:
        print(f"Duplication rate: {(duplicates / len(embeddings)) * 100:.2f}%\n")

    # Geographic bounds
    lats = [e["lat"] for e in embeddings]
    lons = [e["lon"] for e in embeddings]

    print("Geographic bounds:")
    print(f"  Latitude:  {min(lats):.4f} to {max(lats):.4f}")
    print(f"  Longitude: {min(lons):.4f} to {max(lons):.4f}\n")

    # Zoom levels
    z_dist = {}
    for e in embeddings:
        z = e["z"]
        z_dist[z] = z_dist.get(z, 0) + 1

    print("Zoom level distribution:")
    for z in sorted(z_dist.keys()):
        count = z_dist[z]
        pct = (count / len(embeddings)) * 100
        print(f"  z{z:3d}: {count:6d} records ({pct:5.1f}%)")

    print("\n" + "=" * 70)


def save_to_file(embeddings, filename="all_2017_embeddings.json"):
    """Save all embeddings to a file."""
    with open(filename, "w") as f:
        json.dump(embeddings, f, indent=2)
    print(f"\n💾 Saved {len(embeddings)} records to {filename}")


if __name__ == "__main__":
    print("=" * 70)
    print("  QUERY ALL 2017 DATA FROM MILVUS")
    print("=" * 70)
    print()

    embeddings = query_all_2017_data()
    analyze_data(embeddings)

    if embeddings:
        save_to_file(embeddings)
