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
import time
import os
import itertools
from typing import AbstractSet, Iterator, Mapping, Optional, Sequence
from math import ceil

# from absl import flags
import apache_beam as beam
from apache_beam import pvalue
from shapely import geometry
import ee
from zarr.errors import GroupNotFoundError
import pyproj
import xarray as xr
import xarray_beam as xbeam
from xarray_beam._src import core as xbeam_core
from xarray_beam._src.core import Key
from apache_beam.options.pipeline_options import PipelineOptions
from cloudpathlib import GSPath
from shapely.geometry import shape
from shapely.ops import transform
from xee import EarthEngineBackendEntrypoint
from xarray_beam._src import threadmap

from pipeline.blockmean import BlockMean
from pipeline.common import (
    ALL_BANDS,
    RAW_CHUNKS,
    RAW_CHUNKS_WITH_FEATURES,
    STACKED_CHUNKS,
    FINAL_CHUNKS,
    ITEMSIZE,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


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
        # parser.add_argument(
        #     "--n_x",
        #     type=int,
        #     default=2,
        #     help="number of columns to split the aoi into."
        # )
        # parser.add_argument(
        #     "--n_y",
        #     type=int,
        #     default=2,
        #     help="number of rows to split the aoi into."
        # )
        # parser.add_argument(
        #     "--i_x",
        #     type=int,
        #     required=True
        # )
        # parser.add_argument(
        #     "--i_y",
        #     type=int,
        #     required=True
        # )
        parser.add_argument("--raw_archive", type=str, required=True, help="The output zarr path.")

def main(argv: list[str]) -> None:

    options = PipelineOptions()
    main_options = options.get_all_options()
    custom_options = options.view_as(CustomOptions)

    assert custom_options.input_geojson, "Must specify --input_geojson"
    assert custom_options.raw_archive, "Must specify --raw_archive"
    if not custom_options.bands:
        bands = ALL_BANDS
    else:
        bands = [b.strip() for b in custom_options.bands.split(",")]

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

    reproject = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True).transform
    #aoi_27700 = transform(reproject, aoi)
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

    aoi_utm_ee = ee.Geometry(aoi_utm.__geo_interface__, proj=ee.Projection("EPSG:32630", transform=affine))

    im_float = (
        ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
        .filterDate(ee.Date("2023-12-30"), ee.Date("2024-01-02"))
        .filterBounds(aoi_ee)
        .filter(ee.Filter.eq("UTM_ZONE", "30N"))
        .select(bands)
        .mosaic()
        #.rename([f"{ii}" for ii in range(len(bands))])
        .clip(aoi_ee)
    )

    

    # quantize according to the original paper https://arxiv.org/pdf/2507.22291
    # im_quantized = (
    #     im_float
    #     .abs()
    #     .pow(ee.Image.constant(0.5))
    #     .multiply(im_float.signum())
    #     .multiply(ee.Image.constant(127)) # [-128, 127] 
    #     .clamp(-127, 127)
    #     .int8()  # Convert to int8
    # )

    ds = xr.open_dataset(
        im_float,
        engine=EarthEngineBackendEntrypoint,
        projection=ee.Projection("EPSG:32630", transform=affine),
        geometry=list(aoi.bounds),
        scale=scale,
        chunks=RAW_CHUNKS,
        ee_init_if_necessary=True,
        ee_init_kwargs={
            "project": main_options['project'],
            "url": os.environ.get("HV_URL", "https://earthengine-highvolume.googleapis.com"),
        },
        executor_kwargs={
            "max_workers": main_options['ee_max_num_workers'],
        },
        getitem_kwargs={
            "max_retries":10,
            "initial_delay": 1000, # delay 1s before retrying
        }
    )

    


    def iter_chunk_keys(
        offsets: Mapping[str, Sequence[int]],
        vars: Optional[AbstractSet[str]] = None,  # pylint: disable=redefined-builtin
        chunks=RAW_CHUNKS,
    ) -> Iterator[Key]:
        """Iterate over the Key objects corresponding to the given chunks."""
        chunk_indices = [range(len(sizes)) for sizes in offsets.values()]
        count_skipped_aois = 0
        for indices in itertools.product(*chunk_indices):
            key_offsets = {
                dim: offsets[dim][index] for dim, index in zip(offsets, indices)
            }
            # check intersection of bbox with the area of interest
            query_shp = geometry.box(
                    ds.X[key_offsets["X"]],
                    ds.Y[key_offsets["Y"]],
                    ds.X[min(key_offsets["X"] + chunks["X"]-1, ds.sizes["X"]-1)],
                    ds.Y[min(key_offsets["Y"] + chunks["Y"]-1, ds.sizes["Y"]-1)],
                )
            
            if aoi_utm.intersects(
                query_shp
            ):
                if vars is None:
                    yield Key(key_offsets)
                else:
                    yield Key(key_offsets, vars)
            else:
                count_skipped_aois += 1
                if count_skipped_aois % 100 == 0:
                    print(f"Skipped {count_skipped_aois} AOIs that do not intersect with the area of interest.")
                continue


    class ShimDatasetToChunks(xbeam_core.DatasetToChunks):
        """ Shim to tell dataset to chunks to never reshuffle - big bottleneck"""

        def _iter_all_keys(self) -> Iterator[Key]:
            """Iterate over all Key objects."""
            if not self.split_vars:
                yield from iter_chunk_keys(self.offsets)
            else:
                for name, variable in self._first.items():
                    relevant_offsets = {
                        k: v for k, v in self.offsets.items() if k in variable.dims
                    }
                    yield from iter_chunk_keys(relevant_offsets, vars={name})  # pytype: disable=wrong-arg-types  # always-use-property-annotation


        def expand(self, pcoll):

            key_pcoll = pcoll | beam.Create(self._iter_all_keys())

            return key_pcoll | "KeyToChunks" >> threadmap.FlatThreadMap(
                self._key_to_chunks, num_threads=self.num_threads
            )

    template = xbeam.make_template(ds)

    # # check the zarr archive exists already
    # try:
    #     check_archive = xr.open_zarr(
    #         custom_options.raw_archive
    #     )
    #     # xr.testing.assert_identical(check_archive, ds.chunk(RAW_CHUNKS))
    # except (FileNotFoundError, GroupNotFoundError):
    #     print(f"Zarr archive {custom_options.raw_archive} does not exist, creating.")
    #     # if the archive does not exist, we can write it.
    #     ds.chunk(RAW_CHUNKS).to_zarr(
    #         custom_options.raw_archive,
    #         mode="w",
    #         compute=False
    #     )

    

    # MEGACHUNKS = {
    #     "X":ceil(ceil(ds.sizes["X"] / RAW_CHUNKS["X"]) / custom_options.n_x) * RAW_CHUNKS["X"],
    #     "Y":ceil(ceil(ds.sizes["Y"] / RAW_CHUNKS["Y"]) / custom_options.n_y) * RAW_CHUNKS["Y"],
    # }
    # print (MEGACHUNKS)

    print ('~~~~ ds ~~~~')
    print(ds)
    print ('~~~~ template ~~~~')
    print(template)

    breakpoint()

    # bigtask = xbeam.DatasetToChunks(ds, chunks=RAW_CHUNKS, split_vars=False, num_threads=main_options["ee_max_num_workers"])
    # print ('~~~~ bigtask ~~~~')
    # print(bigtask)
    # print ('task counct', bigtask._task_count())
    # print ('shardcount', bigtask._shard_count())
    # tic = time.time()
    # print (' all keys')
    # all_keys = list(bigtask._iter_all_keys())
    # print (' all keys count', len(all_keys))
    # print (' all keys first 10', all_keys[:10])
    # print (' all keys took', time.time() - tic)

    # def subpipeline_region(base, x_offset, y_offset, gate_token):
    #     seed = base | f"Seed-{x_offset}-{y_offset}" >> beam.Create([None])

    #     # Gate this branch on the previous token (if any)
    #     if gate_token is not None:
    #         seed = seed | f"GateOnPrev-{x_offset}-{y_offset}" >> beam.Map(lambda x, _: x, pvalue.AsSingleton(gate_token))

    #     # Do your work for this level
    #     pc = (
    #         seed
    #         | f"DS2C-{x_offset}-{y_offset}" >> ShimDatasetToChunks(
    #             ds.isel({
    #                 "X":slice(x_offset, x_offset+MEGACHUNKS["X"]),
    #                 "Y":slice(y_offset, y_offset+MEGACHUNKS["Y"])
    #             }),
    #             chunks=RAW_CHUNKS,
    #             split_vars=False,
    #             num_threads=main_options["ee_max_num_workers"]
    #         )
    #     )
        

    #     # Write sink (returns PDone; keep pc around for token)
    #     _ = (
    #         pc 
    #         | f"C2Z_{x_offset}-{y_offset}" >> xbeam.ChunksToZarr(
    #             custom_options.raw_archive,
    #             template=template,
    #             zarr_chunks=RAW_CHUNKS,
    #             num_threads=main_options["ee_max_num_workers"],
    #         )
    #     )

    #     # Produce a tiny PCollection token once this branch has produced all its elements
    #     # (materialize pc; this gates the *next* branch)
    #     token = (
    #         pc
    #         | f"Count-{x_offset}-{y_offset}" >> beam.combiners.Count.Globally()
    #         | f"Token-{x_offset}-{y_offset}" >> beam.Map(lambda _: None)
    #     )
    #     return token



    # with beam.Pipeline(options=options) as root:

    #     # maybe also setup zarr here?
    #     # base = (
    #     #     root 
    #     #     | "BaseSeed" >> beam.Create([None])
    #     # )

    #     # use a token to ensure parallel branches are actually done in series.
    #     prev_token = None

    #     for x_offset, y_offset in itertools.product(
    #         [x*MEGACHUNKS["X"] for x in range((ds.sizes["X"] // MEGACHUNKS["X"]) + 1)],
    #         [y*MEGACHUNKS["Y"] for y in range((ds.sizes["Y"] // MEGACHUNKS["Y"]) + 1)]
    #     ):
    #         print(f'checking {x_offset=}; {y_offset=}')
    #         # if the current megachunk intersects with the area of interest then process it.
    #         query_box = geometry.box(
    #                 float(ds.X[x_offset]),
    #                 float(ds.Y[y_offset]),
    #                 float(ds.X[min(ds.sizes["X"]-1, (x_offset + (MEGACHUNKS["X"]-1)))]), # access the last element safely like slice
    #                 float(ds.Y[min(ds.sizes["Y"]-1, (y_offset + (MEGACHUNKS["Y"]-1)))])  # access the last element safely like slice
    #             )
    #         if aoi_27700.intersects(
    #             query_box
    #         ):
    #             print (f'doing it. {x_offset} {y_offset}')
    #             # If the AOI intersects with the chunk, process it
    #             prev_token = subpipeline_region(
    #                 root,
    #                 x_offset=x_offset,
    #                 y_offset=y_offset,
    #                 gate_token=prev_token,
    #             )
    #         else:
    #             print ("not doing it")
    #             print (query_box)

    # x_slice = slice(
    #     custom_options.i_x * MEGACHUNKS["X"],
    #     (custom_options.i_x + 1) * MEGACHUNKS["X"]
    # )
    # y_slice = slice(
    #     custom_options.i_y * MEGACHUNKS["Y"],
    #     (custom_options.i_y + 1) * MEGACHUNKS["Y"]
    # )

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
                zarr_chunks=RAW_CHUNKS,
                # num_threads=main_options["ee_max_num_workers"],
                # needs_setup=False
            )
        )

            
if __name__ == "__main__":
    import sys

    main(sys.argv)
