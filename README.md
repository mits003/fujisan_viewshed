# Fujisan Viewshed - ご当地富士可視域マップ

Web-based mapping application that visualizes the "viewshed" (visible areas) of Local Fuji mountains (ご当地富士) across Japan. See exactly which geographic areas offer a view of each mountain.

## Architecture

- **Frontend:** Next.js + MapLibre GL JS with PMTiles
- **Infrastructure:** AWS S3 + CloudFront (fully static/serverless)
- **Data Pipeline:** Python + GDAL + tippecanoe

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

### Step 2: Download DEM & Create GeoTIFFs

Downloads GSI DEM10B PNG tiles, decodes RGB to elevation, and merges into GeoTIFFs per mountain.

```bash
uv run python -m pipeline.download_dem --radius-km 20
```

Options:
- `--radius-km N` — Radius around each peak in km (default: 20 for testing, use 100 for full analysis)
- `--delay N` — Seconds between tile downloads (default: 0.5)
- `--workers N` — Parallel download threads per mountain (default: 4)
- `--input PATH` — Input GeoJSON (default: `data/mountains.geojson`)
- `--output-dir PATH` — Output directory (default: `data/dem`)

### Step 3: Viewshed Analysis & Polygonize

Runs `gdal_viewshed` on each mountain's DEM and polygonizes visible areas to GeoJSON.

```bash
uv run python -m pipeline.viewshed
```

Options:
- `--workers N` — Parallel worker processes (default: 4)
- `--input PATH` — Input GeoJSON (default: `data/mountains.geojson`)
- `--dem-dir PATH` — DEM GeoTIFFs directory (default: `data/dem/geotiff`)
- `--output-dir PATH` — Output directory (default: `data`)

### Step 4: Generate PMTiles

Converts viewshed GeoJSON polygons into a single PMTiles file for the web frontend.

```bash
uv run python -m pipeline.generate_pmtiles
```

## Deployment

AWS serverless deployment (S3 + CloudFront) managed by Terraform. See [deployment/README.md](deployment/README.md) for full setup instructions.

```bash
# 1. Provision infrastructure
cd deployment/terraform && terraform init && terraform apply

# 2. Configure environment
cp .env.example .env  # then edit .env with your values

# 3. Upload pipeline data to S3
./scripts/upload-data.sh

# 4. Frontend deploys automatically via GitHub Actions on push to main
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
├── download_dem.py              # GSI DEM downloader + GeoTIFF builder
├── viewshed.py                  # Viewshed analysis + polygonize
├── generate_pmtiles.py          # GeoJSON to PMTiles converter
└── utils/
    ├── tiles.py                 # Slippy map tile coordinate math
    ├── dem_decode.py            # GSI PNG RGB-to-elevation decoder
    └── geojson.py               # Shared GeoJSON utilities
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
