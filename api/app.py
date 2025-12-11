import logging
import os
from contextlib import asynccontextmanager

import redis
from area import area
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from geojson import Feature, FeatureCollection
from geojson_pydantic import Polygon
from pydantic import BaseModel
from pymilvus import Collection, connections
from pyproj import CRS, Transformer
from shapely import geometry
from shapely.ops import transform

from api.ratelimiter import check_redis_connection, rate_limiter

EMB_DIM = 64
COLLECTION = os.getenv("COLLECTION", "high_res_hun")

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
METRIC_TYPE = os.getenv("METRIC_TYPE", "L2")  # or "L2"
TARGET_CRS = "EPSG:4326"  # WGS84
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")

# e.g. z16 -> 160x160m -> 25600sqm
AREA_THRESHOLDS = {
    1600: 80,
    25600: 16,
    102400: 32,
    409600: 64,
    1638400: 128,
    6553600: 256,
}


def connect():
    connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        check_redis_connection()
    except redis.ConnectionError:
        raise HTTPException(
            status_code=503, detail="Redis connection failed. Please check your Redis service."
        )
    connect()
    col = Collection(COLLECTION)
    col.load()
    logger.info("API connected to milvus service.")
    yield


app = FastAPI(lifespan=lifespan)
origins = [
    "http://localhost:3000",
    "http://192.168.1.72:3000",
    "https://100.123.97.11:3000"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)

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
    query_vector: list[float]
    query_z: int


class NeighbourQuery(BaseModel):
    geojson: dict
    k: int = Query(20, ge=1, le=1000)
    nprobe: int = Query(32, le=64)


def _get_vector_for_latlon(col: Collection, x_c: float, y_c: float, z: int, scale: float = 100.0):
    # Try get exactish match using the centroid and scale.
    # Scale factors adapted for WGS84 (degrees)
    # 0.00001 deg ~ 1.1m
    # 0.0001 deg ~ 11m
    # 0.001 deg ~ 111m
    
    # We want to find a tile center close to our query point.
    # If the DB has z=16 tiles (approx 160m), searching with a small window is fine.
    
    for s in [0.00001, 0.00005, 0.0001, 0.0005, 0.001]:  # try increasing scales
        expr = (
            f"lon > {x_c - s} and lon < {x_c + s} and "
            + f"lat > {y_c - s} and lat < {y_c + s}"
        )
        # Note: removed z constraint temporarily or permanently if mixed z logic is complex?
        # The original code had `z == {z}`. Let's add it back if we trust our z calculation,
        # but for now let's leave it out to find *any* matching vector nearby.
        # Actually, let's just prioritize spatial match.
        
        print(f"Query expr: {expr}")
        res = col.query(
            expr=expr, output_fields=["lon", "lat", "z", "vector"], limit=10
        )  # get more
        print(f"Query result: {len(res)} records")
        for r in res:
            emb = r["vector"]
            if any(e != 0 for e in emb):  # find first non-zero
                return emb
    return None  # if all zero or none


def which_z(query_area: float) -> int:
    """
    z8 -> 80x80m -> 1600sqm
    z16 -> 160x160m -> 25600sqm
    z32 -> 320x320m -> 102400sqm
    z64 -> 640x640m -> 409600sqm
    z128 -> 1280x1280m -> 1638400sqm
    z256 -> 2560x2560m -> 6553600sqm
    """
    # get the smallest area >= query_area, return its z
    for area in sorted(AREA_THRESHOLDS.keys()):
        if query_area <= area:
            return AREA_THRESHOLDS[area]
    # if larger than all, return the largest z
    return AREA_THRESHOLDS[sorted(AREA_THRESHOLDS.keys())[-1]]


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
    print("Neighbors function called")
    geojson = neighbour_query.geojson
    if geojson.get("type") == "Feature":
        geojson = geojson["geometry"]
    try:
        polygon = Polygon(**geojson)
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
        raise HTTPException(status_code=400, detail="Query area is too small for any vectors.")

    logger.info(f"Query area: {query_area}, using z{query_z}")

    connect()
    col = Collection(COLLECTION)
    col.load()

    # get the shape centroid and then search +- z*10
    shp_utm = reproject(shp, "EPSG:4326", TARGET_CRS)
    shp_utm_centroid = shp_utm.centroid

    print(f"Query centroid UTM: {shp_utm_centroid.x}, {shp_utm_centroid.y}, z={query_z}")

    vec = _get_vector_for_latlon(
        col, shp_utm_centroid.x, shp_utm_centroid.y, query_z, scale=10000.0
    )
    if vec is None:
        raise HTTPException(
            status_code=404, detail="No vector found at that lat/lon (try a snapped grid point)."
        )

    search_params = {"metric_type": METRIC_TYPE, "params": {"nprobe": neighbour_query.nprobe}}
    hits = col.search(
        data=[vec],
        anns_field="vector",
        param=search_params,
        limit=neighbour_query.k,
        output_fields=["lon", "lat", "z", "vector"],
    )[0]

    features = []
    for h in hits:
        vector = h.entity.get("vector")
        # Skip if vector is all zeros
        if not any(e != 0 for e in vector):
            continue
        # reproject back to original CRS if needed
        if TARGET_CRS != "EPSG:4326":
            hit_pt = reproject(
                geometry.Point(h.entity.get("lon"), h.entity.get("lat")), TARGET_CRS, "EPSG:4326"
            )
        else:
            hit_pt = geometry.Point(h.entity.get("lon"), h.entity.get("lat"))
        properties = {
            "z": h.entity.get("z"),
            "vector": vector,
            "distance": h.distance,
        }
        features.append(Feature(geometry=hit_pt, properties=properties))

    # If not enough non-zero vectors, try with larger limit
    if len(features) < neighbour_query.k:
        larger_limit = neighbour_query.k * 10  # try 10 times more
        hits = col.search(
            data=[vec],
            anns_field="vector",
            param=search_params,
            limit=larger_limit,
            output_fields=["lon", "lat", "z", "vector"],
        )[0]
        for h in hits:
            vector = h.entity.get("vector")
            if not any(e != 0 for e in vector):
                continue
            if TARGET_CRS != "EPSG:4326":
                hit_pt = reproject(
                    geometry.Point(h.entity.get("lon"), h.entity.get("lat")),
                    TARGET_CRS,
                    "EPSG:4326",
                )
            else:
                hit_pt = geometry.Point(h.entity.get("lon"), h.entity.get("lat"))
            properties = {
                "z": h.entity.get("z"),
                "vector": vector,
                "distance": h.distance,
            }
            features.append(Feature(geometry=hit_pt, properties=properties))
            if len(features) >= neighbour_query.k:
                break

    featurecollection = FeatureCollection(features)

    return QueryResponse(
        neighbours=featurecollection,
        query_vector=vec,
        query_lat=shp_utm_centroid.y,
        query_lon=shp_utm_centroid.x,
        query_z=query_z,
    )
