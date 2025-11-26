"""
Automated Local Pipeline
1. Download data from Google Drive
2. Merge tiles
3. Convert to Zarr
"""

import subprocess
import sys

from download_from_drive import download_data


def run_command(command):
    """Run a shell command and check for errors."""
    print(f"\nRunning: {' '.join(command)}")
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        print(f"Error running command: {command}")
        sys.exit(result.returncode)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run the local pipeline.")
    parser.add_argument(
        "--pattern", type=str, default="budapest_2024", help="File pattern to match in Drive."
    )
    parser.add_argument(
        "--folder", type=str, default="earth_engine_exports", help="Drive folder name."
    )
    parser.add_argument(
        "--interactive", action="store_true", help="Force interactive OAuth authentication."
    )
    args = parser.parse_args()

    print("=== Starting Automated Local Pipeline ===")

    # Step 1: Download
    print("\n--- Step 1: Download from Drive ---")
    try:
        files = download_data(
            folder_name=args.folder, pattern=args.pattern, force_interactive=args.interactive
        )
        if not files:
            print("No files downloaded or found. Exiting.")
            sys.exit(1)
    except Exception as e:
        print(f"Download failed: {e}")
        sys.exit(1)

    # Step 2: Merge
    print("\n--- Step 2: Merge Tiles ---")
    # Derive output filename from pattern (e.g. "budapest_2024" -> "data/budapest_2024.tif")
    # If pattern contains wildcards or is complex, we might need a better heuristic.
    # For now, assume pattern is a prefix like "budapest_2024" or "hun_2024_tile"
    base_name = args.pattern.replace("*", "").strip("-_")
    merged_tif = f"data/{base_name}.tif"

    run_command(
        [
            "uv",
            "run",
            "python",
            "local_pipeline/0_merge_tiles.py",
            "--input_pattern",
            f"data/{args.pattern}-*.tif",
            "--output",
            merged_tif,
        ]
    )

    # Step 3: Convert to Zarr
    print("\n--- Step 3: Convert to Zarr ---")
    raw_zarr = f"{base_name}_raw.zarr"
    run_command(
        [
            "uv",
            "run",
            "python",
            "local_pipeline/1_convert_to_zarr.py",
            "--input",
            merged_tif,
            "--output",
            raw_zarr,
        ]
    )

    print("\n=== Pipeline Completed Successfully! ===")


if __name__ == "__main__":
    main()
