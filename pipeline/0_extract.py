# ~~~~~~~~ EXTRACT PIPELINE ~~~~~~~~
# This script extracts Earth Engine data and saves it to a Zarr archive.
# It loads an xarray dataset backed by Earth Engine, and extracts the pixel values using the ee backend
# the chunks are then saved to a zarr archive.
#
# Notes on tuning:
# - Apache beam seems to have quite a high cpu overhead, but the ee concurrency can't exceed 20 concurrent
#   requests (latency seems to be abour 1s per request)
# - So we have a problem where we need more cpu, but the pipeline will greedily make more calls to ee.
# - A good solution seemed to be to use a single thread per worker, and then tune the number of workers.
# - Beam will also push for larger batches, which causes cpu clogging (and can crash your job)
# - so a better solution seems to be limiting the total job size, so batches are smaller, and flow properly through the pipeline


import logging
import os
import time
from collections.abc import Iterator, Mapping, Sequence
from typing import AbstractSet

# from absl import flags
import apache_beam as beam
import ee
import pyproj
import xarray as xr
import xarray_beam as xbeam
from apache_beam.options.pipeline_options import PipelineOptions
from shapely import geometry
from shapely.geometry import shape
from shapely.ops import transform
from xarray_beam._src import core as xbeam_core
from xarray_beam._src import threadmap
from xarray_beam._src.core import Key
from xee import EarthEngineBackendEntrypoint

import itertools
import json
import logging
import os
import time
from collections.abc import Iterator, Mapping, Sequence
from typing import AbstractSet

# from absl import flags
import apache_beam as beam
import ee
import pyproj
import xarray as xr
import xarray_beam as xbeam
from apache_beam.options.pipeline_options import PipelineOptions
from shapely import geometry
from shapely.geometry import shape
from shapely.ops import transform
from xarray_beam._src import core as xbeam_core
from xarray_beam._src import threadmap
from xarray_beam._src.core import Key
from xee import EarthEngineBackendEntrypoint

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

ALL_BANDS = [f"A{i:02d}" for i in range(64)]
RAW_CHUNKS = {"time": 1, "X": 2048, "Y": 2048}  # this makes nice 8km/8mb chunks


class CustomOptions(PipelineOptions):
    @classmethod
    def _add_argparse_args(cls, parser):
        parser.add_argument(
            "--input_geojson",
            type=str,
            required=True,
            help="Path to the input GeoJSON file containing the area of interest.",
        )
        parser.add_argument(
            "--bands",
            type=str,
            help="List of bands to select. If not specified, all bands will be used.",
        )
        parser.add_argument(
            "--scale", type=float, default=100, help="Scale factor for output Zarr."
        )
        parser.add_argument(
            "--ee_max_num_workers",
            type=int,
            default=2,
            help="Maximum number of workers for Earth Engine operations.",
        )
        parser.add_argument(
            "--utm_zone",
            type=str,
            required=True,
        )
        parser.add_argument(
            "--start_date",
            type=str,
            required=True,
            help="Start date for filtering (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--end_date",
            type=str,
            required=True,
            help="End date for filtering (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--raw_archive", type=str, required=True, help="The output zarr path."
        )


def clean_and_parse_utm_zone(espg_str: str) -> str:
    """Clean and parse the EPSG string for UTM zone."""
    if not espg_str.startswith("EPSG:"):
        raise ValueError("UTM zone must start with 'EPSG:'")
    parts = espg_str.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        raise ValueError("UTM zone must be in the format 'EPSG:32XYY'")
    assert len(parts[1]) == 5, "UTM zone must be 5 characters long"
    hemisphere = parts[1][2]
    if hemisphere not in ["6", "7"]:
        raise ValueError(
            f"UTM zone hemisphere ({hemisphere}) must be '6' for North or '7' for South"
        )
    zone = parts[1][-2:]
    if int(zone) < 1 or int(zone) > 60:
        raise ValueError("UTM zone number must be between 01 and 60")
    return zone + {"6": "N", "7": "S"}[hemisphere]  # e.g. "30N" or "30S"


def main(argv: list[str]) -> None:
    # ### Apache Beam Pipeline Setup ###
    options = PipelineOptions()
    main_options = options.get_all_options()
    custom_options = options.view_as(CustomOptions)

    if not custom_options.bands:
        bands = ALL_BANDS
    else:
        bands = [b.strip() for b in custom_options.bands.split(",")]
    print("bands:", bands)

    # parse the utm zone
    assert custom_options.utm_zone.startswith("EPSG"), (
        "specify your utm zone as EPSG:32XYY, where X is 6 for N, 7 for S, and YY is the zone number."
    )

    # ### Earth Engine Initialization ###
    credentials = ee.ServiceAccountCredentials(
        main_options["service_account_email"], os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    )

    ee.Initialize(
        credentials=credentials,
        project=main_options["project"],
        url=os.environ.get("HV_URL", "https://earthengine-highvolume.googleapis.com"),
    )

    # Load the area of interest from a geojson file stored in GCS
    with open(custom_options.input_geojson) as f:
        geojson = json.loads(f.read())
        aoi = shape(geojson["geometry"])

    utm_abbrev = clean_and_parse_utm_zone(custom_options.utm_zone)

    # reproject the AOI to UTM
    reproject = pyproj.Transformer.from_crs(
        "EPSG:4326", custom_options.utm_zone, always_xy=True
    ).transform
    aoi_utm = transform(reproject, aoi)
    minx, _miny, _maxx, maxy = aoi_utm.bounds

    aoi_ee = ee.Geometry(aoi.__geo_interface__)

    # Set desired pixel size
    scale = int(custom_options.scale)

    affine = [
        scale,
        0,
        minx,
        0,
        -scale,
        maxy,
    ]

    im_float = (
        ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
        .filterDate(custom_options.start_date, custom_options.end_date)
        .filterBounds(aoi_ee)
        .select(bands)
        .mosaic()
        .clip(aoi_ee)
    )

    # Optionally quantize according to the original paper https://arxiv.org/pdf/2507.22291
    # im_quantized = (
    #     im_float
    #     .abs()
    #     .pow(ee.Image.constant(0.5))
    #     .multiply(im_float.signum())
    #     .multiply(ee.Image.constant(127)) # [-128, 127]
    #     .clamp(-127, 127)
    #     .int8()  # Convert to int8
    # )

    # im_float = im_quantized

    ds = xr.open_dataset(
        im_float,
        engine=EarthEngineBackendEntrypoint,
        projection=ee.Projection(custom_options.utm_zone, transform=affine),
        geometry=list(aoi.bounds),
        scale=scale,
        chunks=RAW_CHUNKS,
        ee_init_if_necessary=True,
        ee_init_kwargs={
            "project": main_options["project"],
            "url": os.environ.get("HV_URL", "https://earthengine-highvolume.googleapis.com"),
        },
        executor_kwargs={
            "max_workers": main_options["ee_max_num_workers"],
        },
        getitem_kwargs={
            "max_retries": 10,
            "initial_delay": 60000,  # increase the delay before retrying to respect pool size
        },
    )

    def iter_chunk_keys(
        offsets: Mapping[str, Sequence[int]],
        vars: AbstractSet[str] | None = None,  # pylint: disable=redefined-builtin
        chunks=RAW_CHUNKS,
    ) -> Iterator[Key]:
        """A shim overwrite to obtain chunks that intersect with the area of interest."""
        chunk_indices = [range(len(sizes)) for sizes in offsets.values()]
        count_skipped_aois = 0
        processed_chunks = 0
        for indices in itertools.product(*chunk_indices):
            key_offsets = {
                dim: offsets[dim][index] for dim, index in zip(offsets, indices, strict=False)
            }
            # check intersection of bbox with the area of interest
            query_shp = geometry.box(
                ds.X[key_offsets["X"]],
                ds.Y[key_offsets["Y"]],
                ds.X[min(key_offsets["X"] + chunks["X"] - 1, ds.sizes["X"] - 1)],
                ds.Y[min(key_offsets["Y"] + chunks["Y"] - 1, ds.sizes["Y"] - 1)],
            )

            if aoi_utm.intersects(query_shp):
                if vars is None:
                    yield Key(key_offsets)
                else:
                    yield Key(key_offsets, vars)
                processed_chunks += 1
                if processed_chunks % 10 == 0:
                    print(f"Queued {processed_chunks} chunks for processing")
                time.sleep(10)  # delay between chunks to respect rate limits
            else:
                count_skipped_aois += 1
                if count_skipped_aois % 100 == 0:
                    print(
                        f"Skipped {count_skipped_aois} AOIs that do not intersect with the area of interest."
                    )
                continue

    class ShimDatasetToChunks(xbeam_core.DatasetToChunks):
        """This shim around DatasetToChunks:
        - we force all keys to be iterated locally, rather than in a beam worker.
        - this allows us to check the intersection of our key bounds with the area of interest,
         so we can skip keys that do not intersect.
        """

        def _iter_all_keys(self) -> Iterator[Key]:
            """Iterate over all Key objects."""
            if not self.split_vars:
                yield from iter_chunk_keys(self.offsets)
            else:
                for name, variable in self._first.items():
                    relevant_offsets = {k: v for k, v in self.offsets.items() if k in variable.dims}
                    yield from iter_chunk_keys(
                        relevant_offsets, vars={name}
                    )  # pytype: disable=wrong-arg-types  # always-use-property-annotation

        def expand(self, pcoll):
            key_pcoll = pcoll | beam.Create(self._iter_all_keys())

            return key_pcoll | "KeyToChunks" >> threadmap.FlatThreadMap(
                self._key_to_chunks, num_threads=self.num_threads
            )

    template = xbeam.make_template(ds)

    print("~~~~ ds ~~~~")
    print(ds)
    print("~~~~ template ~~~~")
    print(template)

    # Make zarr_chunks adaptive to data size
    adaptive_zarr_chunks = {}
    for dim in ds.sizes:
        adaptive_zarr_chunks[dim] = ds.sizes[dim]

    try:
        with beam.Pipeline(options=options) as root:
            _ = (
                root
                # First pull in the dataset and write it to chunks
                | ShimDatasetToChunks(
                    ds,
                    chunks=RAW_CHUNKS,
                    num_threads=main_options["ee_max_num_workers"],
                )
                | xbeam.ChunksToZarr(
                    custom_options.raw_archive,
                    template=template,
                    zarr_chunks=adaptive_zarr_chunks,
                )
            )
    except Exception as e:
        print(f"Pipeline failed with error: {e}")
        import traceback

        traceback.print_exc()
        raise


if __name__ == "__main__":
    import sys

    main(sys.argv)
