"""Mean-reduce the dataset to 64x64 pixels"""

import json
import logging
import os

# from absl import flags
import dataclasses
import numpy as np
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

ZOOM_BLOCKS = [8,16,32,64,128,256]

class CustomOptions(PipelineOptions):
    @classmethod
    def _add_argparse_args(cls, parser):
        parser.add_argument(
            "--reduced_archive", type=str, required=True, help="The output zarr path."
        )
        parser.add_argument("--intermediary_archive", type=str, required=True, help="The input intermediary zarr path.")
        parser.add_argument(
            "--zoom_block",
            type=int,
            choices=ZOOM_BLOCKS,
            required=True,
            help=f"Must be one of {ZOOM_BLOCKS}"
        )


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

    assert custom_options.intermediary_archive, "Must specify --intermediary_archive"
    assert custom_options.reduced_archive, "Must specify --reduced_archive"
    assert custom_options.zoom_block in ZOOM_BLOCKS, f"Invalid zoom block: {custom_options.zoom_block}. Must be one of {ZOOM_BLOCKS}"

    target_chunks = {"features":-1, "time":1, "X":64, "Y":64}
    


    if custom_options.zoom_block == 8:
        coarsen_dim = {"X":8,"Y":8}
        ds_on_disk, source_chunks = xbeam.open_zarr(custom_options.intermediary_archive)
    else:
        coarsen_dim = {"X":2,"Y":2}
        previous_zoom = ZOOM_BLOCKS[ZOOM_BLOCKS.index(custom_options.zoom_block)-1]
        ds_on_disk, source_chunks = xbeam.open_zarr(custom_options.reduced_archive + f"_z{previous_zoom}")


    working_chunks = {k:v for k,v in source_chunks.items()}
    working_chunks.update({"features":-1})

    template = xbeam.make_template(ds_on_disk)
    new_template = template.coarsen(**coarsen_dim, boundary="pad").mean(skipna=True)


    # itemsize: i.e. sum([as_bytes(var.dtype) for var in ds])
    itemsize = 1 # one variable (embeddings) with int8 dtype

    print ('~~~~ ds_on_disk~~~')
    print (ds_on_disk)
    print ("~~~~~ template ~~~~~")
    print (template) #8x8
    print ("~~~ new template ~~~")
    print (new_template)


    if custom_options.zoom_block == 8:

        with beam.Pipeline(options=options) as root:
            _ = (
                root
                | xbeam.DatasetToChunks(ds_on_disk, chunks=source_chunks)
                | xbeam.Rechunk( # make chunks contiguous on features dimension
                    dim_sizes=ds_on_disk.sizes,
                    itemsize=itemsize,
                    source_chunks=source_chunks,
                    target_chunks=working_chunks,
                )
                | BlockMean(**coarsen_dim,boundary="pad")
                | xbeam.ChunksToZarr(custom_options.reduced_archive+"_z8", template=new_template, zarr_chunks=target_chunks)
            )
    else:
        with beam.Pipeline(options=options) as root:
            _ = (
                root
                | xbeam.DatasetToChunks(ds_on_disk, chunks=source_chunks)
                | BlockMean(**coarsen_dim,boundary="pad")
                | xbeam.ConsolidateChunks(target_chunks=target_chunks)
                | xbeam.ChunksToZarr(
                    custom_options.reduced_archive+"_z"+str(custom_options.zoom_block), 
                    template=new_template, 
                    zarr_chunks=target_chunks
                )
            )
            

if __name__ == "__main__":
    import sys
    main(sys.argv)