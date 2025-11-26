import os
import sys

# Add repo root to path
sys.path.append(os.getcwd())

import importlib.util


def import_module_from_path(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


process_loop = import_module_from_path("process_loop", "hungary_full_pipeline/1_process_loop.py")
process_single_tile = process_loop.process_single_tile


def main():
    tile_path = "data_hun/hun_2024_tile_000.tif"
    if not os.path.exists(tile_path):
        print(f"File {tile_path} not found!")
        # Try to find any tif in data_hun
        import glob

        tifs = glob.glob("data_hun/*.tif")
        if tifs:
            tile_path = tifs[0]
            print(f"Using {tile_path} instead.")
        else:
            return

    print(f"Testing processing of {tile_path}...")
    try:
        process_single_tile(tile_path)
        print("Processing successful!")
    except Exception as e:
        print(f"Processing failed: {e}")


if __name__ == "__main__":
    main()
