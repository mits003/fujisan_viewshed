# Fujisan Viewshed - ご当地富士可視域マップ

Web-based mapping application that visualizes the "viewshed" (visible areas) of Local Fuji mountains (ご当地富士) across Japan. See exactly which geographic areas offer a view of each mountain.

## Architecture

- **Frontend:** Next.js + MapLibre GL JS with PMTiles
- **Infrastructure:** AWS S3 + CloudFront (fully static/serverless)
- **Data Pipeline:** Python + GDAL + DuckDB + tippecanoe
- **Tile Storage:** S3-backed tile index for deduplicated DEM management

## Data Pipeline

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [GDAL](https://gdal.org/) (`brew install gdal`)
- [tippecanoe](https://github.com/felt/tippecanoe) (for Phase 1 Step 4)

### Setup

```bash
uv sync
```

### Step 1: Fetch Mountain Metadata

Queries Wikidata for mountains with "XX富士" aliases and saves to `data/mountains.json`.

```bash
uv run python -m pipeline.fetch_mountains --limit 3
```

Options:
- `--limit N` — Max number of mountains (default: 3)
- `--output PATH` — Output JSON path (default: `data/mountains.json`)

### Step 2: Build Tile Index (optional)

Builds a DuckDB tile index that maps tile coordinates to mountains, enabling deduplication and S3-backed workflows.

```bash
uv run python -m pipeline.build_tile_index --radius-km 20
```

Options:
- `--radius-km N` — Radius around each peak in km (default: 20)
- `--input PATH` — Input GeoJSON (default: `data/mountains.geojson`)
- `--output PATH` — Output DuckDB database (default: `data/tile_index.duckdb`)

### Step 3: Download DEM & Create GeoTIFFs

Downloads GSI DEM10B PNG tiles, decodes RGB to elevation, and merges into GeoTIFFs per mountain.

**Local mode (default):**
```bash
uv run python -m pipeline.download_dem --radius-km 20
```

**Tile index mode (S3-backed, requires Step 2):**
```bash
uv run python -m pipeline.download_dem --tile-index data/tile_index.duckdb --s3-bucket my-bucket
```

Options:
- `--radius-km N` — Radius around each peak in km (default: 20 for testing, use 100 for full analysis)
- `--delay N` — Seconds between tile downloads (default: 0.5)
- `--workers N` — Parallel download threads per mountain (default: 4)
- `--tile-index PATH` — DuckDB tile index for streaming S3 mode
- `--s3-bucket NAME` — S3 bucket for tile storage (used with `--tile-index`)
- `--input PATH` — Input GeoJSON (default: `data/mountains.geojson`)
- `--output-dir PATH` — Output directory (default: `data/dem`)

### Step 4: Viewshed Analysis & Polygonize

Runs `gdal_viewshed` on each mountain's DEM and polygonizes visible areas to GeoJSON.

**Local mode (default):**
```bash
uv run python -m pipeline.viewshed
```

**Tile index mode (S3-backed VRT, requires Step 2-3 with tile index):**
```bash
uv run python -m pipeline.viewshed --tile-index data/tile_index.duckdb --s3-bucket my-bucket
```

Options:
- `--workers N` — Parallel worker processes (default: 4)
- `--tile-index PATH` — DuckDB tile index for S3-backed VRT mode
- `--s3-bucket NAME` — S3 bucket for tile storage (used with `--tile-index`)
- `--input PATH` — Input GeoJSON (default: `data/mountains.geojson`)
- `--dem-dir PATH` — DEM GeoTIFFs directory (default: `data/dem/geotiff`)
- `--output-dir PATH` — Output directory (default: `data`)

### Step 5: Generate PMTiles

Converts viewshed GeoJSON polygons into a single PMTiles file for the web frontend.

```bash
uv run python -m pipeline.generate_pmtiles
```

## Deployment

AWS serverless deployment (S3 + CloudFront) managed by Terraform. The S3 bucket serves dual purpose: static site hosting via CloudFront and DEM tile storage for the tile index pipeline. See [deployment/README.md](deployment/README.md) for full setup instructions.

```bash
# 1. Provision infrastructure
cd deployment/terraform && terraform init && terraform apply

# 2. Configure environment
cp .env.example .env  # then edit .env with your values

# 3. Upload pipeline data to S3
./scripts/upload-data.sh

# 4. Frontend deploys automatically via GitHub Actions on push to main
```

If resources already exist in AWS (e.g. from a previous apply), import them into Terraform state:

```bash
cd deployment/terraform
terraform import aws_s3_bucket.site fujisan-viewshed
terraform import aws_cloudfront_origin_access_control.site <OAC_ID>
terraform import aws_cloudfront_cache_policy.data_files <POLICY_ID>
terraform import aws_cloudfront_cache_policy.immutable_assets <POLICY_ID>
terraform import aws_cloudfront_distribution.site <DISTRIBUTION_ID>
```

## Data Sources

| Source | URL | Usage |
|--------|-----|-------|
| Wikidata SPARQL | https://query.wikidata.org/sparql | Mountain metadata |
| GSI DEM10B | https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png | Elevation tiles (zoom 14) |

See [referenced_sources.csv](referenced_sources.csv) for the full list of referenced sources.

## Project Structure

```
pipeline/                        # Data pipeline (Python + GDAL)
├── fetch_mountains.py           # Wikidata SPARQL fetcher
├── build_tile_index.py          # DuckDB tile index builder
├── download_dem.py              # GSI DEM downloader + GeoTIFF builder
├── viewshed.py                  # Viewshed analysis + polygonize
├── generate_pmtiles.py          # GeoJSON to PMTiles converter
└── utils/
    ├── tiles.py                 # Slippy map tile coordinate math
    ├── dem_decode.py            # GSI PNG RGB-to-elevation decoder
    ├── geojson.py               # Shared GeoJSON utilities
    └── s3_tiles.py              # S3 batch upload/download utilities
web/                             # Frontend (Next.js + MapLibre GL JS)
├── src/
│   ├── app/                     # Next.js app router pages
│   └── components/              # React components (Map, etc.)
└── public/data/                 # Pipeline output for local dev
deployment/
├── README.md                    # Deployment guide
└── terraform/                   # AWS infrastructure (S3 + CloudFront)
scripts/
└── upload-data.sh               # Upload pipeline data to S3
data/                            # Pipeline output (gitignored)
├── mountains.geojson            # Mountain metadata
├── fuji_viewshed.pmtiles        # Vector tiles for frontend
├── dem/                         # DEM GeoTIFFs
├── viewshed/                    # Viewshed rasters
└── geojson/                     # Viewshed polygons
```
