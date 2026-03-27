#!/usr/bin/env bash
#
# Upload pipeline data (PMTiles + metadata) to S3.
# Run this locally after the data pipeline completes.
#
# Usage:
#   ./scripts/upload-data.sh <S3_BUCKET_NAME> [CLOUDFRONT_DISTRIBUTION_ID]
#
# Prerequisites:
#   - AWS CLI configured with appropriate credentials
#   - data/fuji_viewshed.pmtiles and data/mountains.geojson must exist

set -euo pipefail

BUCKET="${1:?Usage: $0 <S3_BUCKET_NAME> [CLOUDFRONT_DISTRIBUTION_ID]}"
CF_DIST_ID="${2:-}"
DATA_DIR="$(cd "$(dirname "$0")/.." && pwd)/data"

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
