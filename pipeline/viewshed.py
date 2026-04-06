"""Step 3: Run gdal_viewshed and gdal_polygonize for each mountain.

For each mountain in mountains.json, runs gdal_viewshed on the DEM GeoTIFF
to produce a binary visibility raster, then polygonizes the visible areas
to GeoJSON.

Viewshed parameters (from design doc):
- Observer placed at mountain peak (oz=2.0m above peak)
- Target is human observer on ground (tz=1.6m eye level)
- Max distance 100km
- Standard curvature/refraction coefficient 0.85714
"""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from osgeo import gdal, ogr, osr

from pipeline.utils.geojson import features_to_dicts

gdal.UseExceptions()

# Viewshed parameters from design doc
OBSERVER_HEIGHT = 2.0  # meters above mountain peak
TARGET_HEIGHT = 1.6  # average human eye level
MAX_DISTANCE = 100000  # 100km in meters
CURV_COEFF = 0.85714  # standard curvature/refraction


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
    args = parser.parse_args()

    mountains_path = Path(args.input)
    if not mountains_path.exists():
        print(f"Error: {mountains_path} not found. Run fetch_mountains and download_dem first.")
        sys.exit(1)

    geojson = json.loads(mountains_path.read_text(encoding="utf-8"))
    mountains = features_to_dicts(geojson["features"])
    print(f"Loaded {len(mountains)} mountains")

    dem_dir = Path(args.dem_dir)
    output_dir = Path(args.output_dir)

    results = []
    if args.workers == 1:
        for mountain in mountains:
            result = process_mountain(mountain, dem_dir, output_dir)
            if result:
                results.append(result)
    else:
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
