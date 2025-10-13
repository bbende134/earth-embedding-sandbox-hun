import ee
import pyproj
import xarray as xr
import zarr
from shapely.geometry import Polygon
from shapely.ops import transform


def test_clip_small_dataset(ee_initialize):
    aoi = Polygon(
        [
            [-1.406189579662267, 51.80481237120908],
            [-1.406189579662267, 51.70662720111915],
            [-1.171356815990392, 51.70662720111915],
            [-1.171356815990392, 51.80481237120908],
        ]
    )

    reproject = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True).transform
    aoi_27700 = transform(reproject, aoi)
    minx, miny, maxx, maxy = aoi_27700.bounds

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

    aoi_ee = ee.Geometry.Polygon(list(aoi.exterior.coords)[0:4])

    ic = (
        ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
    .filterDate(ee.Date("2025-09-01"), ee.Date("2025-10-13"))
        .filterBounds(aoi_ee)
        .select(["A20", "A01"])
        .mosaic()
    )

    ds = xr.open_dataset(
        ic,
        engine="ee",
        projection=ee.Projection("EPSG:27700", transform=affine),
        geometry=list(aoi.bounds),
        scale=100,
    )

    ds = ds.to_array(dim="feature")

    print("materializing to zarr")
    ds.to_zarr("test.zarr", mode="w")

    z = zarr.open("test.zarr")

    assert True
