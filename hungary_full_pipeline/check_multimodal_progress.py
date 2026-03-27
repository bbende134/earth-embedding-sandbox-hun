"""
Check multimodal processing progress from Milvus.
Shows patch counts per modality broken down by time period and tile.
"""

import argparse
from collections import defaultdict

from pymilvus import Collection, connections, utility

COLLECTION = "dyn_terra"
MODALITIES = ["untok_sen2rgb@224", "untok_sen1grd@224", "untok_dem@224"]
BATCH_SIZE = 16384


def fetch_metadata(host, port, collection):
    connections.connect("default", host=host, port=port)

    if not utility.has_collection(collection):
        print(f"Collection '{collection}' does not exist.")
        return []

    coll = Collection(collection)
    coll.load()

    rows = coll.query(
        expr="id >= 0",
        output_fields=["tile_id", "modality", "date_start", "date_end", "patch_row", "patch_col"],
        limit=BATCH_SIZE,
    )

    # Paginate if needed
    if len(rows) == BATCH_SIZE:
        all_rows = list(rows)
        offset = BATCH_SIZE
        while True:
            batch = coll.query(
                expr="id >= 0",
                output_fields=[
                    "tile_id",
                    "modality",
                    "date_start",
                    "date_end",
                    "patch_row",
                    "patch_col",
                ],
                limit=BATCH_SIZE,
                offset=offset,
            )
            all_rows.extend(batch)
            if len(batch) < BATCH_SIZE:
                break
            offset += BATCH_SIZE
        return all_rows

    return list(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--milvus-host", default="localhost")
    parser.add_argument("--milvus-port", default="19530")
    args = parser.parse_args()

    print(f"Fetching from '{args.collection}'...")
    rows = fetch_metadata(args.milvus_host, args.milvus_port, args.collection)

    if not rows:
        print("No data found.")
        return

    print(f"Total rows: {len(rows)}\n")

    # --- Group by (date_start, date_end) → modality → set of (tile_id, patch_row, patch_col)
    # A unique patch is identified by (tile_id, patch_row, patch_col)
    period_mod_patches = defaultdict(lambda: defaultdict(set))
    period_tiles = defaultdict(set)

    for r in rows:
        period = (r["date_start"], r["date_end"])
        mod = r["modality"]
        patch = (r["tile_id"], r["patch_row"], r["patch_col"])
        tile = r["tile_id"]

        period_mod_patches[period][mod].add(patch)
        period_tiles[period].add(tile)

    # --- All modalities seen
    all_mods = sorted({r["modality"] for r in rows})
    mod_abbr = {m: m.replace("untok_", "").replace("@224", "") for m in all_mods}

    # --- Print timeline table
    col_w = 22
    mod_w = 14
    header = f"{'PERIOD':<{col_w}} {'TILES':>5}  " + "  ".join(
        f"{mod_abbr[m]:>{mod_w}}" for m in all_mods
    )
    print(header)
    print("-" * len(header))

    for period in sorted(period_mod_patches.keys()):
        start, end = period
        n_tiles = len(period_tiles[period])
        mod_data = period_mod_patches[period]

        # Unique patches across all modalities (a patch exists if any modality has it)
        all_patches = set().union(*mod_data.values())
        n_patches = len(all_patches)

        period_str = f"{start} -> {end}"
        row = f"{period_str:<{col_w}} {n_tiles:>5}  "

        for m in all_mods:
            count = len(mod_data.get(m, set()))
            # Flag if a modality is missing patches that others have
            complete = "✓" if count == n_patches else "!"
            row += f"  {count:>{mod_w - 2}}{complete} "

        print(row)

    print()

    # --- Per-tile breakdown
    print("Per-tile breakdown:")
    tile_period_mod = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for r in rows:
        period = (r["date_start"], r["date_end"])
        tile_period_mod[r["tile_id"]][period][r["modality"]] += 1

    tile_col = max(len(t) for t in tile_period_mod) + 2
    for tile in sorted(tile_period_mod):
        for period, mods in sorted(tile_period_mod[tile].items()):
            start, end = period
            total_unique = len(
                {
                    (r["patch_row"], r["patch_col"])
                    for r in rows
                    if r["tile_id"] == tile and (r["date_start"], r["date_end"]) == period
                }
            )
            mod_counts = "  ".join(f"{mod_abbr[m]}={mods.get(m, 0)}" for m in all_mods)
            completeness = (
                "✓" if len(mods) == len(all_mods) and len(set(mods.values())) == 1 else "!"
            )
            tile_info = f"  {tile:<{tile_col}} {start} -> {end}  patches={total_unique}"
            print(f"{tile_info} {completeness}  [{mod_counts}]")

    print()

    # --- Summary: modality completeness across all periods
    print("Modality completeness:")
    for m in all_mods:
        total = sum(len(pm[m]) for pm in period_mod_patches.values())
        print(f"  {mod_abbr[m]:<16} {total:>6} patches")


if __name__ == "__main__":
    main()
