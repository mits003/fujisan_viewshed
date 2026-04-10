"""Build a DuckDB tile index for all mountains.

Computes bounding tiles for every mountain, deduplicates,
and stores the tile-mountain mapping plus per-tile status in DuckDB.

Input:  data/mountains.geojson  (from pipeline.fetch_mountains)
Output: data/dem/tiles.duckdb
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb

from pipeline.utils.geojson import features_to_dicts
from pipeline.utils.tiles import bounding_tiles

ZOOM = 14


def build_index(mountains: list[dict], radius_km: float, db_path: Path) -> duckdb.DuckDBPyConnection:
    """Create DuckDB tile index from mountain list.

    Returns the open DuckDB connection.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))

    # Create tables (idempotent — drop if re-running)
    con.execute("DROP TABLE IF EXISTS tile_mountain")
    con.execute("DROP TABLE IF EXISTS tiles")

    con.execute("""
        CREATE TABLE tiles (
            zoom    TINYINT NOT NULL,
            x       INTEGER NOT NULL,
            y       INTEGER NOT NULL,
            status  VARCHAR DEFAULT 'pending',
            s3_key  VARCHAR,
            updated_at TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (zoom, x, y)
        )
    """)

    con.execute("""
        CREATE TABLE tile_mountain (
            zoom        TINYINT NOT NULL,
            x           INTEGER NOT NULL,
            y           INTEGER NOT NULL,
            mountain_id VARCHAR NOT NULL
        )
    """)

    # Collect all tile-mountain pairs
    pairs: list[tuple[int, int, int, str]] = []
    unique_tiles: set[tuple[int, int, int]] = set()

    for m in mountains:
        mid = m["id"]
        lat, lon = m["lat"], m["lon"]
        x_min, x_max, y_min, y_max = bounding_tiles(lat, lon, radius_km, ZOOM)

        for tx in range(x_min, x_max + 1):
            for ty in range(y_min, y_max + 1):
                pairs.append((ZOOM, tx, ty, mid))
                unique_tiles.add((ZOOM, tx, ty))

    # Bulk insert tile_mountain
    con.executemany(
        "INSERT INTO tile_mountain (zoom, x, y, mountain_id) VALUES (?, ?, ?, ?)",
        pairs,
    )

    # Bulk insert unique tiles
    con.executemany(
        "INSERT INTO tiles (zoom, x, y) VALUES (?, ?, ?)",
        list(unique_tiles),
    )

    # Add indexes
    con.execute("CREATE INDEX idx_tm_mountain ON tile_mountain(mountain_id)")
    con.execute("CREATE INDEX idx_tiles_status ON tiles(status)")

    return con


def print_stats(con: duckdb.DuckDBPyConnection, num_mountains: int) -> None:
    """Print tile index statistics."""
    total_pairs = con.execute("SELECT COUNT(*) FROM tile_mountain").fetchone()[0]
    unique_tiles = con.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
    overlap_ratio = 1 - (unique_tiles / total_pairs) if total_pairs > 0 else 0

    # Estimate data volume
    est_png_gb = unique_tiles * 35e3 / 1e9  # ~35KB avg per PNG
    est_tiff_gb = unique_tiles * 500e3 / 1e9  # ~500KB avg per GeoTIFF

    print(f"\nTile Index Statistics:")
    print(f"  Mountains:          {num_mountains}")
    print(f"  Total tile-mountain pairs: {total_pairs:,}")
    print(f"  Unique tiles:       {unique_tiles:,}")
    print(f"  Overlap ratio:      {overlap_ratio:.1%}")
    print(f"  Avg tiles/mountain: {total_pairs / num_mountains:,.0f}")
    print(f"  Est. PNG volume:    {est_png_gb:.1f} GB")
    print(f"  Est. GeoTIFF volume: {est_tiff_gb:.1f} GB")


def main():
    parser = argparse.ArgumentParser(description="Build DuckDB tile index for all mountains")
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
        "--output",
        type=str,
        default="data/dem/tiles.duckdb",
        help="Output DuckDB database path",
    )
    args = parser.parse_args()

    mountains_path = Path(args.input)
    if not mountains_path.exists():
        print(f"Error: {mountains_path} not found. Run fetch_mountains first.")
        sys.exit(1)

    geojson = json.loads(mountains_path.read_text(encoding="utf-8"))
    mountains = features_to_dicts(geojson["features"])
    print(f"Loaded {len(mountains)} mountains from {mountains_path}")

    db_path = Path(args.output)
    print(f"Building tile index (zoom={ZOOM}, radius={args.radius_km}km)...")

    con = build_index(mountains, args.radius_km, db_path)
    print_stats(con, len(mountains))
    con.close()

    print(f"\nTile index saved to {db_path}")


if __name__ == "__main__":
    main()
