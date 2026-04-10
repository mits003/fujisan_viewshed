"""S3 utilities for tile upload, download, and listing.

Provides batch operations with concurrent I/O via ThreadPoolExecutor
and adaptive retry for high-throughput S3 access.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Adaptive retry handles S3 throttling (503 SlowDown) automatically
_S3_CONFIG = Config(retries={"max_attempts": 10, "mode": "adaptive"})


def create_client(region: str = "ap-northeast-1") -> boto3.client:
    """Create a boto3 S3 client with adaptive retry."""
    return boto3.client("s3", region_name=region, config=_S3_CONFIG)


def list_existing_tiles(client, bucket: str, prefix: str) -> set[str]:
    """List all object keys under prefix. Returns a set for O(1) lookup.

    Uses paginated list_objects_v2 to handle >1000 objects.
    """
    keys: set[str] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


def tile_exists(client, bucket: str, key: str) -> bool:
    """Check if a specific tile exists in S3 via HEAD request."""
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise


def upload_tile(client, local_path: Path, bucket: str, key: str) -> bool:
    """Upload a single tile to S3 via put_object.

    Returns True on success.
    """
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=local_path.read_bytes(),
        )
        return True
    except ClientError:
        logger.exception("Failed to upload %s to s3://%s/%s", local_path, bucket, key)
        return False


def download_tile(client, bucket: str, key: str, local_path: Path) -> bool:
    """Download a single tile from S3 to local path.

    Returns True on success.
    """
    try:
        resp = client.get_object(Bucket=bucket, Key=key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(resp["Body"].read())
        return True
    except ClientError:
        logger.exception("Failed to download s3://%s/%s", bucket, key)
        return False


def batch_upload(
    client, file_key_pairs: list[tuple[Path, str]], bucket: str, workers: int = 30,
) -> int:
    """Upload multiple tiles concurrently.

    Args:
        file_key_pairs: List of (local_path, s3_key) tuples.

    Returns the number of successful uploads.
    """
    success = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(upload_tile, client, path, bucket, key): key
            for path, key in file_key_pairs
        }
        for future in as_completed(futures):
            if future.result():
                success += 1
    return success


def batch_download(
    client, key_path_pairs: list[tuple[str, Path]], bucket: str, workers: int = 30,
) -> int:
    """Download multiple tiles concurrently.

    Args:
        key_path_pairs: List of (s3_key, local_path) tuples.

    Returns the number of successful downloads.
    """
    success = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(download_tile, client, bucket, key, path): key
            for key, path in key_path_pairs
        }
        for future in as_completed(futures):
            if future.result():
                success += 1
    return success


def tile_s3_key(prefix: str, z: int, x: int, y: int, ext: str = ".tif") -> str:
    """Build S3 key for a tile in z/x/y directory structure."""
    return f"{prefix}/{z}/{x}/{y}{ext}"
