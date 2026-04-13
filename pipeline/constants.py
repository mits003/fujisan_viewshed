"""Domain-fixed constants for the fujisan_viewshed pipeline.

These values are determined by external data sources or physics
and should not change between runs.
"""

# GSI DEM10B tile system
ZOOM = 14
TILE_SIZE = 256
GSI_DEM_PNG_URL = "https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png"

# Viewshed analysis parameters (from design doc)
OBSERVER_HEIGHT = 2.0  # meters above mountain peak
TARGET_HEIGHT = 1.6  # average human eye level
MAX_DISTANCE = 100_000  # 100km in meters
CURV_COEFF = 0.85714  # standard curvature/refraction coefficient
