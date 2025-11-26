#!/usr/bin/env python3
"""
Split Large TIF Helper
Splits a large 50km TIF into four 25km quarters to avoid Beam gRPC 2GB limit.
"""

import argparse
import os
import sys

import rioxarray as rxr


def split_tif(input_path, output_dir):
    """Split a large TIF into 4 quarters (NW, NE, SW, SE)."""
    print(f"Loading {input_path}...")
    
    # Load the TIF
    da = rxr.open_rasterio(input_path, chunks={"band": 1, "x": 1024, "y": 1024})
    
    # Get dimensions
    ny, nx = da.shape[1], da.shape[2]
    half_y = ny // 2
    half_x = nx // 2
    
    print(f"Input dimensions: {ny} x {nx}")
    print(f"Splitting into 4 quarters of ~{half_y} x {half_x} each...")
    
    # Base name
    base_name = os.path.basename(input_path).replace(".tif", "")
    
    quarters = [
        ("NW", 0, half_y, 0, half_x),
        ("NE", 0, half_y, half_x, nx),
        ("SW", half_y, ny, 0, half_x),
        ("SE", half_y, ny, half_x, nx),
    ]
    
    output_paths = []
    
    for quadrant, y_start, y_end, x_start, x_end in quarters:
        # Slice the data (using isel for integer indexing)
        quarter = da.isel(y=slice(y_start, y_end), x=slice(x_start, x_end))
        
        # Create output path
        output_path = os.path.join(output_dir, f"{base_name}_{quadrant}.tif")
        
        print(f"  Writing {quadrant} quarter to {output_path}...")
        quarter.rio.to_raster(output_path, driver="COG", compress="LZW")
        
        output_paths.append(output_path)
    
    print(f"✓ Split {input_path} into 4 quarters")
    return output_paths


def main():
    parser = argparse.ArgumentParser(description="Split large 50km TIF into 25km quarters")
    parser.add_argument("--input", required=True, help="Path to large TIF file")
    parser.add_argument("--output-dir", default="data_hun", help="Output directory for quarters")
    args = parser.parse_args()
    
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Split
    output_paths = split_tif(args.input, args.output_dir)
    
    print(f"\n✓ Created {len(output_paths)} quarter tiles:")
    for path in output_paths:
        print(f"  - {path}")


if __name__ == "__main__":
    main()
