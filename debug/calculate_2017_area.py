#!/usr/bin/env python3
"""
Calculate the area covered by 2017 embeddings in Milvus database.
"""

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union


def get_2017_coordinates():
    """Get all lat/lon coordinates for 2017 data from Milvus."""
    try:
        from pymilvus import Collection, connections

        connections.connect(host="localhost", port="19530")
        col = Collection("hungary_with_neighbors_embeddings")
        col.load()

        print("Querying 2017 coordinates from Milvus...")

        # Get all 2017 records with lat/lon
        results = col.query(
            expr="year == 2017",
            output_fields=["lat", "lon"],
            limit=10000,  # Should be enough for 7086 records
        )

        coordinates = []
        for result in results:
            lat, lon = result["lat"], result["lon"]
            coordinates.append((lon, lat))  # Note: shapely uses (x, y) = (lon, lat)

        print(f"Retrieved {len(coordinates)} coordinate pairs")
        return coordinates

    except Exception as e:
        print(f"Error querying Milvus: {e}")
        return []


def calculate_covered_area(coordinates):
    """Calculate the area covered by the coordinates."""
    if not coordinates:
        return 0, None, None

    # Create Point objects
    points = [Point(lon, lat) for lon, lat in coordinates]

    # Calculate convex hull
    if len(points) >= 3:
        hull = unary_union(points).convex_hull
    else:
        # For few points, just use the union
        hull = unary_union(points)

    # Calculate bounding box
    lons = [coord[0] for coord in coordinates]
    lats = [coord[1] for coord in coordinates]

    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)

    # Create bounding box polygon
    bbox = Polygon([(min_lon, min_lat), (min_lon, max_lat), (max_lon, max_lat), (max_lon, min_lat)])

    # Calculate areas (in square degrees)
    # Note: This is approximate since we're using geographic coordinates
    # For more accurate area calculation, we'd need to project to a suitable CRS

    hull_area_sq_deg = hull.area if hasattr(hull, "area") else 0
    bbox_area_sq_deg = bbox.area

    # Rough conversion to square kilometers (very approximate!)
    # 1 degree latitude ≈ 111 km
    # 1 degree longitude ≈ 111 km * cos(latitude)
    # For Hungary (around 47°N), cos(47°) ≈ 0.68
    avg_lat = (min_lat + max_lat) / 2
    km_per_deg_lat = 111.0
    km_per_deg_lon = 111.0 * abs(np.cos(np.radians(avg_lat)))

    hull_area_sq_km = hull_area_sq_deg * km_per_deg_lat * km_per_deg_lon
    bbox_area_sq_km = bbox_area_sq_deg * km_per_deg_lat * km_per_deg_lon

    return {
        "point_count": len(coordinates),
        "hull_area_sq_deg": hull_area_sq_deg,
        "bbox_area_sq_deg": bbox_area_sq_deg,
        "hull_area_sq_km": hull_area_sq_km,
        "bbox_area_sq_km": bbox_area_sq_km,
        "bounds": {"min_lon": min_lon, "max_lon": max_lon, "min_lat": min_lat, "max_lat": max_lat},
        "center": {"lat": avg_lat, "lon": (min_lon + max_lon) / 2},
    }


def main():
    print("=== 2017 Data Coverage Area Analysis ===\n")

    # Get coordinates from Milvus
    coordinates = get_2017_coordinates()

    if not coordinates:
        print("No coordinates retrieved. Cannot calculate area.")
        return

    # Calculate area
    area_stats = calculate_covered_area(coordinates)

    print("📊 Coverage Statistics:")
    print(f"   Points: {area_stats['point_count']}")
    print(f"   Convex Hull Area: {area_stats['hull_area_sq_deg']:.4f} sq deg")
    print(f"   Bounding Box Area: {area_stats['bbox_area_sq_deg']:.4f} sq deg")
    print(f"   Convex Hull Area: {area_stats['hull_area_sq_km']:.0f} km²")
    print(f"   Bounding Box Area: {area_stats['bbox_area_sq_km']:.0f} km²")

    print("\n📍 Geographic Bounds:")
    bounds = area_stats["bounds"]
    print(f"   Longitude: {bounds['min_lon']:.4f} to {bounds['max_lon']:.4f}")
    print(f"   Latitude: {bounds['min_lat']:.4f} to {bounds['max_lat']:.4f}")

    print("\n📍 Center Point:")
    center = area_stats["center"]
    print(f"   {center['lat']:.4f}, {center['lon']:.4f}")

    # Compare with expected Hungary area
    # Hungary is approximately 93,030 km²
    hungary_area_sq_km = 93030
    coverage_ratio = area_stats["hull_area_sq_km"] / hungary_area_sq_km * 100

    print("\n🇭🇺 Comparison with Hungary:")
    print(f"   Hungary area: {hungary_area_sq_km:,} km²")
    print(f"   Coverage ratio: {coverage_ratio:.1f}%")

    if coverage_ratio > 100:
        print("   → Coverage extends beyond Hungary (includes neighbors)")
    elif coverage_ratio > 80:
        print("   → Near-complete coverage of Hungary")
    elif coverage_ratio > 50:
        print("   → Partial coverage of Hungary")
    else:
        print("   → Limited coverage of Hungary")


if __name__ == "__main__":
    main()
