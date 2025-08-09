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
STACKED_CHUNKS = {"features": -1, "time": 1, "X": 1024, "Y": 1024}
FINAL_CHUNKS = {"features": -1, "time": 1, "X": 64, "Y": 64}
ITEMSIZE = 4 # 1 variable x 4 bytes (float32)

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


class CustomOptions(PipelineOptions):
    @classmethod
    def _add_argparse_args(cls, parser):
        parser.add_argument("--raw_archive", type=str, required=True, help="The output zarr path.")
        parser.add_argument("--reduced_archive", type=str, required=True, help="The output consolidated zarr path.")



def to_array(ds: xr.Dataset) -> xr.DataArray:
    """Convert a dataset to a DataArray and squeeze it."""
    # reshape feature variables into a coordinate
    new_array = ds.to_dataarray(dim="features")

    # convert the feature label into int and sort it
    new_array = new_array.assign_coords(features=new_array.features.astype("uint8")).sortby("features")  
    return xr.Dataset({"embeddings": new_array})

def main(argv: list[str]) -> None:

    

    options = PipelineOptions()
    main_options = options.get_all_options()
    custom_options = options.view_as(CustomOptions)

    assert custom_options.raw_archive, "Must specify --raw_archive"
    assert custom_options.reduced_archive, "Must specify --reduced_archive"

    ds_on_disk, source_chunks = xbeam.open_zarr(custom_options.raw_archive)

    template = xbeam.make_template(ds_on_disk)

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
    print(ds_on_disk)
    print('~~~~ template ~~~~')
    print(template)
    print('~~~~ stacked_template ~~~~')
    print(stacked_template)
    print('~~~~ blocked_template ~~~~')
    print(blocked_template)

    with beam.Pipeline(options=options) as root:
        _ = (
            root 
            # First pull in the dataset and write it to chunks
            | xbeam.DatasetToChunks(ds_on_disk, chunks=source_chunks)
            # re-org to array and assign the new coordinates
            | beam.MapTuple(lambda k, ds: (k, to_array(ds)))
            # rechunk making the chunks contiguous on the features dimension
            # tofix: rechunker currently dies. Try a bigger VM? Stacked chunks at 1024?
            # | xbeam.Rechunk( 
            #     dim_sizes=stacked_template.sizes,
            #     itemsize=ITEMSIZE,
            #     source_chunks=RAW_CHUNKS_WITH_FEATURES, # 
            #     target_chunks=STACKED_CHUNKS,
            #     min_mem= 1024**2,  # 1 MiB
            # )
            | xbeam.SplitChunks({ "features": 1, "time": 1, "X": 512, "Y": 512 })
            | xbeam.ConsolidateChunks({ "features": -1, "time": 1, "X": 512, "Y": 512 })
            # block-reduce the dataset
            | BlockMean(X=8,Y=8,boundary="pad")
            # consolidate the chunks back tog
            # | xbeam.ConsolidateChunks(target_chunks=FINAL_CHUNKS)
            | xbeam.ChunksToZarr(custom_options.reduced_archive+"_z8", template=blocked_template, zarr_chunks=FINAL_CHUNKS)
        )

if __name__ == "__main__":
    import sys

    main(sys.argv)
