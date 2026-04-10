"""Step 2: Download GSI DEM10B PNG tiles and decode to GeoTIFFs.

For each mountain in mountains.json, downloads GSI DEM10B PNG tiles covering
a configurable radius, decodes RGB values to elevation, and merges into a
single GeoTIFF suitable for gdal_viewshed.

Supports two modes:
  - Legacy (default): per-mountain download, convert, merge
  - Tile index mode (--tile-index): streaming download → convert → upload to S3,
    using a pre-built DuckDB tile index for deduplication

Reference: https://maps.gsi.go.jp/development/demtile.html
"""

import argparse
import json
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from osgeo import gdal, osr
from tqdm import tqdm

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


# ---------------------------------------------------------------------------
# Legacy mode: per-mountain download + merge (unchanged)
# ---------------------------------------------------------------------------

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
        with tqdm(total=total_tiles, desc=f"  Downloading [{mid}]", unit="tile") as pbar:
            for future in as_completed(futures):
                tx, ty = futures[future]
                png_path = future.result()
                downloaded_tiles.append((tx, ty, png_path))
                pbar.update(1)

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

    print(f"  {downloaded} tiles downloaded, {len(tile_tiff_paths)} valid GeoTIFFs")

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


# ---------------------------------------------------------------------------
# Tile index mode: streaming download → convert → upload to S3
# ---------------------------------------------------------------------------

def _process_tile_streaming(
    z: int, x: int, y: int,
    cache_dir: Path, delay: float,
    s3_client, s3_bucket: str, s3_prefix: str,
) -> str | None:
    """Download, convert, upload a single tile. Returns s3_key or None.

    Runs in main thread (GDAL not thread-safe for tile_to_geotiff).
    Download is done inline since we process one tile at a time for streaming.
    """
    from pipeline.utils.s3_tiles import tile_s3_key, upload_tile

    s3_key = tile_s3_key(s3_prefix, z, x, y)

    # Download PNG
    png_path = download_tile(x, y, z, cache_dir, delay)
    if png_path is None:
        return None

    # Convert to GeoTIFF in temp location
    with tempfile.TemporaryDirectory() as tmpdir:
        tiff_path = Path(tmpdir) / f"{x}_{y}.tif"
        if not tile_to_geotiff(png_path, x, y, z, tiff_path):
            return None

        # Validate GeoTIFF before uploading
        try:
            ds = gdal.Open(str(tiff_path))
            if ds is None:
                print(f"  Warning: Invalid GeoTIFF for tile {z}/{x}/{y}, skipping")
                return None
            ds = None
        except RuntimeError:
            print(f"  Warning: Unreadable GeoTIFF for tile {z}/{x}/{y}, skipping")
            return None

        # Upload to S3
        if not upload_tile(s3_client, tiff_path, s3_bucket, s3_key):
            return None

    # Delete cached PNG to save space (S3 is the archive)
    png_path.unlink(missing_ok=True)

    return s3_key


def process_tiles_streaming(
    db_path: Path, cache_dir: Path, delay: float,
    s3_bucket: str, s3_prefix: str,
    batch_size: int = 100,
) -> None:
    """Stream-process all pending tiles: download → convert → upload → update status.

    Uses DuckDB tile index for progress tracking. Processes tiles sequentially
    (GDAL not thread-safe) but downloads benefit from PNG cache.
    """
    import duckdb

    from pipeline.utils.s3_tiles import create_client, list_existing_tiles, tile_s3_key

    con = duckdb.connect(str(db_path))
    s3_client = create_client()

    # On cold start, reconcile DuckDB with S3 for any 'in_progress' tiles
    in_progress = con.execute(
        "SELECT zoom, x, y FROM tiles WHERE status = 'in_progress'"
    ).fetchall()
    if in_progress:
        print(f"Recovering {len(in_progress)} in-progress tiles...")
        existing = list_existing_tiles(s3_client, s3_bucket, s3_prefix)
        for z, x, y in in_progress:
            key = tile_s3_key(s3_prefix, z, x, y)
            new_status = "done" if key in existing else "pending"
            con.execute(
                "UPDATE tiles SET status = ?, updated_at = current_timestamp "
                "WHERE zoom = ? AND x = ? AND y = ?",
                [new_status, z, x, y],
            )

    # Get all pending tiles
    pending = con.execute(
        "SELECT zoom, x, y FROM tiles WHERE status = 'pending'"
    ).fetchall()
    total = len(pending)
    if total == 0:
        print("All tiles already processed.")
        con.close()
        return

    print(f"Processing {total} pending tiles (streaming to s3://{s3_bucket}/{s3_prefix})...")

    done_batch: list[tuple[int, int, int, str]] = []
    error_batch: list[tuple[int, int, int]] = []

    with tqdm(total=total, desc="Tiles", unit="tile") as pbar:
        for z, x, y in pending:
            # Mark in_progress
            con.execute(
                "UPDATE tiles SET status = 'in_progress', updated_at = current_timestamp "
                "WHERE zoom = ? AND x = ? AND y = ?",
                [z, x, y],
            )

            s3_key = _process_tile_streaming(
                z, x, y, cache_dir, delay, s3_client, s3_bucket, s3_prefix,
            )

            if s3_key:
                done_batch.append((z, x, y, s3_key))
            else:
                error_batch.append((z, x, y))

            pbar.update(1)

            # Batch update DuckDB every batch_size tiles
            if len(done_batch) + len(error_batch) >= batch_size:
                _flush_status(con, done_batch, error_batch)
                done_batch.clear()
                error_batch.clear()

    # Final flush
    _flush_status(con, done_batch, error_batch)

    done_total = con.execute("SELECT COUNT(*) FROM tiles WHERE status = 'done'").fetchone()[0]
    error_total = con.execute("SELECT COUNT(*) FROM tiles WHERE status = 'error'").fetchone()[0]
    print(f"\nDone. {done_total} tiles uploaded, {error_total} errors.")
    con.close()


def _flush_status(
    con, done: list[tuple[int, int, int, str]], errors: list[tuple[int, int, int]],
) -> None:
    """Batch update tile statuses in DuckDB."""
    for z, x, y, s3_key in done:
        con.execute(
            "UPDATE tiles SET status = 'done', s3_key = ?, updated_at = current_timestamp "
            "WHERE zoom = ? AND x = ? AND y = ?",
            [s3_key, z, x, y],
        )
    for z, x, y in errors:
        con.execute(
            "UPDATE tiles SET status = 'error', updated_at = current_timestamp "
            "WHERE zoom = ? AND x = ? AND y = ?",
            [z, x, y],
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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
    # Tile index mode arguments
    parser.add_argument(
        "--tile-index",
        type=str,
        default=None,
        help="Path to DuckDB tile index (enables streaming mode)",
    )
    parser.add_argument(
        "--s3-bucket",
        type=str,
        default=None,
        help="S3 bucket for tile storage (required with --tile-index)",
    )
    parser.add_argument(
        "--s3-prefix",
        type=str,
        default="dem_tiff",
        help="S3 key prefix for tile GeoTIFFs (default: dem_tiff)",
    )
    args = parser.parse_args()

    # Tile index mode: streaming download → S3
    if args.tile_index:
        if not args.s3_bucket:
            print("Error: --s3-bucket is required when using --tile-index")
            sys.exit(1)

        db_path = Path(args.tile_index)
        if not db_path.exists():
            print(f"Error: {db_path} not found. Run build_tile_index first.")
            sys.exit(1)

        cache_dir = Path(args.cache_dir)
        process_tiles_streaming(
            db_path, cache_dir, args.delay,
            args.s3_bucket, args.s3_prefix,
        )
        return

    # Legacy mode: per-mountain download + merge
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
    for mountain in tqdm(mountains, desc="DEM download", unit="mountain"):
        merged = process_mountain(mountain, args.radius_km, cache_dir, output_dir, args.delay, args.workers)
        if merged:
            results.append({"id": mountain["id"], "name": mountain["name"], "dem_path": str(merged)})

    print(f"\nDone. Created {len(results)}/{len(mountains)} GeoTIFFs.")
    for r in results:
        print(f"  - {r['name']}: {r['dem_path']}")


if __name__ == "__main__":
    main()
