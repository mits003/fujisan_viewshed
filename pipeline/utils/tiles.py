"""Slippy map tile coordinate utilities.

Reference: https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames
"""

import math


def deg2tile(lat_deg: float, lon_deg: float, zoom: int) -> tuple[int, int]:
    """Convert lat/lon to slippy map tile numbers (x, y)."""
    lat_rad = math.radians(lat_deg)
    n = 1 << zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


def tile2deg(xtile: int, ytile: int, zoom: int) -> tuple[float, float]:
    """Convert tile numbers to NW corner lat/lon."""
    n = 1 << zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


def bounding_tiles(
    lat: float, lon: float, radius_km: float, zoom: int = 14
) -> tuple[int, int, int, int]:
    """Return (x_min, x_max, y_min, y_max) tile range for a radius around a point.

    Uses approximate degree offsets from the center point.
    """
    # 1 degree latitude ~ 111.32 km
    lat_offset = radius_km / 111.32
    # 1 degree longitude varies with latitude
    lon_offset = radius_km / (111.32 * math.cos(math.radians(lat)))

    lat_min = lat - lat_offset
    lat_max = lat + lat_offset
    lon_min = lon - lon_offset
    lon_max = lon + lon_offset

    x_min, _ = deg2tile(lat_max, lon_min, zoom)  # NW corner
    x_max, _ = deg2tile(lat_min, lon_max, zoom)  # SE corner (x)
    _, y_min = deg2tile(lat_max, lon_min, zoom)  # NW corner (y)
    _, y_max = deg2tile(lat_min, lon_max, zoom)  # SE corner

    return x_min, x_max, y_min, y_max
