#!/usr/bin/env python3
"""
Analyze coverage and create GeoJSON for missing areas in 2017 processing.
"""

import json
import math
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import unary_union

# Tile size mappings (z -> meters per side)
TILE_SIZES_METERS = {
    8: 80,
    16: 160,
    32: 320,
    64: 640,
    128: 1280,
    256: 2560,
}


def load_geojson(filepath):
    """Load a GeoJSON file and return its geometry."""
    with open(filepath) as f:
        data = json.load(f)

    if data.get("type") == "FeatureCollection":
        geometries = [shape(feature["geometry"]) for feature in data["features"]]
        return unary_union(geometries)
    elif data.get("type") == "Feature":
        return shape(data["geometry"])
    else:
        raise ValueError(f"Unsupported GeoJSON type: {data.get('type')}")


def meters_to_degrees(meters, latitude):
    """Convert meters to degrees at a given latitude."""
    # Earth radius in meters
    earth_radius = 6371000

    # Convert meters to degrees latitude (approximately constant)
    lat_degrees = meters / (earth_radius * math.pi / 180)

    # Convert meters to degrees longitude (varies with latitude)
    lon_degrees = meters / (earth_radius * math.pi / 180 * math.cos(math.radians(latitude)))

    return lat_degrees, lon_degrees


def create_tile_polygon(lon, lat, z):
    """Create a polygon representing a tile centered at lon/lat with given z level."""
    if z not in TILE_SIZES_METERS:
        print(f"Warning: Unknown z level {z}, using z16 as default")
        z = 16

    tile_size_m = TILE_SIZES_METERS[z]
    half_size_m = tile_size_m / 2

    # Convert to degrees
    lat_offset, lon_offset = meters_to_degrees(half_size_m, lat)

    # Create polygon
    return Polygon(
        [
            (lon - lon_offset, lat - lat_offset),
            (lon + lon_offset, lat - lat_offset),
            (lon + lon_offset, lat + lat_offset),
            (lon - lon_offset, lat + lat_offset),
        ]
    )


def load_processed_areas():
    """Load processed areas by querying Milvus for 2017 embeddings."""
    try:
        from pymilvus import Collection, connections

        print("Connecting to Milvus...")
        connections.connect(host="localhost", port="19530")
        col = Collection("hungary_with_neighbors_embeddings")
        col.load()

        print("Querying 2017 embeddings from Milvus...")

        # Milvus has a limit of 16384 for query results, so we need to paginate
        all_results = []
        batch_size = 5000  # Keep offset + limit <= 16384
        offset = 0

        while True:
            # Check if we would exceed the limit
            if offset + batch_size > 16384:
                print(f"Reached Milvus query limit at offset {offset}, stopping pagination")
                break

            results = col.query(
                expr="year == 2017",
                output_fields=["lat", "lon", "z"],
                limit=batch_size,
                offset=offset,
            )

            if not results:
                break

            all_results.extend(results)
            offset += batch_size

            print(f"Retrieved {len(all_results)} embeddings so far...")

            # Safety check to avoid infinite loops
            if offset > 20000:  # Should be more than enough
                print("Warning: Reached maximum offset, stopping query")
                break

        print(f"Total retrieved: {len(all_results)} embeddings")

        processed_geometries = []
        for i, result in enumerate(all_results):
            lat, lon, z = result["lat"], result["lon"], result["z"]

            # Create tile polygon for this embedding
            tile_geom = create_tile_polygon(lon, lat, z)
            processed_geometries.append(tile_geom)

            if (i + 1) % 1000 == 0:
                print(f"Processed {i + 1} tiles...")

        print(f"Created {len(processed_geometries)} tile polygons")

        if processed_geometries:
            # Union all tile polygons to get processed area
            processed_area = unary_union(processed_geometries)
            print(f"Union created: {processed_area.area:.4f} sq deg")
            return processed_area
        else:
            return Polygon()  # Empty polygon

    except Exception as e:
        print(f"Error querying Milvus: {e}")
        return Polygon()  # Empty polygon


def create_missing_areas_geojson():
    """Create a GeoJSON file with areas that haven't been processed yet."""
    print("=== Creating Missing Areas GeoJSON ===\n")

    # Load the full area to be processed
    full_area_path = Path(__file__).parent.parent / "processed" / "hungary_with_neighbors.geojson"
    try:
        full_area = load_geojson(full_area_path)
        print("✓ Loaded full area: hungary_with_neighbors.geojson")
        print(f"  Full area: {full_area.area:.4f} sq deg")
    except Exception as e:
        print(f"✗ Error loading full area: {e}")
        return

    # Load processed areas
    processed_area = load_processed_areas()
    print(f"  Processed area: {processed_area.area:.4f} sq deg")

    # Calculate missing area
    if processed_area.is_empty:
        missing_area = full_area
        print("  No processed areas found - all area is missing")
    else:
        missing_area = full_area.difference(processed_area)
        print(f"  Missing area: {missing_area.area:.4f} sq deg")

    coverage_ratio = (processed_area.area / full_area.area) * 100 if full_area.area > 0 else 0
    print(f"Coverage: {coverage_ratio:.1f}%")

    # Create GeoJSON for missing areas
    if missing_area.is_empty:
        print("\n✅ All areas have been processed! No missing areas.")
        return None
    elif missing_area.area < 0.001:  # Very small area
        print("\n⚠️  Missing area is very small, might be due to geometric precision.")
        return None
    else:
        # Convert to GeoJSON
        if isinstance(missing_area, Polygon):
            features = [missing_area]
        elif isinstance(missing_area, MultiPolygon):
            features = list(missing_area.geoms)
        else:
            features = [missing_area]

        geojson_features = []
        for i, geom in enumerate(features):
            if geom.area > 0.001:  # Only include meaningful areas
                geojson_features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "id": f"missing_area_{i + 1}",
                            "area_sq_deg": round(geom.area, 6),
                        },
                        "geometry": geom.__geo_interface__,
                    }
                )

        if not geojson_features:
            print("\n⚠️  No significant missing areas found.")
            return None

        geojson_data = {"type": "FeatureCollection", "features": geojson_features}

        # Save to file
        output_path = Path(__file__).parent.parent / "missing_areas_2017.geojson"
        with open(output_path, "w") as f:
            json.dump(geojson_data, f, indent=2)

        print(f"\n💾 Created missing areas GeoJSON: {output_path}")
        print(f"   Contains {len(geojson_features)} missing area polygons")

        # Print summary of missing areas
        print("\n📊 Missing Areas Summary:")
        for i, feature in enumerate(geojson_features):
            area_sq_deg = feature["properties"]["area_sq_deg"]
            print(f"  Area {i + 1}: {area_sq_deg:.4f} sq deg")

        return output_path


def main():
    missing_geojson = create_missing_areas_geojson()

    if missing_geojson:
        print("\n🚀 Next Steps:")
        print(f"1. Review the missing areas in: {missing_geojson}")
        print("2. Run processing with this command:")
        print("   uv run python pipeline/0_extract.py \\")
        print(f"     --input_geojson {missing_geojson} \\")
        print("     --raw_archive gs://earth-embeddings-hungary-output/missing_areas_2017 \\")
        print("     --start_date 2017-01-01 \\")
        print("     --end_date 2017-12-31 \\")
        print("     --utm_zone EPSG:32633 \\")
        print("     --scale 10 \\")
        print("     --ee_max_num_workers 10 \\")
        print("     --runner DirectRunner \\")
        print("     --project YOUR_PROJECT_ID \\")
        print("     --service_account_email YOUR_SERVICE_ACCOUNT")
    else:
        print("\n✅ No missing areas to process!")


if __name__ == "__main__":
    main()
