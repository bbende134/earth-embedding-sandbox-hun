#!/usr/bin/env python3
"""
Iteratively query ALL Milvus data and calculate actual covered areas.
Discovers the true boundary of available data.
"""

import json
import math
from pathlib import Path

from pymilvus import Collection, connections
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

TILE_SIZES_METERS = {
    8: 80,
    16: 160,
    32: 320,
    64: 640,
    128: 1280,
    256: 2560,
}


def meters_to_degrees(meters, latitude):
    """Convert meters to degrees at a given latitude."""
    earth_radius = 6371000
    lat_degrees = meters / (earth_radius * math.pi / 180)
    lon_degrees = meters / (earth_radius * math.pi / 180 * math.cos(math.radians(latitude)))
    return lat_degrees, lon_degrees


def create_tile_polygon(lon, lat, z):
    """Create a polygon representing a tile centered at lon/lat with given z level."""
    if z not in TILE_SIZES_METERS:
        z = 16

    tile_size_m = TILE_SIZES_METERS[z]
    half_size_m = tile_size_m / 2

    lat_offset, lon_offset = meters_to_degrees(half_size_m, lat)

    return Polygon(
        [
            (lon - lon_offset, lat - lat_offset),
            (lon + lon_offset, lat - lat_offset),
            (lon + lon_offset, lat + lat_offset),
            (lon - lon_offset, lat + lat_offset),
        ]
    )


def query_all_data_iteratively(year=2017):
    """Query all data iteratively, discovering the true data boundary."""
    print("🔗 Connecting to Milvus...\n")
    connections.connect(host="localhost", port="19530")
    col = Collection("hungary_with_neighbors_embeddings")
    col.load()

    print(f"📊 Querying ALL {year} data iteratively...\n")

    all_data = []
    batch_size = 1000
    offset = 0
    stopped = False

    while not stopped:
        # Calculate remaining budget before hitting limit
        if offset + batch_size > 16384:
            remaining = 16384 - offset
            if remaining <= 0:
                print(f"\n⏹️  Reached absolute Milvus limit at offset {offset}")
                break
            batch_size = remaining
            print(f"  Adjusting final batch to {batch_size} (staying within limit)...")

        try:
            print(f"  Querying offset {offset:5d}, limit {batch_size:4d}...", end=" ", flush=True)

            results = col.query(
                expr=f"year == {year}",
                output_fields=["lat", "lon", "z", "year"],
                limit=batch_size,
                offset=offset,
            )

            if not results:
                print("✓ (0 records - END OF DATA)")
                stopped = True
                break

            all_data.extend(results)
            print(f"✓ ({len(results)} records, total: {len(all_data):,})")

            # If we got fewer results than requested, we've reached the end
            if len(results) < batch_size:
                print(f"\n✅ Reached end of data: got {len(results)} < {batch_size}")
                stopped = True
                break

            offset += batch_size

        except Exception as e:
            print(f"✗ ERROR: {e}")
            break

    print(f"\n✅ Total records retrieved: {len(all_data):,}\n")
    return all_data


def analyze_retrieved_data(data):
    """Analyze the retrieved data."""
    if not data:
        print("❌ No data to analyze!")
        return

    print("=" * 70)
    print("  DATA ANALYSIS")
    print("=" * 70 + "\n")

    # Unique locations
    unique_locs = set()
    for d in data:
        key = (round(d["lat"], 6), round(d["lon"], 6), d["z"])
        unique_locs.add(key)

    duplicates = len(data) - len(unique_locs)
    print(f"Total records:       {len(data):,}")
    print(f"Unique locations:    {len(unique_locs):,}")
    print(f"Duplicate records:   {duplicates:,}")
    if len(data) > 0:
        print(f"Duplication rate:    {(duplicates / len(data)) * 100:.2f}%\n")

    # Geographic bounds
    lats = [d["lat"] for d in data]
    lons = [d["lon"] for d in data]

    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    print("Geographic bounds:")
    print(f"  Latitude:  {min_lat:.4f} to {max_lat:.4f} (span: {max_lat - min_lat:.4f}°)")
    print(f"  Longitude: {min_lon:.4f} to {max_lon:.4f} (span: {max_lon - min_lon:.4f}°)\n")

    # Zoom distribution
    z_dist = {}
    for d in data:
        z = d["z"]
        z_dist[z] = z_dist.get(z, 0) + 1

    print("Zoom level distribution:")
    for z in sorted(z_dist.keys()):
        count = z_dist[z]
        pct = (count / len(data)) * 100
        print(f"  z{z:3d}: {count:7,d} records ({pct:5.1f}%)")

    print()
    return unique_locs


def calculate_coverage_area(data):
    """Calculate the actual covered area from embeddings."""
    if not data:
        return None, 0

    print("=" * 70)
    print("  CALCULATING COVERAGE AREA")
    print("=" * 70 + "\n")

    print("🔄 Creating tile geometries (this may take a moment)...\n")

    # Create geometries from UNIQUE locations only (to avoid inflating area)
    unique_locs = {}
    for d in data:
        key = (round(d["lat"], 6), round(d["lon"], 6), d["z"])
        if key not in unique_locs:
            unique_locs[key] = d

    print(f"  Processing {len(unique_locs):,} unique tile locations...")

    geometries = []
    for i, (key, d) in enumerate(unique_locs.items()):
        lat, lon, z = d["lat"], d["lon"], d["z"]
        tile_geom = create_tile_polygon(lon, lat, z)
        geometries.append(tile_geom)

        if (i + 1) % 2000 == 0:
            print(f"    {i + 1:,} tiles processed...")

    print(f"\n  Creating union of {len(geometries):,} unique tiles...")
    covered_geom = unary_union(geometries)
    covered_area = covered_geom.area

    print(f"\n✅ Covered area: {covered_area:.4f} sq deg\n")

    return covered_geom, covered_area


def calculate_missing_areas(covered_geom, covered_area):
    """Calculate missing areas."""
    print("=" * 70)
    print("  COMPARING WITH TARGET AREA")
    print("=" * 70 + "\n")

    # Load or create target area
    full_area_path = Path(__file__).parent.parent / "processed" / "hungary_with_neighbors.geojson"

    try:
        with open(full_area_path) as f:
            data = json.load(f)
        from shapely.geometry import shape

        target_geom = shape(data["geometry"])
        target_area = target_geom.area
        print(f"✓ Loaded target area from {full_area_path.name}")
    except Exception as e:
        print(f"⚠️  Using default bounding box (could not load: {e})")
        target_geom = box(15.0, 45.0, 25.0, 49.5)
        target_area = 45.0

    print(f"  Target area: {target_area:.4f} sq deg (Hungary + neighbors)\n")

    coverage_pct = (covered_area / target_area) * 100 if target_area > 0 else 0
    missing_area = target_area - covered_area
    missing_pct = 100 - coverage_pct

    print("=" * 70)
    print("  COVERAGE SUMMARY")
    print("=" * 70 + "\n")

    print(f"Target area:         {target_area:.4f} sq deg")
    print(f"Covered area:        {covered_area:.4f} sq deg")
    print(f"Missing area:        {missing_area:.4f} sq deg")
    print(f"Coverage:            {coverage_pct:.2f}%")
    print(f"Missing percentage:  {missing_pct:.2f}%\n")

    if coverage_pct >= 100:
        print("✅ FULL COVERAGE ACHIEVED!")
    elif coverage_pct >= 90:
        print("✅ EXCELLENT coverage (>90%)")
    elif coverage_pct >= 50:
        print("⚠️  MODERATE coverage (50-90%)")
    else:
        print("❌ LOW coverage (<50%)")

    print()
    return target_geom, target_area, coverage_pct


def save_analysis(data, covered_area, coverage_pct):
    """Save analysis results to file."""
    analysis = {
        "timestamp": str(Path(__file__).stat().st_mtime),
        "total_records": len(data),
        "covered_area_sq_deg": covered_area,
        "coverage_percentage": coverage_pct,
        "retrieval_method": "iterative_pagination",
        "milvus_limit": 16384,
    }

    output_file = "coverage_analysis_2017.json"
    with open(output_file, "w") as f:
        json.dump(analysis, f, indent=2)

    print(f"💾 Saved analysis to {output_file}\n")


def main():
    print("=" * 70)
    print("  ITERATIVE MILVUS DATA RETRIEVAL & COVERAGE ANALYSIS")
    print("=" * 70)
    print()

    # Query all data
    all_data = query_all_data_iteratively(year=2017)

    if not all_data:
        print("❌ No data retrieved!")
        return

    # Analyze data
    analyze_retrieved_data(all_data)

    # Calculate coverage
    covered_geom, covered_area = calculate_coverage_area(all_data)

    if covered_geom is None:
        return

    # Compare with target
    _target_geom, _target_area, coverage_pct = calculate_missing_areas(covered_geom, covered_area)

    # Save analysis
    save_analysis(all_data, covered_area, coverage_pct)

    print("=" * 70)


if __name__ == "__main__":
    main()
