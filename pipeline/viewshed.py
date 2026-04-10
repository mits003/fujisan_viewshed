"""Step 3: Run gdal_viewshed and gdal_polygonize for each mountain.

For each mountain in mountains.json, runs gdal_viewshed on the DEM GeoTIFF
to produce a binary visibility raster, then polygonizes the visible areas
to GeoJSON.

Supports two modes:
  - Legacy (default): reads pre-merged DEM GeoTIFFs from local disk
  - Tile index mode (--tile-index): builds VRT from S3-hosted tiles,
    materializes locally via gdal.Translate, runs viewshed, cleans up

Viewshed parameters (from design doc):
- Observer placed at mountain peak (oz=2.0m above peak)
- Target is human observer on ground (tz=1.6m eye level)
- Max distance 100km
- Standard curvature/refraction coefficient 0.85714
"""

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from osgeo import gdal, ogr, osr
from pipeline.utils.geojson import features_to_dicts

gdal.UseExceptions()

# Viewshed parameters from design doc
OBSERVER_HEIGHT = 2.0  # meters above mountain peak
TARGET_HEIGHT = 1.6  # average human eye level
MAX_DISTANCE = 100000  # 100km in meters
CURV_COEFF = 0.85714  # standard curvature/refraction

ZOOM = 14


def run_viewshed(dem_path: Path, output_path: Path, lon: float, lat: float) -> bool:
    """Run gdal_viewshed on a DEM GeoTIFF.

    Returns True on success.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "gdal_viewshed",
        "-ox", str(lon),
        "-oy", str(lat),
        "-oz", str(OBSERVER_HEIGHT),
        "-tz", str(TARGET_HEIGHT),
        "-md", str(MAX_DISTANCE),
        "-cc", str(CURV_COEFF),
        "-om", "NORMAL",
        "-of", "GTiff",
        str(dem_path),
        str(output_path),
    ]

    print(f"  Running gdal_viewshed...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Error: gdal_viewshed failed:\n{result.stderr}")
        return False
    return True


def polygonize_viewshed(
    viewshed_path: Path, geojson_path: Path, mountain: dict
) -> bool:
    """Polygonize a viewshed raster to GeoJSON, keeping only visible areas (value=255).

    Adds mountain metadata as properties on each polygon feature.
    """
    import numpy as np

    geojson_path.parent.mkdir(parents=True, exist_ok=True)

    src_ds = gdal.Open(str(viewshed_path))
    band = src_ds.GetRasterBand(1)

    # Create a binary raster: 1=visible (was 255), 0=not visible
    # gdal_viewshed NORMAL mode: 0=not visible, 255=visible
    arr = band.ReadAsArray()
    binary_arr = (arr == 255).astype(np.uint8)

    src_mem = gdal.GetDriverByName("MEM").Create(
        "", src_ds.RasterXSize, src_ds.RasterYSize, 1, gdal.GDT_Byte
    )
    src_mem.SetGeoTransform(src_ds.GetGeoTransform())
    src_mem.SetProjection(src_ds.GetProjection())
    src_mem.GetRasterBand(1).WriteArray(binary_arr)

    # Create output GeoJSON
    drv = ogr.GetDriverByName("GeoJSON")
    if geojson_path.exists():
        geojson_path.unlink()
    dst_ds = drv.CreateDataSource(str(geojson_path))

    srs = osr.SpatialReference()
    srs.ImportFromWkt(src_ds.GetProjection())

    layer = dst_ds.CreateLayer("viewshed", srs=srs, geom_type=ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("visible", ogr.OFTInteger))
    layer.CreateField(ogr.FieldDefn("mountain_id", ogr.OFTString))
    layer.CreateField(ogr.FieldDefn("mountain_name", ogr.OFTString))
    layer.CreateField(ogr.FieldDefn("fuji_alias", ogr.OFTString))

    print(f"  Running polygonize...")
    # Use the binary raster as mask so only visible (value=1) areas are polygonized
    gdal.Polygonize(src_mem.GetRasterBand(1), src_mem.GetRasterBand(1), layer, 0, [])

    dst_ds = None
    src_mem = None
    src_ds = None

    # Re-open to add mountain metadata and count features
    geojson_data = json.loads(geojson_path.read_text(encoding="utf-8"))
    for feature in geojson_data.get("features", []):
        feature["properties"]["mountain_id"] = mountain["id"]
        feature["properties"]["mountain_name"] = mountain["name"]
        feature["properties"]["fuji_alias"] = mountain["fuji_alias"]

    feat_count = len(geojson_data.get("features", []))
    geojson_path.write_text(json.dumps(geojson_data, ensure_ascii=False), encoding="utf-8")

    print(f"  Polygonized: {feat_count} visible features")
    return True


# ---------------------------------------------------------------------------
# Legacy mode: per-mountain from local DEM GeoTIFF
# ---------------------------------------------------------------------------

def process_mountain(mountain: dict, dem_dir: Path, output_dir: Path) -> dict | None:
    """Run viewshed + polygonize for a single mountain."""
    mid = mountain["id"]
    name = mountain["name"]
    lat = mountain["lat"]
    lon = mountain["lon"]

    dem_path = dem_dir / f"{mid}_dem.tif"
    if not dem_path.exists():
        print(f"  Warning: DEM not found at {dem_path}, skipping")
        return None

    print(f"\nProcessing {name} ({mid}) at [{lat:.4f}, {lon:.4f}]...")

    # Step 1: gdal_viewshed
    viewshed_path = output_dir / "viewshed" / f"{mid}_viewshed.tif"
    if not run_viewshed(dem_path, viewshed_path, lon, lat):
        return None

    # Step 2: gdal_polygonize -> GeoJSON
    geojson_path = output_dir / "geojson" / f"{mid}_viewshed.geojson"
    if not polygonize_viewshed(viewshed_path, geojson_path, mountain):
        return None

    return {
        "id": mid,
        "name": name,
        "viewshed_raster": str(viewshed_path),
        "geojson": str(geojson_path),
    }


# ---------------------------------------------------------------------------
# Tile index mode: S3-backed VRT → local Translate → viewshed
# ---------------------------------------------------------------------------

def _configure_gdal_for_s3() -> None:
    """Set GDAL config options for efficient S3 VRT reads."""
    gdal.SetConfigOption("GDAL_MAX_DATASET_POOL_SIZE", "450")
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
    gdal.SetConfigOption("GDAL_HTTP_MULTIPLEX", "YES")
    gdal.SetConfigOption("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")


def _build_s3_vrt(
    tile_keys: list[str], s3_bucket: str, vrt_path: Path,
) -> bool:
    """Build a VRT referencing tiles on S3 via /vsis3/ paths.

    Returns True on success.
    """
    vsis3_paths = [f"/vsis3/{s3_bucket}/{key}" for key in tile_keys]
    vrt_ds = gdal.BuildVRT(str(vrt_path), vsis3_paths)
    if vrt_ds is None:
        return False
    vrt_ds = None  # Close to flush
    return True


def _materialize_and_viewshed(
    mountain: dict, vrt_path: Path, output_dir: Path, tmpdir: str,
) -> dict | None:
    """Translate S3 VRT to local GeoTIFF, run viewshed, clean up.

    The merged GeoTIFF is temporary — only viewshed results are kept.
    """
    mid = mountain["id"]
    name = mountain["name"]
    lat = mountain["lat"]
    lon = mountain["lon"]

    # Materialize VRT to local merged GeoTIFF
    merged_path = Path(tmpdir) / f"{mid}_dem.tif"
    print(f"  Materializing DEM from S3 VRT...")
    gdal.Translate(
        str(merged_path),
        str(vrt_path),
        format="GTiff",
        creationOptions=["COMPRESS=DEFLATE", "TILED=YES"],
    )

    if not merged_path.exists():
        print(f"  Error: Failed to materialize VRT for {name}")
        return None

    # Run viewshed on local file
    viewshed_path = output_dir / "viewshed" / f"{mid}_viewshed.tif"
    if not run_viewshed(merged_path, viewshed_path, lon, lat):
        return None

    # Polygonize
    geojson_path = output_dir / "geojson" / f"{mid}_viewshed.geojson"
    if not polygonize_viewshed(viewshed_path, geojson_path, mountain):
        return None

    # Clean up local merged DEM (viewshed raster kept for debugging)
    merged_path.unlink(missing_ok=True)

    return {
        "id": mid,
        "name": name,
        "viewshed_raster": str(viewshed_path),
        "geojson": str(geojson_path),
    }


def process_mountains_s3(
    mountains: list[dict], db_path: Path,
    s3_bucket: str, s3_prefix: str, output_dir: Path,
) -> list[dict]:
    """Process all mountains using S3-backed VRT tile index mode.

    Mountains are sorted by geographic grid cell for S3 cache locality.
    Each mountain is processed sequentially: build VRT → Translate → viewshed → cleanup.
    Local storage per mountain: ~1.5GB (merged GeoTIFF + viewshed raster).
    """
    import duckdb

    from pipeline.utils.s3_tiles import tile_s3_key

    _configure_gdal_for_s3()

    con = duckdb.connect(str(db_path), read_only=True)

    # Sort mountains by grid cell for S3 cache locality
    sorted_mountains = sorted(mountains, key=lambda m: (math.floor(m["lat"]), math.floor(m["lon"])))

    results = []
    for mountain in sorted_mountains:
        mid = mountain["id"]
        name = mountain["name"]
        print(f"\nProcessing {name} ({mid})...")

        # Query tile index for this mountain's tiles
        rows = con.execute(
            "SELECT DISTINCT t.zoom, t.x, t.y, t.s3_key "
            "FROM tile_mountain tm "
            "JOIN tiles t ON tm.zoom = t.zoom AND tm.x = t.x AND tm.y = t.y "
            "WHERE tm.mountain_id = ? AND t.status = 'done'",
            [mid],
        ).fetchall()

        if not rows:
            print(f"  Warning: No tiles available for {name}, skipping")
            continue

        # Build s3_keys — use stored key or reconstruct from coordinates
        tile_keys = [
            row[3] if row[3] else tile_s3_key(s3_prefix, row[0], row[1], row[2])
            for row in rows
        ]
        print(f"  {len(tile_keys)} tiles from S3")

        with tempfile.TemporaryDirectory() as tmpdir:
            vrt_path = Path(tmpdir) / f"{mid}.vrt"
            if not _build_s3_vrt(tile_keys, s3_bucket, vrt_path):
                print(f"  Error: Failed to build S3 VRT for {name}")
                continue

            result = _materialize_and_viewshed(mountain, vrt_path, output_dir, tmpdir)
            if result:
                results.append(result)

    con.close()
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    # Step 3 of 3: Run gdal_viewshed on each mountain's DEM, then polygonize
    # the visible areas to GeoJSON for map display.
    # Input:  data/mountains.geojson + data/dem/geotiff/{id}_dem.tif (from pipeline.download_dem)
    # Output: data/viewshed/{id}_viewshed.tif + data/geojson/{id}_viewshed.geojson
    parser = argparse.ArgumentParser(description="Run viewshed analysis and polygonize")
    parser.add_argument(
        "--input", type=str, default="data/mountains.geojson",
        help="Input mountains GeoJSON file",
    )
    parser.add_argument(
        "--dem-dir", type=str, default="data/dem/geotiff",
        help="Directory containing DEM GeoTIFFs",
    )
    parser.add_argument(
        "--output-dir", type=str, default="data",
        help="Output directory",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Number of parallel worker processes (default: 4)",
    )
    # Tile index mode arguments
    parser.add_argument(
        "--tile-index", type=str, default=None,
        help="Path to DuckDB tile index (enables S3 VRT mode)",
    )
    parser.add_argument(
        "--s3-bucket", type=str, default=None,
        help="S3 bucket for tile storage (required with --tile-index)",
    )
    parser.add_argument(
        "--s3-prefix", type=str, default="dem_tiff",
        help="S3 key prefix for tile GeoTIFFs (default: dem_tiff)",
    )
    args = parser.parse_args()

    mountains_path = Path(args.input)
    if not mountains_path.exists():
        print(f"Error: {mountains_path} not found. Run fetch_mountains and download_dem first.")
        sys.exit(1)

    geojson = json.loads(mountains_path.read_text(encoding="utf-8"))
    mountains = features_to_dicts(geojson["features"])
    print(f"Loaded {len(mountains)} mountains")

    # Tile index mode: S3-backed VRT
    if args.tile_index:
        if not args.s3_bucket:
            print("Error: --s3-bucket is required when using --tile-index")
            sys.exit(1)

        db_path = Path(args.tile_index)
        if not db_path.exists():
            print(f"Error: {db_path} not found. Run build_tile_index first.")
            sys.exit(1)

        output_dir = Path(args.output_dir)
        results = process_mountains_s3(
            mountains, db_path, args.s3_bucket, args.s3_prefix, output_dir,
        )

        print(f"\nDone. Processed {len(results)}/{len(mountains)} mountains.")
        for r in results:
            print(f"  - {r['name']}: {r['geojson']}")
        return

    # Legacy mode: local DEM GeoTIFFs
    from concurrent.futures import ProcessPoolExecutor, as_completed

    dem_dir = Path(args.dem_dir)
    output_dir = Path(args.output_dir)

    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_mountain, mountain, dem_dir, output_dir): mountain
            for mountain in mountains
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    print(f"\nDone. Processed {len(results)}/{len(mountains)} mountains.")
    for r in results:
        print(f"  - {r['name']}: {r['geojson']}")


if __name__ == "__main__":
    main()
