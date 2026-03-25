"""
Verify that the test tile was correctly loaded into Milvus.
"""

import sys

from pymilvus import Collection, connections, utility


def main():
    COLLECTION = "high_res_hun_2021"

    print("Connecting to Milvus...")
    connections.connect("default", host="localhost", port="19530")

    if not utility.has_collection(COLLECTION):
        print(f"ERROR: Collection {COLLECTION} does not exist!")
        sys.exit(1)

    col = Collection(COLLECTION)
    col.load()

    count = col.num_entities
    print(f"Collection count: {count}")

    if count == 0:
        print("ERROR: Collection is empty!")
        sys.exit(1)

    # Query a few vectors to check for non-null
    # Also retrieve IDs for cleanup
    res = col.query(expr="id >= 0", output_fields=["id", "vector"])

    if not res:
        print("ERROR: Query returned no results!")
        sys.exit(1)

    print(f"Retrieved {len(res)} vectors.")

    # Check for non-zero vectors (simple check)
    has_data = False
    ids_to_delete = []

    for item in res:
        ids_to_delete.append(item["id"])
        vec = item["vector"]
        if any(v != 0 for v in vec):
            has_data = True

    if not has_data:
        print("ERROR: All retrieved vectors are zero-filled!")
        # Still cleanup
        col.delete(f"id in {ids_to_delete}")
        sys.exit(1)

    print("✓ Verification SUCCESS: Data found and looks valid.")

    # Cleanup
    print(f"Cleaning up {len(ids_to_delete)} test entries...")
    col.delete(f"id in {ids_to_delete}")
    col.flush()

    # Verify cleanup
    res_after = col.query(expr="id >= 0", output_fields=["id"])
    if len(res_after) == 0:
        print("✓ Cleanup SUCCESS: Test data deleted.")
    else:
        print(f"WARNING: Cleanup might have failed. Found {len(res_after)} items remaining.")

    sys.exit(0)


if __name__ == "__main__":
    main()
