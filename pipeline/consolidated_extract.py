# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
r"""Exports EE ImageCollections to Zarr using Xarray-Beam."""

import json
import logging
import os

# from absl import flags
import apache_beam as beam
import ee
import pyproj
import xarray as xr
import xarray_beam as xbeam
from xarray_beam._src import core as xbeam_core
from apache_beam.options.pipeline_options import PipelineOptions
from cloudpathlib import GSPath
from shapely.geometry import shape
from shapely.ops import transform
from xee import EarthEngineBackendEntrypoint

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

ALL_BANDS = [f"A{i:02d}" for i in range(64)]
RAW_CHUNKS = {"time": 1, "X": 1024, "Y": 1024}
RAW_CHUNKS_WITH_FEATURES = {"features": 1, "time": 1, "X": 1024, "Y": 1024}
STACKED_CHUNKS = {"features": -1, "time": 1, "X": 512, "Y": 512}
#INTERMEDIARY_CHUNKS = {"features": 1, "time": 1, "X": 1024, "Y": 1024}
FINAL_CHUNKS = {"features": -1, "time": 1, "X": 64, "Y": 64}
ITEMSIZE = 4



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
            "--target_chunks",
            type=str,
            default="lat=128,lon=128",
            help=(
                "Chunks on the input Zarr dataset to change on the outputs, in the "
                "form of a comma separated dimension=size pairs, e.g., "
                "--target_chunks='x=10,y=10'. Omitted dimensions are not changed and a "
                "chunksize of -1 indicates not to chunk a dimension."
            ),
        )
        parser.add_argument("--raw_archive", type=str, required=True, help="The output zarr path.")

# Borrowed from the xbeam examples:
# https://github.com/google/xarray-beam/blob/4f4fcb965a65b5d577601af311d0e0142ee38076/examples/xbeam_rechunk.py#L41
def _parse_chunks_str(chunks_str: str) -> dict[str, int]:
    chunks = {}
    parts = chunks_str.split(",")
    for part in parts:
        k, v = part.split("=")
        chunks[k] = int(v)
    return chunks


def to_array(ds: xr.Dataset) -> xr.DataArray:
    """Convert a dataset to a DataArray and squeeze it."""
    # reshape feature variables into a coordinate
    new_array = ds.to_dataarray(dim="features")

    # convert the feature label into int and sort it
    new_array = new_array.assign_coords(features=new_array.features.astype("uint8")).sortby("features")  
    return xr.Dataset({"embeddings": new_array})

def main(argv: list[str]) -> None:

    class BlockMean(beam.PTransform):
        """Calculate the mean over one or more distributed dataset dimensions."""

        def __init__(self, boundary="pad", **dim_blocks):
            super().__init__()
            self.dim_blocks = dim_blocks
            self.boundary = boundary
            assert boundary in ["pad", "exact", "trim"], \
            (
                f"Invalid boundary: {boundary}. boundary must be one of 'pad', 'exact', or 'trim':" +
                "https://docs.xarray.dev/en/stable/generated/xarray.DataArray.coarsen.html"
            )

        def _update_key(
            self, key: xbeam_core.Key, chunk: xr.Dataset
        ) -> tuple[xbeam_core.Key, xr.Dataset]:
            """set the new key offsets.
            """

            dims = self.dim_blocks.keys()
            new_offsets = {
                d: key.offsets[d] // self.dim_blocks[d]
                for d in dims if d in key.offsets
            }
            new_key = key.with_offsets(**new_offsets)
            return new_key, chunk

        def expand(self, pcoll):
            return (
                pcoll
                |   beam.MapTuple(self._update_key)
                |   beam.MapTuple(
                    lambda key, chunk: (
                        key, chunk.coarsen(**self.dim_blocks, boundary=self.boundary).mean(skipna=True)
                    )
                )
            )
        

    options = PipelineOptions()
    main_options = options.get_all_options()
    custom_options = options.view_as(CustomOptions)

    assert custom_options.input_geojson, "Must specify --input_geojson"
    assert custom_options.raw_archive, "Must specify --raw_archive"
    if not custom_options.bands:
        bands = ALL_BANDS
    else:
        bands = [b.strip() for b in custom_options.bands.split(",")]

    target_chunks = {"time": 1, "X": 1024, "Y": 1024}

    credentials = ee.ServiceAccountCredentials(
        main_options["service_account_email"], os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    )

    ee.Initialize(
        credentials=credentials,
        project=main_options['project'],
        url=os.environ.get("HV_URL", "https://earthengine-highvolume.googleapis.com"),
    )

    with open(GSPath(custom_options.input_geojson)) as f:
        geojson = json.loads(f.read())
        aoi = shape(geojson["geometry"])

    reproject = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True).transform
    aoi_27700 = transform(reproject, aoi)
    minx, miny, maxx, maxy = aoi_27700.bounds

    aoi_ee = ee.Geometry.Polygon(list(aoi.exterior.coords)[0:4])

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
        .filterDate(ee.Date("2023-12-30"), ee.Date("2024-01-02"))
        .filterBounds(aoi_ee)
        .select(bands)
        .mosaic()
        .rename([f"{ii}" for ii in range(len(bands))])
        .clip(aoi_ee)
    )

    # quantize according to the original paper https://arxiv.org/pdf/2507.22291
    # im = (
    #     im_float
    #     .abs()
    #     .pow(ee.Image.constant(0.5))
    #     .multiply(im_float.signum())
    #     .multiply(ee.Image.constant(127)) # [-128, 127] 
    #     .clamp(-127, 127)
    #     .int8()  # Convert to int8
    # )

    print (main_options)

    ds = xr.open_dataset(
        im_float,
        engine=EarthEngineBackendEntrypoint,
        projection=ee.Projection("EPSG:27700", transform=affine),
        geometry=list(aoi.bounds),
        scale=scale,
        chunks=RAW_CHUNKS,
        ee_init_if_necessary=True,
        ee_init_kwargs={
            "project": main_options['project'],
            "url": os.environ.get("HV_URL", "https://earthengine-highvolume.googleapis.com"),
        },
        executor_kwargs={
            "max_workers": main_options['max_num_workers'],
        }
    )

    template = xbeam.make_template(ds)

    # stack features to a coodinate
    inner_array = template.to_dataarray(dim="features")
    # cast them to int and sort them
    inner_array = inner_array.assign_coords(features=inner_array.features.astype("int8")).sortby("features")

    stacked_template = xr.Dataset(
        {
            "embeddings": inner_array
        }
    )

    blocked_template = stacked_template.coarsen(X=8, Y=8, boundary="pad").mean(skipna=True)

    print('~~~~ ds ~~~~')
    print(ds)
    print('~~~~ template ~~~~')
    print(template)
    print('~~~~ stacked_template ~~~~')
    print(stacked_template)
    print('~~~~ blocked_template ~~~~')
    print(blocked_template)
    breakpoint()

    with beam.Pipeline(options=options) as root:
        _ = (
            root 
            # First pull in the dataset and write it to chunks
            | xbeam.DatasetToChunks(ds, chunks={"time": 1, "X": 1024, "Y": 1024})
            # re-org to array and assign the new coordinates
            | beam.MapTuple(lambda k, ds: (k, to_array(ds)))
            # rechunk making the chunks contiguous on the features dimension
            | xbeam.Rechunk( 
                dim_sizes=stacked_template.sizes,
                itemsize=ITEMSIZE,
                source_chunks=RAW_CHUNKS_WITH_FEATURES, # 
                target_chunks=STACKED_CHUNKS,
            )
            # block-reduce the dataset
            | BlockMean(X=8,Y=8,boundary="pad")
            # consolidate the chunks back tog
            # | xbeam.ConsolidateChunks(target_chunks=FINAL_CHUNKS)
            | xbeam.ChunksToZarr(custom_options.raw_archive+"_z8b", template=blocked_template, zarr_chunks=FINAL_CHUNKS)
        )

if __name__ == "__main__":
    import sys

    main(sys.argv)
