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
COLLECTION = os.getenv("COLLECTION", "geo_embeddings")

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
METRIC_TYPE = os.getenv("METRIC_TYPE", "IP")  # or "L2"
TARGET_CRS = "EPSG:32633"  # UTM 33N
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
    query_embedding: list[float]
    query_z: int


class NeighbourQuery(BaseModel):
    geojson: dict
    k: int = Query(20, ge=1, le=200)
    nprobe: int = Query(32, le=64)
    year: int = Query(2024, ge=2017, le=2030)
    coordinate_system: str = Query("geographic", regex="^(geographic|utm)$")
    z: int = Query(None, ge=8, le=256)  # Optional: manual zoom level override


def _get_vector_for_latlon(col: Collection, x_c: float, y_c: float, z: int, year: int, scale: float = 100.0):
    # Try get exactish match using the centroid and scale.
    # Start with smaller scales for more precise matching

    candidates = []
    for s in [10.0, 50.0, 100.0, 500.0, 1000.0]:  # start smaller, increase gradually
        expr = (
            f"lon > {x_c - z * s / 2} and lon < {x_c + z * s / 2} and "
            + f"lat > {y_c - z * s / 2} and lat < {y_c + z * s / 2} and z == {z} and year == {year}"
        )
        print(f"Query expr: {expr}")
        res = col.query(
            expr=expr, output_fields=["lon", "lat", "z", "year", "embedding"], limit=20  # get more candidates
        )
        print(f"Query result: {len(res)} records at scale {s}")

        # Collect all valid embeddings
        for r in res:
            emb = r["embedding"]
            if emb and any(e != 0 for e in emb):  # valid non-zero embedding
                # Calculate distance from centroid
                dist = ((r["lon"] - x_c) ** 2 + (r["lat"] - y_c) ** 2) ** 0.5
                candidates.append((emb, dist, r["lon"], r["lat"]))

    if not candidates:
        return None

    # Sort by distance (closest first) and return the closest valid embedding
    candidates.sort(key=lambda x: x[1])
    closest_emb, closest_dist, closest_lon, closest_lat = candidates[0]
    print(f"Selected embedding at distance {closest_dist:.6f} from centroid (lon={closest_lon:.6f}, lat={closest_lat:.6f})")
    return closest_emb


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


@app.get("/healthz", dependencies=[Depends(rate_limiter(limit=100, window=60))])
def healthz():
    return {"ok": True}


@app.post(
    "/neighbours",
    response_model=QueryResponse,
    dependencies=[Depends(rate_limiter(limit=100, window=60))],
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

    query_z = neighbour_query.z if neighbour_query.z is not None else which_z(query_area)
    if query_z == 0:
        raise HTTPException(status_code=400, detail="Query area is too small for any embeddings.")

    logger.info(f"Query area: {query_area}, using z{query_z} {'(manual)' if neighbour_query.z is not None else '(auto)'}")

    connect()
    col = Collection(COLLECTION)
    col.load()

    # Handle coordinate system conversion
    if neighbour_query.coordinate_system == "utm":
        # Input is already in UTM, convert to geographic for centroid calculation
        shp_geo = reproject(shp, TARGET_CRS, "EPSG:4326")
        shp_geo_centroid = shp_geo.centroid
        shp_utm_centroid = shp_geo_centroid  # Already in UTM space
    else:
        # Input is geographic, convert to UTM as before
        shp_utm = reproject(shp, "EPSG:4326", TARGET_CRS)
        shp_utm_centroid = shp_utm.centroid
        shp_geo_centroid = reproject(shp_utm_centroid, TARGET_CRS, "EPSG:4326")

    print(f"Query centroid UTM: {shp_utm_centroid.x}, {shp_utm_centroid.y}, z={query_z}")
    print(f"Query centroid Geo: {shp_geo_centroid.x}, {shp_geo_centroid.y}")

    vec = _get_vector_for_latlon(
        col, shp_geo_centroid.x, shp_geo_centroid.y, query_z, neighbour_query.year, scale=10000.0
    )
    if vec is None:
        raise HTTPException(
            status_code=404, detail="No embedding found at that lat/lon (try a snapped grid point)."
        )

    search_params = {"metric_type": METRIC_TYPE, "params": {"nprobe": neighbour_query.nprobe}}
    # Filter by year AND zoom level in the search expression to search within the same resolution
    expr = f"year == {neighbour_query.year} and z == {query_z}"
    hits = col.search(
        data=[vec],
        anns_field="embedding",
        param=search_params,
        limit=neighbour_query.k,
        expr=expr,
        output_fields=["lon", "lat", "z", "year", "embedding"],
    )[0]

    # If we don't get enough results at the same zoom level, try without zoom constraint
    if len([h for h in hits if not any(e == 0 for e in h.entity.get("embedding", []))]) < neighbour_query.k:
        print(f"DEBUG: Only {len(hits)} valid results at z={query_z}, trying cross-zoom search")
        expr_fallback = f"year == {neighbour_query.year}"
        hits = col.search(
            data=[vec],
            anns_field="embedding",
            param=search_params,
            limit=neighbour_query.k,
            expr=expr_fallback,
            output_fields=["lon", "lat", "z", "year", "embedding"],
        )[0]

    features = []
    for h in hits:
        embedding = h.entity.get("embedding")
        print(f"Processing hit with embedding: {embedding[:5]}..., year: {h.entity.get('year')}")
        # Skip if embedding is all zeros
        if not any(e != 0 for e in embedding):
            print("Skipping zero embedding")
            continue
        # Coordinates are already in EPSG:4326 (lat/lon) from database
        hit_pt = geometry.Point(h.entity.get("lon"), h.entity.get("lat"))
        properties = {
            "z": h.entity.get("z"),
            "year": h.entity.get("year"),
            "embedding": embedding,
            "distance": h.distance,
        }
        print(f"Properties before Feature creation: {properties}")
        features.append(Feature(geometry=hit_pt, properties=properties))

    # If not enough non-zero embeddings, try with larger limit
    if len(features) < neighbour_query.k:
        larger_limit = neighbour_query.k * 10  # try 10 times more
        hits = col.search(
            data=[vec],
            anns_field="embedding",
            param=search_params,
            limit=larger_limit,
            expr=expr,
            output_fields=["lon", "lat", "z", "year", "embedding"],
        )[0]
        for h in hits:
            embedding = h.entity.get("embedding")
            print(f"Processing hit (second search) with embedding: {embedding[:5]}..., year: {h.entity.get('year')}")
            if not any(e != 0 for e in embedding):
                print("Skipping zero embedding (second search)")
                continue
            # Coordinates are already in EPSG:4326 (lat/lon) from database
            hit_pt = geometry.Point(h.entity.get("lon"), h.entity.get("lat"))
            properties = {
                "z": h.entity.get("z"),
                "year": h.entity.get("year"),
                "embedding": embedding,
                "distance": h.distance,
            }
            print(f"Properties before Feature creation (second search): {properties}")
            features.append(Feature(geometry=hit_pt, properties=properties))
            if len(features) >= neighbour_query.k:
                break

    featurecollection = FeatureCollection(features)

    return QueryResponse(
        neighbours=featurecollection,
        query_embedding=vec,
        query_lat=shp_geo_centroid.y,
        query_lon=shp_geo_centroid.x,
        query_z=query_z,
    )


@app.get("/years", dependencies=[Depends(rate_limiter(limit=100, window=60))])
def get_available_years():
    """Get list of available years in the database"""
    connect()
    col = Collection(COLLECTION)
    col.load()
    
    # Get all unique years
    years_result = col.query(
        expr='',
        output_fields=['year'],
        limit=10000  # Should be enough to get all unique years
    )
    
    unique_years = list(set(entity['year'] for entity in years_result))
    unique_years.sort(reverse=True)  # Most recent first
    
    return {"years": unique_years}
