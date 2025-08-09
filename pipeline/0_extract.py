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
        parser.add_argument("--raw_archive", type=str, required=True, help="The output zarr path.")


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

    reproject = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True).transform
    aoi_27700 = transform(reproject, aoi)
    minx, _miny, _maxx, maxy = aoi_27700.bounds

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

    with beam.Pipeline(options=options) as root:
        _ = (
            root 
            # First pull in the dataset and write it to chunks
            | xbeam.DatasetToZarr(ds, custom_options.raw_archive, zarr_chunks=RAW_CHUNKS)
        )

if __name__ == "__main__":
    import sys

    main(sys.argv)
