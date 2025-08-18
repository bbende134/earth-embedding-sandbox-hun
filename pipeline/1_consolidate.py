# ~~~~~~~~ CONSOLIDATE PIPELINE ~~~~~~~~
# This pipeline consolidates the raw band data into a cohesive dataset and
# passes the first block-reduce step.
# If the pipeline is consolidating multiple archives, it can be called with
#  comma-separated --raw_archives.
# Otherwise it will use a single archive specified by --raw_archive.
# If consolidating multiple archives, you can also just merge them to a single
#  dataset first by using the --merge-only flag.


import logging

import apache_beam as beam
import xarray as xr
import xarray_beam as xbeam
from apache_beam.options.pipeline_options import PipelineOptions
from xarray_beam._src import core as xbeam_core

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

RAW_CHUNKS = {"time": 1, "X": 1024, "Y": 1024}
RAW_CHUNKS_WITH_FEATURES = {"features": 1, "time": 1, "X": 1024, "Y": 1024}
STACKED_CHUNKS = {"features": -1, "time": 1, "X": 1024, "Y": 1024}
FINAL_CHUNKS = {"features": -1, "time": 1, "X": 64, "Y": 64}
ITEMSIZE = 4  # 1 variable x 4 bytes (float32)


class BlockMean(beam.PTransform):
    """Calculate the mean over one or more distributed dataset dimensions."""

    def __init__(self, boundary="pad", **dim_blocks):
        super().__init__()
        self.dim_blocks = dim_blocks
        self.boundary = boundary
        assert boundary in ["pad", "exact", "trim"], (
            f"Invalid boundary: {boundary}. boundary must be one of 'pad', 'exact', or 'trim':"
            + "https://docs.xarray.dev/en/stable/generated/xarray.DataArray.coarsen.html"
        )

    def _update_key(
        self, key: xbeam_core.Key, chunk: xr.Dataset
    ) -> tuple[xbeam_core.Key, xr.Dataset]:
        """set the new key offsets."""

        dims = self.dim_blocks.keys()
        new_offsets = {d: key.offsets[d] // self.dim_blocks[d] for d in dims if d in key.offsets}
        new_key = key.with_offsets(**new_offsets)
        return new_key, chunk

    def expand(self, pcoll):
        return (
            pcoll
            | beam.MapTuple(self._update_key)
            | beam.MapTuple(
                lambda key, chunk: (
                    key,
                    chunk.coarsen(**self.dim_blocks, boundary=self.boundary).mean(skipna=True),
                )
            )
        )


class CustomOptions(PipelineOptions):
    @classmethod
    def _add_argparse_args(cls, parser):
        parser.add_argument("--raw_archive", type=str, help="The input zarr path.")
        parser.add_argument(
            "--raw_archives", type=str, help="The input zarr paths for multiple archives."
        )
        parser.add_argument(
            "--intermediate_archive", type=str, help="The output consolidated zarr path."
        )
        parser.add_argument(
            "--reduced_archive", type=str, help="The output zarr path after block-reduction."
        )
        parser.add_argument(
            "--merge-only",
            type=bool,
            default=False,
            help="If true, only merge the input archives without further processing.",
        )


def to_array(ds: xr.Dataset) -> xr.DataArray:
    """Convert a dataset to a DataArray and squeeze it."""
    # reshape feature variables into a coordinate
    new_array = ds.to_dataarray(dim="features")

    # convert the feature label into int and sort it
    new_array = new_array.assign_coords(
        features=[int(f[1:]) for f in new_array.features.values]
    ).sortby("features")
    return xr.Dataset({"embeddings": new_array})


def main(argv: list[str]) -> None:
    options = PipelineOptions()
    custom_options = options.view_as(CustomOptions)

    assert custom_options.raw_archive or custom_options.raw_archives, (
        "Must specify one of --raw_archive or --raw_archives"
    )

    if custom_options.raw_archive:
        if custom_options.merge_only:
            # if we only want to merge the input archive, we can just use it directly
            raise ValueError(
                "Cannot specify --merge-only with --raw_archive. "
                "Use --raw_archives instead for merging multiple archives."
            )
        if custom_options.intermediate_archive:
            raise ValueError(
                "Cannot specify --intermediate_archive with --raw_archive. "
                "Use --raw_archives for merging multiple archives."
            )
        # single archive case
        ds_on_disk, source_chunks = xbeam.open_zarr(custom_options.raw_archive)

    elif custom_options.raw_archives:
        if custom_options.merge_only and not custom_options.intermediate_archive:
            raise ValueError(
                "Must specify --intermediate_archive when using --raw_archives and --merge-only."
            )
        # multiple archives case
        archives = custom_options.raw_archives.split(",")
        ds_on_disk = xr.merge([xr.open_zarr(archive) for archive in archives])
        source_chunks = RAW_CHUNKS

    template = xbeam.make_template(ds_on_disk)

    # stack features to a coodinate
    inner_array = template.to_dataarray(dim="features")
    # cast them to int and sort them
    inner_array = inner_array.assign_coords(
        features=[int(f[1:]) for f in inner_array.features.values]
    ).sortby("features")

    stacked_template = xr.Dataset({"embeddings": inner_array})

    blocked_template = stacked_template.coarsen(X=8, Y=8, boundary="pad").mean(skipna=True)

    print("~~~~ ds ~~~~")
    print(ds_on_disk)
    print("~~~~ template ~~~~")
    print(template)
    print("~~~~ stacked_template ~~~~")
    print(stacked_template)
    print("~~~~ blocked_template ~~~~")
    print(blocked_template)

    if custom_options.merge_only:
        # If we only want to merge the input archives, we can write them directly
        with beam.Pipeline(options=options) as root:
            _ = (
                root
                | xbeam.DatasetToChunks(ds_on_disk, chunks=source_chunks)
                | xbeam.ChunksToZarr(
                    custom_options.intermediate_archive, template=template, zarr_chunks=RAW_CHUNKS
                )
            )
    else:
        with beam.Pipeline(options=options) as root:
            _ = (
                root
                # First pull in the dataset and write it to chunks
                | xbeam.DatasetToChunks(ds_on_disk, chunks=source_chunks)
                # re-org to array and assign the new coordinates
                | beam.MapTuple(lambda k, ds: (k, to_array(ds)))
                | xbeam.SplitChunks({"features": 1, "time": 1, "X": 512, "Y": 512})
                | xbeam.ConsolidateChunks({"features": -1, "time": 1, "X": 512, "Y": 512})
                # block-reduce the dataset
                | BlockMean(X=8, Y=8, boundary="pad")
                # consolidate the chunks back together
                # | xbeam.ConsolidateChunks(target_chunks=FINAL_CHUNKS)
                | xbeam.ChunksToZarr(
                    custom_options.reduced_archive + "_z8",
                    template=blocked_template,
                    zarr_chunks=FINAL_CHUNKS,
                )
            )


if __name__ == "__main__":
    import sys

    main(sys.argv)
