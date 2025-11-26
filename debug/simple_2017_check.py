#!/usr/bin/env python3
"""
Simple 2017 coverage check
"""

import os


def main():
    print("=== 2017 Data Coverage Check ===\n")

    try:
        import gcsfs
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
            scopes=["https://www.googleapis.com/auth/devstorage.read_write"],
        )
        fs = gcsfs.GCSFileSystem(token=credentials)

        bucket = "earth-embeddings-hungary-output"

        # Get all files in bucket
        all_files = fs.ls(bucket, detail=False)
        print(f"Total files in bucket: {len(all_files)}")

        # Check for 2017 data
        year_2017_files = [f for f in all_files if "_2017_" in f]
        print(f"Files with '_2017_': {len(year_2017_files)}")

        # Check for hungary_with_neighbors 2017
        hungary_2017 = [f for f in all_files if "hungary_with_neighbors_2017" in f]
        print(f"hungary_with_neighbors 2017 files: {len(hungary_2017)}")

        if hungary_2017:
            print("\n✓ 2017 data HAS BEEN PROCESSED for hungary_with_neighbors")
            print("Available zoom levels:")
            for f in sorted(hungary_2017):
                zoom_level = f.split("_z")[-1] if "_z" in f else "unknown"
                print(f"  - z{zoom_level}")
        else:
            print("\n✗ 2017 data has NOT been processed for hungary_with_neighbors")

        # Check Milvus (simple count)
        try:
            from pymilvus import Collection, connections

            connections.connect(host="localhost", port="19530")
            col = Collection("hungary_with_neighbors_embeddings")
            col.load()

            # Count 2017 records
            count_result = col.query("year == 2017", output_fields=["count(*)"])
            if count_result:
                count = count_result[0]["count(*)"]
                print(f"\nMilvus records for 2017: {count}")
            else:
                print("\nMilvus records for 2017: 0")

        except Exception as e:
            print(f"\nMilvus check failed: {e}")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
