"""GSI DEM PNG tile RGB-to-elevation decoder.

Reference: https://maps.gsi.go.jp/development/demtile.html

Encoding formula:
    x = 2^16 * R + 2^8 * G + B
    if x < 2^23:  elevation = x * 0.01  (meters)
    if x == 2^23: nodata (R=128, G=0, B=0)
    if x > 2^23:  elevation = (x - 2^24) * 0.01  (negative elevation)
"""

import numpy as np


def decode_dem_png(rgb_array: np.ndarray) -> np.ndarray:
    """Decode GSI DEM PNG RGB array (H, W, 3) to elevation in meters (H, W).

    Returns float32 array with np.nan for nodata pixels.
    """
    r = rgb_array[:, :, 0].astype(np.int32)
    g = rgb_array[:, :, 1].astype(np.int32)
    b = rgb_array[:, :, 2].astype(np.int32)

    x = (r << 16) | (g << 8) | b

    nodata_mask = x == (1 << 23)

    elevation = np.where(
        x < (1 << 23),
        x * 0.01,
        (x - (1 << 24)) * 0.01,
    ).astype(np.float32)

    elevation[nodata_mask] = np.nan
    return elevation
