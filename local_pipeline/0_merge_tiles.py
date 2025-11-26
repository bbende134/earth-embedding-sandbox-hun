"""
Step 0: Merge GeoTIFF tiles from Earth Engine export.
Earth Engine splits large exports into multiple tiles.
This script merges them into a single GeoTIFF.
"""

import argparse
import glob

import rasterio
from rasterio.merge import merge


def main():
    parser = argparse.ArgumentParser(description="Merge GeoTIFF tiles")
    parser.add_argument(
        "--input_pattern",
        type=str,
        default="data/budapest_2024-*.tif",
        help="Glob pattern for input tiles",
    )
    parser.add_argument(
        "--output", type=str, default="data/budapest_2024.tif", help="Output merged GeoTIFF path"
    )
    args = parser.parse_args()

    # Find all tiles
    tiles = sorted(glob.glob(args.input_pattern))

    if not tiles:
        print(f"ERROR: No tiles found matching pattern: {args.input_pattern}")
        print("Please ensure tiles are downloaded to the data/ directory")
        return 1

    print(f"Found {len(tiles)} tiles:")
    for t in tiles:
        print(f"  - {t}")

    # Open all tiles
    src_files = [rasterio.open(tile) for tile in tiles]

    # Merge into single array
    print("\nMerging tiles...")
    mosaic, out_transform = merge(src_files)

    # Get metadata from first tile
    out_meta = src_files[0].meta.copy()
    out_meta.update(
        {"height": mosaic.shape[1], "width": mosaic.shape[2], "transform": out_transform}
    )

    # Write merged file
    print(f"Writing to {args.output}...")
    with rasterio.open(args.output, "w", **out_meta) as dest:
        dest.write(mosaic)

    # Close source files
    for src in src_files:
        src.close()

    print("✓ Tiles merged successfully!")
    print(f"  Output: {args.output}")
    print(f"  Shape: {mosaic.shape} (bands × height × width)")

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
