import random

import numpy as np
from pymilvus import Collection, connections

# Connect to Milvus
print("Connecting to Milvus...")
connections.connect(alias="default", host="localhost", port=19530)

print("\n" + "=" * 50)
print("VERIFICATION: Checking Embedding Variance")
print("=" * 50)

collection_name = "terra_S2L2A"
print(f"Loading collection: {collection_name}")
collection = Collection(collection_name)
collection.load()

# Get min and max IDs to sample from valid range
res_min = collection.query(expr="id >= 0", output_fields=["id"], limit=1, order_by="id")
res_max = collection.query(expr="id >= 0", output_fields=["id"], limit=1, order_by="id desc")

if res_min and res_max:
    min_id = res_min[0]["id"]
    max_id = res_max[0]["id"]
    # Ensure we don't go out of bounds
    search_max = max(min_id, max_id - 1000)
    start_id = random.randint(min_id, search_max)

    print(f"Sampling embeddings starting from ID {start_id}...")

    results = collection.query(
        expr=f"id >= {start_id}", output_fields=["id", "embedding"], limit=100
    )

    if results:
        embeddings = np.array([r["embedding"] for r in results])
        ids = [r["id"] for r in results]

        print(f"Analyzed {len(embeddings)} embeddings.")

        # Check for identical embeddings
        unique_embeddings = np.unique(embeddings, axis=0)
        print(f"Number of unique embeddings: {len(unique_embeddings)} / {len(embeddings)}")

        if len(unique_embeddings) == 1:
            print("\n⚠️  CRITICAL: ALL EMBEDDINGS ARE IDENTICAL!")
            print(f"Embedding vector (first 20 dims): {embeddings[0][:20]}")
        else:
            print("\n✓ Embeddings show variation.")

            # Calculate pairwise distances (Euclidean)
            dists = []
            # Check a subset to avoid O(N^2) if N is large, though 100 is fine
            sample_size = min(len(embeddings), 50)
            for i in range(sample_size):
                for j in range(i + 1, sample_size):
                    dist = np.linalg.norm(embeddings[i] - embeddings[j])
                    dists.append(dist)

            if dists:
                print(f"Average pairwise distance (sample): {np.mean(dists):.4f}")
                print(f"Min pairwise distance (sample): {np.min(dists):.4f}")
                print(f"Max pairwise distance (sample): {np.max(dists):.4f}")

            # Check standard deviation
            std_per_dim = np.std(embeddings, axis=0)
            print(f"Average Std Dev per dimension: {np.mean(std_per_dim):.6f}")

            # ---------------------------------------------------------
            # Verify Search (ANN Index)
            # ---------------------------------------------------------
            print("\n" + "-" * 30)
            print("Verifying ANN Search Results")
            print("-" * 30)

            # Pick one embedding to search with
            query_embedding = embeddings[0]
            query_id = ids[0]

            # Calculate distances to all other points in the batch
            print("Checking distances within the batch...")
            batch_dists = []
            for i in range(len(embeddings)):
                if i == 0:
                    continue
                d = np.linalg.norm(query_embedding - embeddings[i])
                batch_dists.append((d, ids[i]))

            batch_dists.sort(key=lambda x: x[0])
            min_batch_dist = batch_dists[0][0]
            min_batch_id = batch_dists[0][1]

            print(
                f"Closest neighbor in batch: ID={min_batch_id}, Dist={min_batch_dist:.4f} (Squared={min_batch_dist**2:.4f})"
            )

            search_params = {"metric_type": "L2", "params": {"nprobe": 128}}  # Increased nprobe
            print(f"Searching for neighbors of ID {query_id} (nprobe=128)...")

            try:
                search_results = collection.search(
                    data=[query_embedding],
                    anns_field="embedding",
                    param=search_params,
                    limit=10,
                    output_fields=["id", "embedding"],
                )

                hits = search_results[0]
                print(f"Found {len(hits)} hits.")

                hit_ids = [hit.id for hit in hits]
                hit_distances = [hit.distance for hit in hits]

                print(f"Hit IDs: {hit_ids}")
                print(f"Hit Distances: {hit_distances}")

                # Manual Distance Check
                print("\nManual Distance Check:")
                for i, hit in enumerate(hits):
                    if i > 2:
                        break  # Check first few
                    hit_vec = np.array(hit.entity.get("embedding"))
                    manual_dist = np.linalg.norm(query_embedding - hit_vec)
                    print(
                        f"  Hit {i}: Milvus Dist={hit.distance:.4f}, Manual Dist={manual_dist:.4f}"
                    )

                # Check if we found the batch neighbor
                if min_batch_dist**2 < hit_distances[1] and min_batch_id not in hit_ids:
                    print("\n⚠️  WARNING: Search missed a closer neighbor found in batch!")
                    print(f"   Batch Neighbor: {min_batch_id} (SqDist: {min_batch_dist**2:.4f})")
                    print(f"   Milvus Nearest: {hit_ids[1]} (SqDist: {hit_distances[1]:.4f})")
                else:
                    print("\n✓ Search results seem consistent with batch data.")

            except Exception as e:
                print(f"❌ Search failed: {e}")


else:
    print("Could not determine ID range.")
