import apache_beam as beam
import xarray as xr
from xarray_beam._src import core as xbeam_core

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