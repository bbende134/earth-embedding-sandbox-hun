import bisect
import logging
import os
from contextlib import asynccontextmanager

import numpy as np
from area import area
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from geojson_pydantic import Polygon
from pydantic import BaseModel
from pymilvus import Collection, connections
from pyproj import CRS, Transformer
from ratelimiter import rate_limiter
from shapely import geometry
from shapely.ops import transform

from geojson import Feature, FeatureCollection

EMB_DIM = 64
COLLECTION = "geo_embeddings"

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
METRIC_TYPE = os.getenv("METRIC_TYPE", "IP")  # or "L2"
TARGET_CRS = os.getenv("TARGET_CRS")  # default to WGS84
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")

# e.g. z16 -> 160x160m -> 25600sqm
AREA_THRESHOLDS = {
    1600: 80,
    25600: 16,
    102400: 32,
    409600: 64,
    # 1638400: 128, # current dev dataset doesn't have 128 or 256 yet
    # 6553600: 256,
}


def connect():
    connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)


@asynccontextmanager
async def lifespan(_: FastAPI):
    connect()
    logger.info("API connected to milvus service.")
    yield


app = FastAPI(lifespan=lifespan)
origins = [
    "http://localhost:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("fastapi")


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    exc_str = f"{exc}".replace("\n", " ").replace("   ", " ")
    logger.error(f"{request}: {exc_str}")
    content = {"status_code": 10422, "message": exc_str, "data": None}
    return JSONResponse(content=content, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


app.add_exception_handler(RequestValidationError, validation_exception_handler)


class QueryResponse(BaseModel):
    query_lat: float
    query_lon: float
    neighbours: dict
    query_embedding: list[float]
    query_z: int


class NeighbourQuery(BaseModel):
    geojson: dict
    k: int = Query(20, ge=1, le=40)
    nprobe: int = Query(32, le=64)


def _get_vector_for_latlon(
    col: Collection, x_c: float, y_c: float, z: int, scale: float = 10.0
) -> np.ndarray:
    # Try get exactish match using the centroid and scale.

    expr = (
        f"lon > {x_c - z * scale / 2} and lon < {x_c + z * scale / 2} and "
        + f"lat > {y_c - z * scale / 2} and lat < {y_c + z * scale / 2} and z == {z}"
    )
    res = col.query(expr=expr, output_fields=["lon", "lat", "z", "embedding"], limit=1)
    if not res:
        return None
    return res[0]["embedding"]


def which_z(query_area: float) -> str:
    """
    z8 -> 80x80m -> 1600sqm
    z16 -> 160x160m -> 25600sqm
    z32 -> 320x320m -> 102400sqm
    z64 -> 640x640m -> 409600sqm
    z128 -> 1280x1280m -> 1638400sqm
    z256 -> 2560x2560m -> 6553600sqm
    """
    # get the largest z that is less than the query area
    idx = bisect.bisect_right(list(AREA_THRESHOLDS.keys()), query_area)

    if idx == 0:
        return 0

    # if idx > len(AREA_THRESHOLDS), the largest z is the last one
    return AREA_THRESHOLDS[list(AREA_THRESHOLDS.keys())[idx - 1]]


def reproject(geom, src_crs, dst_crs):
    """
    geom: Shapely geometry
    src_crs, dst_crs: anything pyproj accepts (EPSG code, proj string, WKT)
    """
    transformer = Transformer.from_crs(CRS(src_crs), CRS(dst_crs), always_xy=True)  # lon,lat order
    return transform(transformer.transform, geom)


@app.get("/healthz", dependencies=[Depends(rate_limiter(limit=10, window=60))])
def healthz():
    return {"ok": True}


@app.post(
    "/neighbours",
    response_model=QueryResponse,
    dependencies=[Depends(rate_limiter(limit=10, window=60))],
)
def neighbors(neighbour_query: NeighbourQuery):
    try:
        polygon = Polygon(**neighbour_query.geojson)
    except Exception as e:
        logger.error(f"Invalid GeoJSON: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid GeoJSON: {e}")

    try:
        shp = geometry.shape(polygon.__geo_interface__)
        query_area = area(shp.__geo_interface__)
    except Exception as e:
        logger.error(f"Error casting to geometry and getting area: {e}")
        raise HTTPException(status_code=400, detail=f"Error parsing GeoJSON: {e}")

    query_z = which_z(query_area)
    if query_z == 0:
        raise HTTPException(status_code=400, detail="Query area is too small for any embeddings.")

    logger.info(f"Query area: {query_area}, using z{query_z}")

    connect()
    col = Collection(COLLECTION)
    col.load()

    # get the shape centroid and then search +- z*10
    shp_utm = reproject(shp, "EPSG:4326", TARGET_CRS)
    shp_utm_centroid = shp_utm.centroid

    vec = _get_vector_for_latlon(col, shp_utm_centroid.x, shp_utm_centroid.y, query_z)
    if vec is None:
        raise HTTPException(
            status_code=404, detail="No embedding found at that lat/lon (try a snapped grid point)."
        )

    search_params = {"metric_type": METRIC_TYPE, "params": {"nprobe": neighbour_query.nprobe}}
    hits = col.search(
        data=[vec],
        anns_field="embedding",
        param=search_params,
        limit=neighbour_query.k,
        output_fields=["lon", "lat", "z", "embedding"],
    )[0]

    features = []
    for h in hits:
        # reproject back to original CRS if needed
        if TARGET_CRS != "EPSG:4326":
            hit_pt = reproject(
                geometry.Point(h.entity.get("lon"), h.entity.get("lat")), TARGET_CRS, "EPSG:4326"
            )
        else:
            hit_pt = geometry.Point(h.entity.get("lon"), h.entity.get("lat"))
        properties = {
            "z": h.entity.get("z"),
            "embedding": h.entity.get("embedding"),
            "distance": h.distance,
        }
        features.append(Feature(geometry=hit_pt, properties=properties))

    featurecollection = FeatureCollection(features)

    return QueryResponse(
        neighbours=featurecollection,
        query_embedding=vec,
        query_lat=shp_utm_centroid.y,
        query_lon=shp_utm_centroid.x,
        query_z=query_z,
    )
