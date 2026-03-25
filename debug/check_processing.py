import argparse

from pymilvus import Collection, connections, utility


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5, help="Number of records to fetch")
    args = parser.parse_args()

    COLLECTION_NAME = "high_res_hun_2021"

    print("Connecting to Milvus on localhost:19530...")
    connections.connect("default", host="localhost", port="19530")

    if not utility.has_collection(COLLECTION_NAME):
        print(f"ERROR: Collection '{COLLECTION_NAME}' does not exist!")
        return

    # Load the collection into memory for searching/querying
    col = Collection(COLLECTION_NAME)
    col.load()

    # Check total embedded entities
    total_count = col.num_entities
    print(f"\nTotal loaded rows in {COLLECTION_NAME}: {total_count}")

    if total_count == 0:
        print("Collection is currently empty.")
        return

    # Fetch a few example records
    # We output the specific metadata fields that were inserted during the loading script
    output_fields = ["id", "lat", "lon", "year", "z"]

    print(f"\nFetching top {args.limit} examples...")

    # We query using a dummy expression so it yields the first few sequential items
    # (Since IDs are auto-generated and >= 0)
    results = col.query(expr="id >= 0", output_fields=output_fields, limit=args.limit)

    for item in results:
        print(
            f"ID: {item['id']} | Lat: {item['lat']:.4f}, Lon: {item['lon']:.4f} | Year: {item['year']} | Zoom: {item['z']}"
        )

    # If you also want to inspect the actual 64-dim float32 vector itself:
    print("\nExtracting vector for the first record...")
    vector_res = col.query(expr=f"id == {results[0]['id']}", output_fields=["vector"], limit=1)
    if vector_res:
        vec = vector_res[0]["vector"]
        print(f"Vector dimensions: {len(vec)}")
        print(f"Vector preview snippet: {vec[:5]} ... {vec[-5:]}")


if __name__ == "__main__":
    main()
