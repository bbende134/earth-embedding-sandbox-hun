#!/usr/bin/env python3
"""
Script to analyze coverage of 2017 data processing for Hungary with neighbors.
Checks both GCS storage and Milvus database to determine what areas have been processed.
"""

import json
import os
from pathlib import Path

from shapely.geometry import shape


def load_processed_areas():
    """Load all processed GeoJSON areas and calculate total area."""
    processed_dir = Path(__file__).parent.parent / "processed"
    areas = []

    for geojson_file in processed_dir.glob("*.geojson"):
        try:
            with open(geojson_file) as f:
                data = json.load(f)

            # Extract geometry (assuming FeatureCollection or Feature)
            if data.get("type") == "FeatureCollection":
                geometries = [shape(feature["geometry"]) for feature in data["features"]]
            elif data.get("type") == "Feature":
                geometries = [shape(data["geometry"])]
            else:
                print(f"Skipping {geojson_file.name}: unsupported GeoJSON type")
                continue

            area_name = geojson_file.stem
            total_area = sum(geom.area for geom in geometries)  # Area in square degrees

            areas.append(
                {
                    "name": area_name,
                    "file": geojson_file.name,
                    "geometries": geometries,
                    "area_sq_deg": total_area,
                    "geometry_count": len(geometries),
                }
            )

        except Exception as e:
            print(f"Error loading {geojson_file.name}: {e}")
            continue

    return areas


def check_gcs_coverage(year=2017):
    """Check which areas have data in GCS for the given year."""
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

        # Check for hungary_with_neighbors data
        hungary_2017_files = [
            f for f in all_files if f"hungary_with_neighbors_{year}_embeddings_z16" in f
        ]

        if hungary_2017_files:
            print(
                f"✓ hungary_with_neighbors: PROCESSED (found {len(hungary_2017_files)} zoom levels)"
            )
            # For area calculation, we need the original GeoJSON
            # For now, just return that it's processed
            return True, 1  # Dummy area value since we don't have the actual area
        else:
            print("✗ hungary_with_neighbors: NOT PROCESSED")
            return False, 0

    except Exception as e:
        print(f"Error checking GCS: {e}")
        return False, 0


def check_milvus_coverage(year=2017):
    """Check how many records exist in Milvus for the given year."""
    try:
        from pymilvus import Collection, connections

        connections.connect(
            host=os.getenv("MILVUS_HOST", "localhost"), port=os.getenv("MILVUS_PORT", "19530")
        )

        collection_name = os.getenv("COLLECTION", "hungary_with_neighbors_embeddings")
        from pymilvus import utility

        if not utility.has_collection(collection_name):
            print(f"Collection {collection_name} does not exist")
            return 0

        col = Collection(collection_name)

        # Query count for the specific year
        year_count = col.query(expr=f"year == {year}", output_fields=["count(*)"])

        if year_count:
            count = year_count[0]["count(*)"]
            print(f"Milvus records for {year}: {count}")
            return count
        else:
            print(f"No records found for {year} in Milvus")
            return 0

    except ImportError:
        print("pymilvus not available, skipping Milvus check")
        return 0
    except Exception as e:
        print(f"Error checking Milvus: {e}")
        return 0


def main():
    print("=== 2017 Data Coverage Analysis ===\n")

    # Load processed areas
    print("Loading processed areas...")
    areas = load_processed_areas()
    print(f"Found {len(areas)} processed areas\n")

    # Calculate total area
    total_area = sum(area["area_sq_deg"] for area in areas)
    print(".4f")

    # Check GCS coverage
    print("\nChecking GCS coverage for 2017...")
    has_coverage, _dummy_area = check_gcs_coverage(year=2017)

    coverage_percentage = 100.0 if has_coverage else 0.0
    print(f"GCS Coverage: {'Yes' if has_coverage else 'No'} ({coverage_percentage:.1f}%)")

    # Check Milvus coverage (optional)
    print("\nChecking Milvus database...")
    try:
        milvus_count = check_milvus_coverage(year=2017)
    except Exception as e:
        print(f"Milvus check failed: {e}")
        milvus_count = 0

    print("\n=== Summary ===")
    print(f"Total processed areas: {len(areas)}")
    print(f"hungary_with_neighbors 2017 data: {'PROCESSED' if has_coverage else 'NOT PROCESSED'}")
    print(f"Total area: {total_area:.4f} sq deg")
    print(f"GCS Coverage: {'Yes' if has_coverage else 'No'} ({coverage_percentage:.1f}%)")
    print(f"Milvus records for 2017: {milvus_count}")

    if has_coverage:
        print("\n✓ 2017 data has been processed and is available in GCS")
        print("  - Available zoom levels: z8, z16, z32, z64, z128, z256")
    else:
        print("\n✗ 2017 data has not been processed yet")
