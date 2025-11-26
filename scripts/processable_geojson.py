import json
from pathlib import Path

# Define path relative to this script (scripts/processable_geojson.py -> root/small_areas/...)
file_path = Path(__file__).parent.parent / "small_areas" / "missing_areas_2017_v2.geojson"

with open(file_path) as f:
    data = json.load(f)

# Extract the geometry from the first feature
geometry = data["features"][0]["geometry"]

# Create simple GeoJSON with just the geometry
simple_geojson = {"type": "Feature", "geometry": geometry}

with open(file_path, "w") as f:
    json.dump(simple_geojson, f, indent=2)

print("Converted to simple GeoJSON format")
