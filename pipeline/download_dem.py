"""Step 2: Download GSI DEM10B PNG tiles and decode to GeoTIFFs.

For each mountain in mountains.json, downloads GSI DEM10B PNG tiles covering
a configurable radius, decodes RGB values to elevation, and merges into a
single GeoTIFF suitable for gdal_viewshed.

Reference: https://maps.gsi.go.jp/development/demtile.html
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from pipeline.utils.dem_decode import decode_dem_png
from pipeline.utils.geojson import features_to_dicts
from pipeline.utils.tiles import bounding_tiles, tile2deg

# GSI DEM PNG tile URL template
# Note: dem_png (not dem) is the correct path for PNG format tiles
GSI_DEM_PNG_URL = "https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png"

TILE_SIZE = 256
ZOOM = 14

# Suppress GDAL error messages for missing tiles
gdal.UseExceptions()


def download_tile(x: int, y: int, z: int, cache_dir: Path, delay: float = 0.5) -> Path | None:
    """Download a single DEM PNG tile, using cache if available.

    Returns the cached file path, or None if the tile doesn't exist (404).
    """
    import requests

    cache_path = cache_dir / str(z) / str(x) / f"{y}.png"
    if cache_path.exists():
        return cache_path

    url = GSI_DEM_PNG_URL.format(z=z, x=x, y=y)

    try:
        resp = requests.get(
            url,
            timeout=30,
            headers={
                "User-Agent": "FujisanViewshed/1.0 (https://github.com/fujisan-viewshed)"
            },
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  Warning: Failed to download tile {z}/{x}/{y}: {e}")
        return None

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.content)

    time.sleep(delay)  # Rate-limit: this is the main bottleneck, not GDAL
    return cache_path


def tile_to_geotiff(
    png_path: Path, x: int, y: int, z: int, output_path: Path
) -> bool:
    """Decode a DEM PNG tile and write as a georeferenced GeoTIFF.

    Returns True if successful, False if the tile is all nodata.
    """
    ds_png = gdal.Open(str(png_path))
    rgb_array = np.stack([ds_png.GetRasterBand(i).ReadAsArray() for i in (1, 2, 3)], axis=-1)
    ds_png = None
    # Custom RGB→elevation decoding; NumPy vectorised, no GDAL equivalent
    elevation = decode_dem_png(rgb_array)

    if np.all(np.isnan(elevation)):
        return False

    # Calculate geographic bounds
    nw_lat, nw_lon = tile2deg(x, y, z)
    se_lat, se_lon = tile2deg(x + 1, y + 1, z)

    pixel_width = (se_lon - nw_lon) / TILE_SIZE
    pixel_height = (nw_lat - se_lat) / TILE_SIZE  # positive value

    # GeoTransform: (top_left_x, pixel_width, 0, top_left_y, 0, -pixel_height)
    geotransform = (nw_lon, pixel_width, 0, nw_lat, 0, -pixel_height)

    driver = gdal.GetDriverByName("GTiff")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ds = driver.Create(str(output_path), TILE_SIZE, TILE_SIZE, 1, gdal.GDT_Float32)
    ds.SetGeoTransform(geotransform)

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())

    band = ds.GetRasterBand(1)
    band.SetNoDataValue(-9999.0)

    # Replace NaN with nodata value
    elevation_out = np.where(np.isnan(elevation), -9999.0, elevation)
    band.WriteArray(elevation_out)
    band.FlushCache()

    ds = None  # Close the dataset
    return True


def process_mountain(
    mountain: dict, radius_km: float, cache_dir: Path, output_dir: Path, delay: float,
    workers: int = 4,
) -> Path | None:
    """Download and merge DEM tiles for a single mountain.

    Returns the path to the merged GeoTIFF, or None on failure.
    """
    name = mountain["name"]
    mid = mountain["id"]
    lat = mountain["lat"]
    lon = mountain["lon"]

    print(f"\nProcessing {name} ({mid}) at [{lat:.4f}, {lon:.4f}]...")

    x_min, x_max, y_min, y_max = bounding_tiles(lat, lon, radius_km, ZOOM)
    total_tiles = (x_max - x_min + 1) * (y_max - y_min + 1)
    print(f"  Tile range: x=[{x_min},{x_max}], y=[{y_min},{y_max}] ({total_tiles} tiles)")

    # Download tiles in parallel (I/O-bound), then convert to GeoTIFF in main thread (GDAL not thread-safe)
    tile_coords = [
        (tx, ty) for tx in range(x_min, x_max + 1) for ty in range(y_min, y_max + 1)
    ]

    # Phase 1: parallel download
    downloaded_tiles: list[tuple[int, int, Path | None]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(download_tile, tx, ty, ZOOM, cache_dir, delay): (tx, ty)
            for tx, ty in tile_coords
        }
        done_count = 0
        for future in as_completed(futures):
            tx, ty = futures[future]
            png_path = future.result()
            downloaded_tiles.append((tx, ty, png_path))
            done_count += 1
            if done_count % 50 == 0:
                print(f"  Downloaded {done_count}/{total_tiles} tiles...")

    # Phase 2: sequential GeoTIFF conversion (GDAL is not thread-safe)
    tile_tiff_dir = output_dir / "tile_tiffs" / mid
    tile_tiff_paths = []
    downloaded = 0

    for tx, ty, png_path in downloaded_tiles:
        if png_path is None:
            continue
        downloaded += 1
        tiff_path = tile_tiff_dir / f"{tx}_{ty}.tif"
        if tiff_path.exists() or tile_to_geotiff(png_path, tx, ty, ZOOM, tiff_path):
            tile_tiff_paths.append(str(tiff_path))

    print(f"  Downloaded {downloaded} tiles, {len(tile_tiff_paths)} valid GeoTIFFs")

    if not tile_tiff_paths:
        print(f"  Warning: No valid tiles for {name}")
        return None

    # Build VRT from tile GeoTIFFs
    # Python bindings call the same C++ code as gdalbuildvrt CLI — no speed difference
    vrt_path = output_dir / f"{mid}.vrt"
    vrt_ds = gdal.BuildVRT(str(vrt_path), tile_tiff_paths)
    if vrt_ds is None:
        print(f"  Error: Failed to build VRT for {name}")
        return None
    vrt_ds = None  # Close to flush

    # Convert VRT to a single merged GeoTIFF (same perf as gdal_translate CLI)
    merged_path = output_dir / "geotiff" / f"{mid}_dem.tif"
    merged_path.parent.mkdir(parents=True, exist_ok=True)
    gdal.Translate(
        str(merged_path),
        str(vrt_path),
        format="GTiff",
        creationOptions=["COMPRESS=DEFLATE", "TILED=YES"],
    )

    print(f"  Merged GeoTIFF: {merged_path}")
    return merged_path


def main():
    # Step 2 of 3: Download GSI DEM10B PNG tiles for each mountain,
    # decode RGB→elevation, and merge into a single GeoTIFF per mountain.
    # Input:  data/mountains.geojson (from pipeline.fetch_mountains)
    # Output: data/dem/geotiff/{id}_dem.tif
    # Next step: pipeline.viewshed (runs viewshed analysis on each DEM)
    parser = argparse.ArgumentParser(description="Download GSI DEM tiles and create GeoTIFFs")
    parser.add_argument(
        "--input",
        type=str,
        default="data/mountains.geojson",
        help="Input mountains GeoJSON file",
    )
    parser.add_argument(
        "--radius-km",
        type=float,
        default=20.0,
        help="Radius in km around each mountain (default: 20)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between tile downloads in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/dem",
        help="Output directory for DEM data",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default="data/dem/tiles",
        help="Cache directory for raw PNG tiles",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel download threads per mountain (default: 4)",
    )
    args = parser.parse_args()

    mountains_path = Path(args.input)
    if not mountains_path.exists():
        print(f"Error: {mountains_path} not found. Run fetch_mountains first.")
        sys.exit(1)

    geojson = json.loads(mountains_path.read_text(encoding="utf-8"))
    mountains = features_to_dicts(geojson["features"])
    print(f"Loaded {len(mountains)} mountains from {mountains_path}")

    output_dir = Path(args.output_dir)
    cache_dir = Path(args.cache_dir)

    results = []
    for mountain in mountains:
        merged = process_mountain(mountain, args.radius_km, cache_dir, output_dir, args.delay, args.workers)
        if merged:
            results.append({"id": mountain["id"], "name": mountain["name"], "dem_path": str(merged)})

    print(f"\nDone. Created {len(results)}/{len(mountains)} GeoTIFFs.")
    for r in results:
        print(f"  - {r['name']}: {r['dem_path']}")


if __name__ == "__main__":
    main()
