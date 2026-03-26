"""Step 1: Fetch Local Fuji (ご当地富士) mountain metadata from Wikidata.

Queries the Wikidata SPARQL endpoint for mountains in Japan whose Japanese
alt-label contains "富士", then outputs structured GeoJSON metadata.

Reference: https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service
"""

import argparse
from pathlib import Path

import geopandas as gpd
import requests

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

#
SPARQL_QUERY = """\
SELECT ?item ?itemLabel ?coord ?elev
       (GROUP_CONCAT(DISTINCT ?altLabel; SEPARATOR="|||") AS ?aliases)
WHERE {
  ?item wdt:P31 wd:Q8502.        # instance of: mountain
  ?item wdt:P17 wd:Q17.          # country: Japan
  ?item wdt:P625 ?coord.         # coordinate location
  ?item wdt:P2044 ?elev.         # elevation
  ?item skos:altLabel ?altLabel.  # alternative label (alias)
  FILTER(LANG(?altLabel) = "ja")            # Japanese aliases only
  FILTER(REGEX(?altLabel, ".+富士$"))       # alias ends with "富士"
  FILTER(?item != wd:Q39231)                # exclude Mount Fuji itself
  SERVICE wikibase:label { bd:serviceParam wikibase:language "ja,en". }
} GROUP BY ?item ?itemLabel ?coord ?elev
ORDER BY DESC(?elev)
"""


ALIAS_SEPARATOR = "|||"


def extract_fuji_aliases(aliases_str: str) -> list[str]:
    """Extract aliases containing '富士' from the SPARQL GROUP_CONCAT result.

    Example: "矢筈山|||川浦富士" → ["川浦富士"]
    """
    fuji_aliases = []
    for alias in aliases_str.split(ALIAS_SEPARATOR):
        alias = alias.strip()
        if alias and "富士" in alias:
            fuji_aliases.append(alias)
    return fuji_aliases


def fetch_mountains(limit: int | None = None) -> gpd.GeoDataFrame:
    """Fetch Local Fuji mountains from Wikidata.

    Returns a GeoDataFrame with Point geometries.
    """
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": "FujisanViewshed/1.0 (https://github.com/fujisan-viewshed)",
    }

    print("Querying Wikidata SPARQL endpoint...")
    resp = requests.get(
        WIKIDATA_SPARQL_URL, params={"query": SPARQL_QUERY}, headers=headers, timeout=60
    )
    resp.raise_for_status()
    data = resp.json()

    rows = []
    for binding in data["results"]["bindings"]:
        wikidata_url = binding["item"]["value"]
        aliases_str = binding.get("aliases", {}).get("value", "")
        fuji_aliases = extract_fuji_aliases(aliases_str)

        rows.append({
            "id": wikidata_url.split("/")[-1],
            "name": binding["itemLabel"]["value"],
            "aliases": fuji_aliases,
            "fuji_alias": fuji_aliases[0] if fuji_aliases else "",
            "elevation": float(binding["elev"]["value"]),
            "wikidata_url": wikidata_url,
            "coord_wkt": binding["coord"]["value"],
        })

    # Wikidata may return multiple records for each mountan's QID,
    gdf = gpd.GeoDataFrame(rows)
    gdf = gpd.GeoDataFrame(
        gdf.drop(columns="coord_wkt"),
        geometry=gpd.GeoSeries.from_wkt(gdf["coord_wkt"]),
        crs="EPSG:4326",
    )
    gdf = gdf.drop_duplicates(subset="id")

    if limit:
        gdf = gdf.head(limit)

    print(f"Found {len(gdf)} mountains.")
    return gdf


def main():
    # Step 1 of 3: Query Wikidata for "ご当地富士" (Local Fuji) mountains
    # and save their metadata (name, coordinates, elevation) as GeoJSON.
    # Output: data/mountains.geojson
    # Next step: pipeline.download_dem (downloads DEM tiles for each mountain)
    parser = argparse.ArgumentParser(description="Fetch Local Fuji mountains from Wikidata")
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Max number of mountains to fetch (default: 3)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/mountains.geojson",
        help="Output GeoJSON file path",
    )
    args = parser.parse_args()

    gdf = fetch_mountains(limit=args.limit)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(output_path, driver="GeoJSON")
    print(f"Saved {len(gdf)} mountains to {output_path}")

    for _, row in gdf.iterrows():
        print(f"  - {row['name']} ({row['fuji_alias']}) [{row.geometry.y:.4f}, {row.geometry.x:.4f}] {row['elevation']}m")


if __name__ == "__main__":
    main()
