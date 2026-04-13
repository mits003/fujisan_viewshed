"""Step 4: Convert viewshed GeoJSON files to a single PMTiles archive using tippecanoe.

Merges all per-mountain viewshed GeoJSON files into one PMTiles file
suitable for serving via HTTP Range Requests with MapLibre GL JS.

tippecanoe settings:
- Layer name: "viewshed"
- Min zoom 4, max zoom 12 (balances detail vs file size)
- Simplification appropriate for viewshed polygons
- Coalesce features with identical properties to reduce tile size
"""

import argparse
import subprocess
import sys
from pathlib import Path

from pipeline.defaults import GEOJSON_DIR, PMTILES_MAX_ZOOM, PMTILES_MIN_ZOOM, PMTILES_OUTPUT


def generate_pmtiles(
    geojson_dir: Path,
    output_path: Path,
    *,
    min_zoom: int = PMTILES_MIN_ZOOM,
    max_zoom: int = PMTILES_MAX_ZOOM,
) -> bool:
    """Merge all viewshed GeoJSON files into a single PMTiles archive.

    Returns True on success.
    """
    geojson_files = sorted(geojson_dir.glob("*_viewshed.geojson"))
    if not geojson_files:
        print(f"Error: No viewshed GeoJSON files found in {geojson_dir}")
        return False

    print(f"Found {len(geojson_files)} GeoJSON files:")
    for f in geojson_files:
        print(f"  - {f.name}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "tippecanoe",
        "-o", str(output_path),
        f"--minimum-zoom={min_zoom}",
        f"--maximum-zoom={max_zoom}",
        "--layer=viewshed",
        "--coalesce-densest-as-needed",
        "--extend-zooms-if-still-dropping",
        "--force",  # overwrite existing output
        *[str(f) for f in geojson_files],
    ]

    print(f"\nRunning tippecanoe (zoom {min_zoom}-{max_zoom})...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: tippecanoe failed:\n{result.stderr}")
        return False

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Generated {output_path} ({size_mb:.1f} MB)")
    return True


def main():
    # Step 4 of Phase 1: Convert per-mountain viewshed GeoJSON files into
    # a single PMTiles archive for efficient map rendering.
    # Input:  data/geojson/{id}_viewshed.geojson (from pipeline.viewshed)
    # Output: data/fuji_viewshed.pmtiles
    parser = argparse.ArgumentParser(
        description="Convert viewshed GeoJSON to PMTiles"
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default=GEOJSON_DIR,
        help="Directory containing viewshed GeoJSON files",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=PMTILES_OUTPUT,
        help="Output PMTiles file path",
    )
    parser.add_argument(
        "--min-zoom", type=int, default=PMTILES_MIN_ZOOM, help="Minimum zoom level (default: 4)"
    )
    parser.add_argument(
        "--max-zoom", type=int, default=PMTILES_MAX_ZOOM, help="Maximum zoom level (default: 12)"
    )
    args = parser.parse_args()

    success = generate_pmtiles(
        Path(args.input_dir),
        Path(args.output),
        min_zoom=args.min_zoom,
        max_zoom=args.max_zoom,
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
