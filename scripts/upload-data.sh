#!/usr/bin/env bash
#
# Upload pipeline data (PMTiles + metadata) to S3.
# Run this locally after the data pipeline completes.
#
# Usage:
#   ./scripts/upload-data.sh
#
# Reads S3_BUCKET_NAME and CLOUDFRONT_DISTRIBUTION_ID from .env or environment.
# Copy .env.example to .env and fill in the values before running.
#
# Prerequisites:
#   - AWS CLI configured with appropriate credentials
#   - data/fuji_viewshed.pmtiles and data/mountains.geojson must exist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$PROJECT_DIR/data"

# Load .env
if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  source "$PROJECT_DIR/.env"
  set +a
elif [[ -z "${S3_BUCKET_NAME:-}" ]]; then
  echo "Error: .env not found and S3_BUCKET_NAME is not set." >&2
  echo "Run: cp .env.example .env  and fill in the values." >&2
  exit 1
fi

BUCKET="$S3_BUCKET_NAME"
CF_DIST_ID="${CLOUDFRONT_DISTRIBUTION_ID:-}"

# Verify data files exist
for f in fuji_viewshed.pmtiles mountains.geojson; do
  if [[ ! -f "$DATA_DIR/$f" ]]; then
    echo "Error: $DATA_DIR/$f not found. Run the pipeline first." >&2
    exit 1
  fi
done

echo "Uploading data to s3://$BUCKET/data/ ..."

aws s3 cp "$DATA_DIR/fuji_viewshed.pmtiles" \
  "s3://$BUCKET/data/fuji_viewshed.pmtiles" \
  --content-type "application/octet-stream"

aws s3 cp "$DATA_DIR/mountains.geojson" \
  "s3://$BUCKET/data/mountains.geojson" \
  --content-type "application/geo+json"

echo "Upload complete."

if [[ -n "$CF_DIST_ID" ]]; then
  echo "Invalidating CloudFront cache for /data/* ..."
  aws cloudfront create-invalidation \
    --distribution-id "$CF_DIST_ID" \
    --paths "/data/*"
  echo "Invalidation created."
fi
