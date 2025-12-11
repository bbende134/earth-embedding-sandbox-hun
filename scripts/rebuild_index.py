from pymilvus import Collection, connections

# Connect to Milvus
print("Connecting to Milvus...")
connections.connect(alias="default", host="localhost", port=19530)

collection_name = "terra_S2L2A"
print(f"Loading collection: {collection_name}")
collection = Collection(collection_name)

# Release collection to allow index modification
print("Releasing collection...")
collection.release()

# Drop existing index
print("Dropping existing index...")
collection.drop_index()

# Create new index
print("Creating new index (IVF_FLAT)...")
index_params = {"metric_type": "L2", "index_type": "IVF_FLAT", "params": {"nlist": 1024}}
collection.create_index(field_name="embedding", index_params=index_params)

print("Index created successfully.")

# Load collection back into memory
print("Loading collection...")
collection.load()

print("Done! Index rebuilt and collection loaded.")
