#!/usr/bin/env python3
"""
Comprehensive test plan:
1. Check what's in GCS
2. Test extraction with --scale=10 on smaller geometry
3. Verify pipeline works end-to-end
4. Compare coverage with original
"""

from google.cloud import storage


def check_gcs_status():
    """Check what data exists in GCS."""
    print("\n" + "=" * 80)
    print("  GCS CURRENT STATUS")
    print("=" * 80 + "\n")

    client = storage.Client()
    bucket = client.bucket("earth-embeddings-hungary-output")

    # List objects
    blobs = list(bucket.list_blobs(max_results=50))

    if not blobs:
        print("✓ Output bucket is empty (clean state for testing)")
    else:
        print(f"✓ Output bucket contains {len(blobs)} objects:")

        # Group by prefix
        prefixes = {}
        for blob in blobs:
            prefix = blob.name.split("/")[0]
            if prefix not in prefixes:
                prefixes[prefix] = 0
            prefixes[prefix] += 1

        for prefix in sorted(prefixes.keys()):
            print(f"    {prefix}: {prefixes[prefix]} blobs")

    print()


def test_extraction_small_scale():
    """Test extraction with --scale=10."""
    print("=" * 80)
    print("  TEST PLAN: Extract with --scale=10")
    print("=" * 80 + "\n")

    print("Purpose: Test if extraction works with different scale factor")
    print("Input: hungary.geojson (full country area)")
    print("Scale: 10m resolution (vs original 100m)")
    print("Expected result: Higher resolution embeddings (~10x more records)\n")

    print("Steps:")
    print("  1. Run: bash test_pipeline_scale10.sh")
    print("  2. Wait for all 4 pipeline stages to complete (~30-40 minutes)")
    print("  3. Verify data in collection: hungary_with_neighbors_embeddings_v2")
    print("  4. Check coverage compared to original\n")

    print("Estimated time: 30-40 minutes")
    print("Storage impact: ~2-3 GB in GCS\n")


def verify_collection_status():
    """Check both collections."""
    print("=" * 80)
    print("  COLLECTION STATUS")
    print("=" * 80 + "\n")

    from pymilvus import Collection, connections

    connections.connect(host="localhost", port="19530")

    # Check original
    try:
        col_orig = Collection("hungary_with_neighbors_embeddings")
        count_orig = col_orig.num_entities
        print(f"Original collection: {count_orig:,} entities")
    except Exception as e:
        print(f"Original collection: ERROR - {e}")

    # Check v2
    try:
        col_v2 = Collection("hungary_with_neighbors_embeddings_v2")
        count_v2 = col_v2.num_entities
        print(f"V2 collection: {count_v2:,} entities")
    except Exception as e:
        print(f"V2 collection: ERROR - {e}")

    connections.disconnect("default")
    print()


def show_test_commands():
    """Show the commands to run."""
    print("=" * 80)
    print("  COMMANDS TO RUN")
    print("=" * 80 + "\n")

    print("1️⃣  Run the pipeline test:")
    print("   bash test_pipeline_scale10.sh\n")

    print("2️⃣  While waiting, check progress:")
    print(
        '   .venv/bin/python -c "from google.cloud import storage; '
        "b = storage.Client().bucket('earth-embeddings-hungary-output'); "
        "print(f'GCS objects: {len(list(b.list_blobs()))}')\""
    )
    print()

    print("3️⃣  After completion, verify data:")
    print("   .venv/bin/python analyze_milvus_records.py  # will need to be updated for v2")
    print()

    print("4️⃣  Compare coverage:")
    print("   .venv/bin/python full_coverage_analysis.py  # will need to be updated for v2")
    print()

    print("5️⃣  Cleanup when done:")
    print(
        '   .venv/bin/python -c "from pymilvus import connections, utility; '
        "connections.connect(); utility.drop_collection('hungary_with_neighbors_embeddings_v2')\""
    )
    print()


if __name__ == "__main__":
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 20 + "PIPELINE TESTING PLAN (--scale=10)" + " " * 24 + "║")
    print("╚" + "=" * 78 + "╝")

    check_gcs_status()
    test_extraction_small_scale()
    verify_collection_status()
    show_test_commands()

    print("=" * 80)
    print("  NEXT STEPS")
    print("=" * 80 + "\n")
    print("To start testing:")
    print("  bash test_pipeline_scale10.sh\n")
    print("⏱️  Estimated runtime: 30-40 minutes\n")
    print("IMPORTANT: Do NOT delete data while test is running!\n")
