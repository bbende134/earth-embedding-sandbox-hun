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
from apache_beam.options.pipeline_options import PipelineOptions
from cloudpathlib import GSPath
from shapely.geometry import shape
from shapely.ops import transform
from xee import EarthEngineBackendEntrypoint

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
            nargs="+",
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
        parser.add_argument("--output", type=str, required=True, help="The output zarr path.")
        parser.add_argument(
            "--ee_service_account_email",
            type=str,
            required=False,
            help="Service account email for Google Cloud authentication.",
        )
        parser.add_argument(
            "--gcp_project_id", type=str, required=True, help="Google Cloud project ID."
        )


# Borrowed from the xbeam examples:
# https://github.com/google/xarray-beam/blob/4f4fcb965a65b5d577601af311d0e0142ee38076/examples/xbeam_rechunk.py#L41
def _parse_chunks_str(chunks_str: str) -> dict[str, int]:
    chunks = {}
    parts = chunks_str.split(",")
    for part in parts:
        k, v = part.split("=")
        chunks[k] = int(v)
    return chunks


def main(argv: list[str]) -> None:
    options = PipelineOptions()
    custom_options = options.view_as(CustomOptions)

    assert custom_options.input_geojson, "Must specify --input_geojson"
    assert custom_options.output, "Must specify --output"
    assert custom_options.gcp_project_id, "Must specify --gcp_project_id"

    target_chunks = {"time": 1, "X": 256, "Y": 256}

    credentials = ee.ServiceAccountCredentials(
        custom_options.ee_service_account_email, os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    )

    ee.Initialize(
        credentials=credentials,
        project=custom_options.gcp_project_id,
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
    scale = 100  # meters

    affine = [
        scale,
        0,
        minx,
        0,
        -scale,
        maxy,
    ]

    ic = (
        ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
        .filterDate(ee.Date("2023-12-30"), ee.Date("2024-01-02"))
        .filterBounds(aoi_ee)
        .select(["A20", "A01"])
        .mosaic()
    )

    ds = xr.open_dataset(
        ic,
        engine=EarthEngineBackendEntrypoint,
        projection=ee.Projection("EPSG:27700", transform=affine),
        geometry=list(aoi.bounds),
        scale=100,
        chunks={"time": 1, "X": 256, "Y": 256},
        ee_init_if_necessary=True,
        ee_init_kwargs={
            "project": custom_options.gcp_project_id,
            "url": os.environ.get("HV_URL", "https://earthengine-highvolume.googleapis.com"),
        },
    )

    with beam.Pipeline(options=options) as root:
        _ = root | xbeam.DatasetToZarr(ds, custom_options.output, target_chunks)


if __name__ == "__main__":
    import sys

    main(sys.argv)
