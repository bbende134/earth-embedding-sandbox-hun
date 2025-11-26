# Earth Embedding Sandbox - Complete Project Guide

## Table of Contents
1. [Project Overview](#project-overview)
2. [What is a Zarr File?](#what-is-a-zarr-file)
3. [Architecture & Data Flow](#architecture--data-flow)
4. [How to Use Embeddings](#how-to-use-embeddings)
5. [How to Add Different Embeddings](#how-to-add-different-embeddings)
6. [Detailed Pipeline Flows](#detailed-pipeline-flows)
7. [Local Development Setup](#local-development-setup)
8. [Running Pipelines](#running-pipelines)

---

## Project Overview

**Earth Embedding Sandbox** is a full-stack geospatial similarity search application that:

1. **Extracts** DeepMind's AlphaEarth Foundations embeddings from Google Earth Engine
2. **Processes** 64-dimensional embeddings at multiple zoom levels (pyramid structure)
3. **Stores** embeddings in a Milvus vector database
4. **Serves** a web UI for polygon-based similarity search across geographic regions

### Key Features

- **64D Embeddings**: Each 10m×10m pixel on Earth has a learned feature vector
- **Multi-Resolution**: Embeddings are mean-pooled to 80m, 160m, 320m, 640m, 1280m, and 2560m per pixel
- **Vector Similarity**: Find locations with similar geographic/environmental characteristics
- **Year-Based Filtering**: Query data from different years (e.g., 2017, 2024)
- **Fast Similarity Search**: Uses Milvus vector database with IVF_FLAT indexing
- **Spatial Awareness**: Results returned as GeoJSON with lat/lon coordinates

---

## What is a Zarr File?

### Definition
**Zarr** is a cloud-native array storage format optimized for distributed computing and large-scale geospatial data.

### Key Characteristics

```
Zarr Structure (like in test.zarr/):
├── .zgroup              # Zarr group metadata
├── .zmetadata           # Global metadata
├── .zattrs              # Attributes
├── X/                   # Dimension arrays
│   ├── .zarray
│   └── 0                # Chunk file
├── Y/                   # Dimension arrays
├── feature/             # Feature dimension (64D embeddings)
├── time/                # Time dimension
└── __xarray_dataarray_variable__/  # Data values

```

### Why Zarr?

| Aspect | Benefit |
|--------|---------|
| **Cloud Storage** | Directly read/write from GCS without downloading |
| **Chunking** | Data split into chunks → parallel processing |
| **Lazy Loading** | Load only needed data into memory |
| **Compression** | Configurable compression (reduces storage ~70%) |
| **Multi-dimensional** | Perfect for (X, Y, time, features) geospatial data |
| **Integration** | Works seamlessly with xarray, dask, and Apache Beam |

### Example: Reading Zarr with xarray

```python
import xarray as xr

# Open zarr archive (works with local or GCS paths)
ds = xr.open_dataset('gs://earth-embeddings-hungary-output/hungary_embeddings.zarr', 
                     engine='zarr')

# Get embedding for a specific location
embedding = ds['embedding'].sel(X=slice(1000, 1001), Y=slice(2000, 2001))

# Stream processing with dask (no memory overload)
mean_value = ds['embedding'].mean(dim='X').compute()
```

### In This Project

- **`test.zarr/`**: Local test data with embeddings
- **`gs://earth-embeddings-hungary-output/`**: Production data in GCS
- **Chunk size**: 256×256 or 1024×1024 pixels per chunk
- **Data type**: Float32 (4 bytes per value)
- **Dimensions**: (time=1, X=pixels_east, Y=pixels_north, features=64)

---

## Architecture & Data Flow

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                      GEOSPATIAL DATA PIPELINE                    │
└─────────────────────────────────────────────────────────────────┘

INPUT LAYER:
  └─ Earth Engine (AlphaEarth Embeddings)
     └─ 64D embeddings at 10m resolution

PROCESSING LAYER (4 Pipeline Steps):
  ├─ 0_extract.py     → Extract from EE → Zarr (raw)
  ├─ 1_consolidate.py → Consolidate chunks & 1st reduction
  ├─ 2_reduce.py      → Create 5 pyramid levels (mean-pooled)
  └─ 3_load_into_db.py → Load into Milvus + Index

STORAGE LAYER:
  ├─ GCS (zarr archives)
  └─ Milvus Vector DB (indexed embeddings)

SERVING LAYER:
  ├─ FastAPI Backend (api/app.py)
  └─ Next.js Frontend (ui/)
     └─ Polygon query → Similarity search → GeoJSON results
```

### Data Coordinate Systems

**UTM 33N (EPSG:32633)** - Used for extraction & storage
- East/North in meters
- Minimizes distortion over Hungary
- Stored as X (easting), Y (northing) in zarr

**Geographic (EPSG:4326)** - Used for queries & UI
- Latitude/Longitude (WGS84)
- User-friendly for mapping

**Conversion flow:**
```
User Input (lat/lon) → Reproject to UTM 33N → Query Zarr/Milvus 
→ Get X,Y,Z,embedding → Convert back to lat/lon → Return GeoJSON
```

---

## How to Use Embeddings

### 1. **Local Query via API**

```python
import requests
import json

# Define a query polygon (GeoJSON format)
query_polygon = {
    "type": "Polygon",
    "coordinates": [[
        [19.05, 47.50],
        [19.10, 47.50],
        [19.10, 47.55],
        [19.05, 47.55],
        [19.05, 47.50]
    ]]
}

# Send query to API
response = requests.post(
    "http://localhost:8000/neighbours",
    json={
        "geojson": query_polygon,
        "k": 20,                    # Return 20 neighbors
        "nprobe": 32,               # Milvus search precision
        "year": 2024,               # Query 2024 data
        "coordinate_system": "geographic"
    }
)

results = response.json()

# Results contain:
# - query_lat, query_lon: Centroid of your query polygon
# - query_z: Zoom level automatically selected based on area
# - query_embedding: 64D vector of your query area
# - neighbours: GeoJSON FeatureCollection of similar locations
#   ├─ geometry: Point(lon, lat)
#   └─ properties:
#       ├─ distance: Similarity score (higher = more similar)
#       ├─ z: Zoom level of result
#       ├─ year: Year of data
#       └─ embedding: 64D vector
```

### 2. **Direct Vector Database Query**

```python
from pymilvus import Collection, connections

# Connect to Milvus
connections.connect(host="localhost", port=19530)
col = Collection("hungary_embeddings")
col.load()

# Prepare search parameters
search_params = {
    "metric_type": "IP",  # Inner Product (cosine similarity)
    "params": {"nprobe": 32}
}

# Your query embedding (64D)
query_vec = [0.5, -0.3, 0.1, ...]  # 64 values

# Search for similar embeddings
results = col.search(
    data=[query_vec],
    anns_field="embedding",
    param=search_params,
    limit=20,  # Return top 20
    expr="year == 2024 and z == 64",  # Filter by year and zoom level
    output_fields=["lon", "lat", "z", "year", "embedding"]
)

for hit in results[0]:
    print(f"Found similar area at ({hit.entity['lon']}, {hit.entity['lat']})")
    print(f"Similarity distance: {hit.distance}")
```

### 3. **Batch Processing with Zarr**

```python
import xarray as xr
import numpy as np
from scipy.spatial.distance import cosine

# Open zarr archive
ds = xr.open_dataset(
    'gs://earth-embeddings-hungary-output/hungary_embeddings.zarr',
    engine='zarr'
)

# Load all embeddings at zoom level z64
embeddings_z64 = ds['embedding'].sel(z=64).values  # Shape: (N, 64)

# Query embedding
query_emb = np.array([0.5, -0.3, ...])  # 64D

# Compute similarities (vectorized)
similarities = 1 - np.array([
    cosine(query_emb, emb) for emb in embeddings_z64
])

# Get top 20 most similar
top_indices = np.argsort(similarities)[-20:]
top_embeddings = embeddings_z64[top_indices]
```

### 4. **Interactive Web UI**

1. Navigate to `http://localhost:3000` (after running `make up-app-build`)
2. Draw a polygon on the map
3. System automatically:
   - Calculates polygon area
   - Selects appropriate zoom level (z8-z256)
   - Queries the database
   - Returns and displays similar locations
4. Results show as red dots with similarity scores

---

## How to Add Different Embeddings

### Scenario 1: Add Embeddings for a New Geographic Region

**Example: Add UK embeddings alongside Hungary**

#### Step 1: Prepare Input Data

```bash
# Create GeoJSON for your region
cat > uk_boundary.geojson << EOF
{
  "type": "FeatureCollection",
  "features": [{
    "type": "Feature",
    "geometry": {
      "type": "Polygon",
      "coordinates": [[
        [-8, 50],
        [2, 50],
        [2, 56],
        [-8, 56],
        [-8, 50]
      ]]
    }
  }]
}
EOF
```

#### Step 2: Update Configuration

Edit `.env`:
```bash
# For UK, use UTM 30N
TARGET_CRS=EPSG:32630
geojson_path=uk_boundary.geojson
raw_archive=gs://my-bucket/uk_raw
reduced_archive=gs://my-bucket/uk_embeddings
COLLECTION=uk_embeddings
```

#### Step 3: Extract Embeddings

```bash
# Local extraction (for testing)
make extract-local

# Or use Dataflow for large regions
make extract-dataflow
```

#### Step 4: Process Through Pipeline

```bash
make consolidate-dataflow
make reduce-dataflow
```

#### Step 5: Load into New Database Collection

Edit `pipeline/3_load_into_db.py` to create separate collection or update it:

```python
COLLECTION = os.getenv("COLLECTION", "uk_embeddings")  # Now UK
```

Then load:
```bash
python pipeline/3_load_into_db.py
```

### Scenario 2: Add Different Embedding Models

**Example: Use Sentinel-1 SAR embeddings instead of AlphaEarth Optical**

#### Step 1: Modify Extraction Script

Edit `pipeline/0_extract.py`:

```python
# Replace AlphaEarth loading with your model
def load_embeddings_from_earth_engine():
    # Instead of:
    # dataset = ee.ImageCollection("projects/google/research/alpha-earth-foundations/v1/optical")
    
    # Use your model:
    dataset = ee.ImageCollection("COPERNICUS/S1_GRD")
    
    # SAR returns different bands/structure
    # Adapt the extraction accordingly
    return dataset
```

#### Step 2: Update Extraction Parameters

```python
# Change bands to match new model
BANDS = ['VV', 'VH']  # Instead of A00-A63

# Update output shape
EMB_DIM = 2  # Instead of 64
```

#### Step 3: Create New Collection with Updated Schema

```python
# In 3_load_into_db.py
FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=2),  # Changed from 64
```

### Scenario 3: Add Multi-Temporal Embeddings

**Example: Track embeddings for same region across multiple years**

#### Step 1: Extract for Multiple Date Ranges

```bash
# Modify extraction to specify date range
--start_date 2017-01-01 --end_date 2017-12-31
--start_date 2020-01-01 --end_date 2020-12-31
--start_date 2024-01-01 --end_date 2024-12-31
```

#### Step 2: Add Year to Database

Already done in `3_load_into_db.py`:

```python
FieldSchema(name="year", dtype=DataType.INT16),  # Year tracking
```

#### Step 3: Query Specific Year

```python
# In API or direct query
expr = "year == 2024 and z == 64"
```

### Scenario 4: Add Custom Embedding Preprocessing

**Example: Normalize embeddings or apply PCA**

#### Step 1: Create Preprocessing Module

```python
# pipeline/embeddings_preprocessing.py
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA

def normalize_embeddings(embeddings):
    """L2 normalize embeddings"""
    return embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

def apply_pca(embeddings, n_components=32):
    """Reduce from 64D to 32D using PCA"""
    pca = PCA(n_components=n_components)
    return pca.fit_transform(embeddings)
```

#### Step 2: Apply in Load Script

```python
# pipeline/3_load_into_db.py
from embeddings_preprocessing import normalize_embeddings, apply_pca

def reshape(block, z, year):
    df = process_block(block)
    
    # Apply preprocessing
    embeddings = df[embedding_cols].values
    embeddings = normalize_embeddings(embeddings)
    # embeddings = apply_pca(embeddings)  # Optional
    
    return embeddings
```

---

## Detailed Pipeline Flows

### Pipeline Stage 0: Extract

**Purpose**: Pull embeddings from Earth Engine, save to Zarr

```
FLOW:
  Earth Engine (AlphaEarth)
       ↓
  [Load ImageCollection for region]
       ↓
  [Reproject to target UTM zone]
       ↓
  [Create xarray dataset]
       ↓
  [Split into chunks (2048×2048 pixels)]
       ↓
  [Apache Beam: Distributed extraction]
       ├─ Worker 1: Fetch chunks 0-10
       ├─ Worker 2: Fetch chunks 11-20
       └─ Worker N: Fetch chunks N*10-(N+1)*10
       ↓
  [Save chunks to Zarr on GCS]
       ↓
  Output: gs://bucket/raw_embeddings.zarr
          Structure: (time=1, X=N, Y=M, features=64)
```

**Key Parameters**:
- `--scale`: Output resolution (default 10m per pixel)
- `--ee_max_num_workers`: EE API concurrent requests (max 20)
- `--chunks`: Internal chunk size (2048×2048 for good balance)

**Configuration in .env**:
```bash
geojson_path=budapest.geojson           # Area of interest
raw_archive=gs://.../budapest_raw       # Output path
scale=10                                # 10m pixels
ee_max_num_workers=5                    # Conservative (EE quota limited)
```

**Troubleshooting**:
- ❌ "Quota exceeded": Reduce `ee_max_num_workers`
- ❌ "Out of memory": Reduce worker count or increase machine size
- ❌ Slow extraction: Increase `ee_max_num_workers` (if quota allows)

---

### Pipeline Stage 1: Consolidate

**Purpose**: Reorganize chunks for efficient access, apply first mean-pooling

```
FLOW:
  Input Zarr (raw)
       ↓
  [Load 256×256 chunks]
       ↓
  [Consolidate to contiguous chunk boundaries]
       ├─ Original: Random-access chunks
       └─ Consolidated: Aligned boundaries
       ↓
  [Apply BlockMean reduction]
       ├─ Mean-pool from (X, Y) = (256, 256) 
       └─ → (128, 128) [2x reduction]
       ↓
  [Save intermediate Zarr]
       ↓
  Output: gs://bucket/budapest_intermediate.zarr
          Structure: (time=1, X=N/2, Y=M/2, features=64)
          Chunk size: 64×64 per chunk
```

**Key Operations**:
```python
# BlockMean PTransform:
coarsen(X=2, Y=2).mean(skipna=True)
# Takes 2×2 pixel blocks and averages them
```

**Why This Step?**
- Aligns chunks for efficient access
- Primes data for remaining reductions
- Reduces data size by 4x (2×2 pooling)

---

### Pipeline Stage 2: Reduce

**Purpose**: Create 5 additional pyramid levels (zoom levels 16, 32, 64, 128, 256)

```
FLOW:
  Input: Intermediate Zarr (z=80)
       ↓
  [Load chunks]
       ↓
  [Iterative mean-pooling to create pyramid]
       │
       ├─→ BlockMean(2, 2) → z=16 (160m per pixel)
       │       ↓ Save
       │
       ├─→ BlockMean(4, 4) → z=32 (320m per pixel)
       │       ↓ Save
       │
       ├─→ BlockMean(8, 8) → z=64 (640m per pixel)
       │       ↓ Save
       │
       ├─→ BlockMean(16, 16) → z=128 (1280m per pixel)
       │       ↓ Save
       │
       └─→ BlockMean(32, 32) → z=256 (2560m per pixel)
               ↓ Save
       ↓
  Output: All 6 zoom levels in single Zarr
          gs://bucket/reduced_embeddings.zarr
          Structure: (time=1, X, Y, features=64, z=[16,32,64,128,256])
```

**Pyramid Logic**:
```
Original: 10m × 10m pixel
z16:      160m × 160m pixel (16 × original)
z32:      320m × 320m pixel (32 × original)
z64:      640m × 640m pixel (64 × original)
z128:     1280m × 1280m pixel (128 × original)
z256:     2560m × 2560m pixel (256 × original)
```

**Query Selection**:
```python
def which_z(query_area):
    """
    If query is ~1600 sqm → use z16
    If query is ~25600 sqm → use z32
    If query is ~100k sqm → use z64
    """
    AREA_THRESHOLDS = {
        1600: 16,
        25600: 32,
        102400: 64,
        409600: 128,
        1638400: 256,
    }
    for area_threshold in sorted(AREA_THRESHOLDS.keys()):
        if query_area <= area_threshold:
            return AREA_THRESHOLDS[area_threshold]
    return 256  # Largest
```

---

### Pipeline Stage 3: Load into Database

**Purpose**: Ingest embeddings into Milvus, create indices for fast search

```
FLOW:
  Input: Reduced Zarr (all zoom levels)
       ↓
  [Connect to Milvus]
       ↓
  [Create collection with schema]
       ├─ id (int64, auto-generated)
       ├─ lat (float)
       ├─ lon (float)
       ├─ z (int16) - zoom level
       ├─ year (int16) - temporal tracking
       └─ embedding (float_vector, dim=64)
       ↓
  [Read Zarr in batches (32KB chunks)]
       │
       ├─→ Batch 1: 32K embeddings
       ├─→ Batch 2: 32K embeddings
       └─→ Batch N: Remaining embeddings
       ↓
  [For each batch]:
       ├─ Reshape from (X, Y, features) → (N_records, features)
       ├─ Extract coordinates (lat, lon) → geographic (WGS84)
       ├─ Fill NaN with 0
       └─ Insert into Milvus
       ↓
  [Create IVF_FLAT index]
       ├─ Metric: Inner Product (IP) [cosine similarity]
       ├─ n_list: 4096 [index partitions]
       └─ Build index on "embedding" field
       ↓
  [Load collection into memory]
       ↓
  Database ready for queries!
```

**Database Schema**:
```python
CollectionSchema([
    FieldSchema("id", DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema("lat", DataType.FLOAT),
    FieldSchema("lon", DataType.FLOAT),
    FieldSchema("z", DataType.INT16),
    FieldSchema("year", DataType.INT16),
    FieldSchema("embedding", DataType.FLOAT_VECTOR, dim=64),
], description="Geospatial embeddings indexed by lat/lon/year/zoom")
```

**Index Configuration**:
- **Type**: IVF_FLAT (Inverted File with Flat Quantizer)
- **Metric**: Inner Product (IP) = cosine similarity after normalization
- **n_list**: 4096 buckets (good balance for millions of vectors)
- **Search params**: `nprobe=32` (check 32 buckets per search)

**Performance Characteristics**:
- **Insert**: ~1M vectors/min on single machine
- **Search**: <100ms for top-20 similar in 10M vectors with nprobe=32
- **Memory**: ~500MB for 1M vectors (64D float32 + indices)

---

### Query Flow: Frontend to Backend

```
USER INTERACTION:
  [Draw polygon on map]
       ↓
FRONTEND (Next.js):
  [Polygon GeoJSON]
       ↓
  POST /neighbours {
    "geojson": {...},
    "k": 20,
    "year": 2024,
    "coordinate_system": "geographic"
  }
       ↓
BACKEND (FastAPI):
  [1. Parse & validate GeoJSON]
  [2. Calculate polygon area]
  [3. Select zoom level]
       ├─ area=1500 sqm → z=16
       └─ area=100k sqm → z=64
       ↓
  [4. Reproject polygon to UTM 33N]
  [5. Calculate centroid]
       ↓
  [6. Query Milvus for embedding at centroid]
       ├─ Find closest valid embedding
       └─ Return 64D vector
       ↓
  [7. Search for similar embeddings]
       ├─ Filter: year==2024 AND z==query_z
       ├─ Limit: k=20
       └─ Metric: Inner Product
       ↓
  [8. Post-process results]
       ├─ Skip zero embeddings
       ├─ Reproject to geographic (WGS84)
       └─ Format as GeoJSON
       ↓
  RESPONSE: {
    "query_lat": 47.5,
    "query_lon": 19.05,
    "query_z": 64,
    "query_embedding": [0.5, -0.3, ...],  // 64D
    "neighbours": {
      "type": "FeatureCollection",
      "features": [
        {
          "type": "Feature",
          "geometry": {"type": "Point", "coordinates": [19.1, 47.6]},
          "properties": {
            "z": 64,
            "year": 2024,
            "distance": 0.95,  // Similarity
            "embedding": [0.51, -0.31, ...]
          }
        },
        // ... more features
      ]
    }
  }
       ↓
FRONTEND:
  [Display results as red dots on map]
  [Show similarity scores]
```

---

## Local Development Setup

### Prerequisites

```bash
# Python 3.12+
python --version

# uv (Python package manager)
pip install uv

# Docker & Docker Compose
docker --version
docker-compose --version

# GCP CLI (if using GCP)
gcloud --version
```

### Installation

```bash
# 1. Clone repository
git clone <repo-url>
cd earth-embedding-sandbox-hun

# 2. Create Python environment
uv venv --python 3.13

# 3. Activate environment
source .venv/bin/activate  # On Linux/Mac
# or: .venv\Scripts\activate  # On Windows

# 4. Install dependencies
uv pip install -e .[dev]

# 5. Set up pre-commit hooks
uv run pre-commit install

# 6. Configure environment
cp .env-template .env
# Edit .env with your GCP credentials and settings
```

### Docker Setup (Minimal)

```bash
# Spin up Milvus and Redis for development
make up-build-dev

# Verify services
docker ps
# Should show: milvus, redis containers

# Spin down when done
make down-v-dev
```

### Running Tests

```bash
# Run all tests
pytest -xs

# Run specific test file
pytest tests/test_api.py -xs

# Run with coverage
pytest --cov=api --cov=pipeline tests/
```

---

## Running Pipelines

### Local Extraction (Testing)

```bash
# Edit .env for your test region
geojson_path=budapest.geojson
raw_archive=gs://my-bucket/test_raw

# Run extraction locally (slow but no GCP costs)
make extract-local

# Typical duration: 30 min - 2 hours (depends on region size)
```

### Full GCP Dataflow Pipeline

```bash
# 1. Authenticate with GCP
gcloud auth activate-service-account --key-file="$GOOGLE_APPLICATION_CREDENTIALS"
gcloud auth configure-docker

# 2. Build and push Docker container
make docker-build
make docker-push

# 3. Extract (Dataflow)
make extract-dataflow
# → Job creates in GCP Console under Dataflow

# 4. Monitor in GCP Console
open "https://console.cloud.google.com/dataflow"

# 5. Wait for completion, then consolidate
make consolidate-dataflow

# 6. Reduce
make reduce-dataflow

# 7. Load into database
MILVUS_HOST=your-milvus-host python pipeline/3_load_into_db.py
```

### Multi-Region Setup

```bash
# Extract UK data
TARGET_CRS=EPSG:32630 \
geojson_path=uk_boundary.geojson \
raw_archive=gs://bucket/uk_raw \
make extract-dataflow

# Extract Hungary data (parallel)
TARGET_CRS=EPSG:32633 \
geojson_path=budapest.geojson \
raw_archive=gs://bucket/hun_raw \
make extract-dataflow

# Both jobs run simultaneously in Dataflow
```

### Batch Processing

```bash
# Extract multiple regions with band-wise extraction
make extract-dataflow-bandwise

# Useful for:
# - Splitting 64 bands across multiple jobs (avoids CPU bottleneck)
# - Extracting in parallel for different geographic regions
# - Recovering from partial failures
```

---

## Appendix: Key Concepts

### Milvus Vector Database

- **Open-source** vector database for similarity search
- **Indexing**: IVF_FLAT, HNSW, etc.
- **Metrics**: Inner Product (IP), Euclidean (L2), Cosine
- **Partition**: Supports partitioning by field (e.g., by year)
- **Python SDK**: `pymilvus` package

### Apache Beam & Dataflow

- **Beam**: Framework for batch/streaming data pipelines
- **Dataflow**: GCP's managed Beam execution service
- **Runners**: DirectRunner (local), DataflowRunner (GCP)
- **Advantages**: Auto-scaling, fault tolerance, distributed processing

### xarray & Zarr

- **xarray**: Pandas for multi-dimensional arrays
- **Zarr**: Cloud-native array storage (chunked, compressed)
- **xarray-beam**: Xarray operations on Beam pipelines
- **Use Case**: Perfect for geospatial data (lat, lon, time, bands)

### Embedding Similarity Metrics

| Metric | Formula | Use Case | Range |
|--------|---------|----------|-------|
| **Cosine** | 1 - (a·b)/(∥a∥∥b∥) | Directional similarity | [0, 2] |
| **Euclidean (L2)** | √(Σ(a_i - b_i)²) | Distance-based | [0, ∞] |
| **Inner Product (IP)** | a·b | Fast on GPU, after normalization ≈ cosine | [-1, 1] |

---

**End of Guide**

For questions or contributions, refer to the main [README.md](README.md).
