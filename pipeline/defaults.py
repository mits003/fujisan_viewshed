"""Configurable defaults for the fujisan_viewshed pipeline.

These values can be overridden via CLI arguments. Centralised here
so that all pipeline steps share the same defaults.
"""

# Paths
MOUNTAINS_GEOJSON = "data/mountains.geojson"
DEM_OUTPUT_DIR = "data/dem"
CACHE_DIR = "data/dem/tiles"
TILE_INDEX_DB = "data/dem/tiles.duckdb"
GEOJSON_DIR = "data/geojson"
PMTILES_OUTPUT = "data/fuji_viewshed.pmtiles"

# Download
DOWNLOAD_DELAY = 0.5  # seconds between requests
MAX_RETRIES = 5
REQUEST_TIMEOUT = 30  # seconds
DEFAULT_WORKERS = 4

# S3
S3_PREFIX = "dem_tiff"
S3_REGION = "ap-northeast-1"

# Search radius
RADIUS_KM = 20.0

# PMTiles
PMTILES_MIN_ZOOM = 4
PMTILES_MAX_ZOOM = 12
