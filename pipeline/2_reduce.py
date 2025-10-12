# ~~~~~~~~ REDUCE PIPELINE ~~~~~~~~
# This pipeline reduces the consolidated dataset into pyramided meanpool zoom levels.
# These pyramided zoom levels are used based on the size of the query dataset.

import logging
import os

import apache_beam as beam
import dotenv
import xarray as xr
import xarray_beam as xbeam
from apache_beam.options.pipeline_options import PipelineOptions
from xarray_beam._src import core as xbeam_core

dotenv.load_dotenv()


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

ALL_BANDS = [f"A{i:02d}" for i in range(64)]
RAW_CHUNKS = {"time": 1, "X": 1024, "Y": 1024}
RAW_CHUNKS_WITH_FEATURES = {"features": 1, "time": 1, "X": 1024, "Y": 1024}
STACKED_CHUNKS = {"features": -1, "time": 1, "X": 1024, "Y": 1024}
FINAL_CHUNKS = {"features": -1, "time": 1, "X": 64, "Y": 64}
ZOOM_BLOCKS = [8, 16, 32, 64, 128, 256]
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
        parser.add_argument(
            "--reduced_archive", type=str, required=True, help="The output zarr path."
        )
        parser.add_argument("--direct_runner_grpc_max_message_size", type=int, default=4294967296)


def main(argv: list[str]) -> None:
    options = PipelineOptions()
    main_options = options.get_all_options()
    custom_options = options.view_as(CustomOptions)

    assert custom_options.reduced_archive, "Must specify --reduced_archive"

    if main_options.get("service_account_email"):
        import gcsfs
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
            scopes=["https://www.googleapis.com/auth/devstorage.read_write"],
        )
        fs = gcsfs.GCSFileSystem(token=credentials)
        store = fs.get_mapper(custom_options.reduced_archive + "_z8")
        ds_on_disk = xr.open_zarr(store, chunks=None)
        source_chunks = {}
        for dim in ds_on_disk.dims:
            if dim in ds_on_disk.chunks:
                source_chunks[dim] = ds_on_disk.chunks[dim][0]
            else:
                source_chunks[dim] = ds_on_disk.sizes[dim]
    else:
        ds_on_disk, source_chunks = xbeam.open_zarr(custom_options.reduced_archive + "_z8")

    templates = {"z8": xbeam.make_template(ds_on_disk)}

    for zoom_block in ZOOM_BLOCKS[1:]:
        templates[f"z{zoom_block}"] = (
            templates["z8"]
            .coarsen(X=int(zoom_block / 8), Y=int(zoom_block / 8), boundary="trim")
            .mean(skipna=True)
        )

    print("~~~~ ds_on_disk~~~")
    print(ds_on_disk)
    print("~~~~~ templates ~~~~~")
    for k, v in templates.items():
        print(f"~~~~~ template {k} ~~~~~")
        print(v)

    with beam.Pipeline(options=options) as root:
        base = (
            root
            | "read_dataset" >> xbeam.DatasetToChunks(ds_on_disk, chunks=source_chunks)
            | "rechunk_pre_parallel"
            >> xbeam.ConsolidateChunks({"features": -1, "time": 1, "X": 256, "Y": 256})
        )

        results = []
        for size in [16, 32, 64, 128, 256]:
            branch = (
                base
                | f"bm{size}" >> BlockMean(X=int(size / 8), Y=int(size / 8), boundary="trim")
                | f"cc{size}"
                >> xbeam.ConsolidateChunks(
                    target_chunks={"features": -1, "time": 1, "X": 64, "Y": 64}
                )
                | f"c2z{size}"
                >> xbeam.ChunksToZarr(
                    custom_options.reduced_archive + f"_z{size}",
                    template=templates[f"z{size}"],
                    zarr_chunks={"features": -1, "time": 1, "X": 64, "Y": 64},
                )
            )
            results.append(branch)


if __name__ == "__main__":
    import sys

    main(sys.argv)
