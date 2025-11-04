"""
Google Cloud Storage utility functions.

Provides common operations for:
- Uploading and downloading files
- Reading and writing JSON/CSV data
- Managing blob paths and URIs
- Listing and searching blobs
"""

import io
import json
import re
import tempfile
from typing import List, Optional

import pandas as pd
from google.cloud import storage


def normalize_blob_path(path: str) -> str:
    """
    Normalize a GCS blob path by removing leading slashes and collapsing multiple slashes.

    Args:
        path: The blob path to normalize

    Returns:
        Normalized blob path

    Example:
        >>> normalize_blob_path("//path//to/blob")
        'path/to/blob'
    """
    p = (path or "").strip()
    p = re.sub(r"^/+", "", p)  # strip leading /
    p = re.sub(r"/{2,}", "/", p)  # collapse // -> /
    return p


def normalize_gs_uri(uri: str) -> str:
    """
    Normalize a gs:// URI by cleaning up the object path.

    For non-gs:// URIs (e.g., local paths or other protocols),
    the input is returned unchanged.

    Args:
        uri: GCS URI to normalize (or other URI/path)

    Returns:
        Normalized URI (or original input if not a GCS URI)

    Example:
        >>> normalize_gs_uri("gs://bucket//path/to/file")
        'gs://bucket/path/to/file'
        >>> normalize_gs_uri("/local/path")
        '/local/path'
    """
    s = (uri or "").strip()
    if not s.startswith("gs://"):
        return s
    # split into bucket + object and normalize the object part
    s2 = s[5:]
    if "/" not in s2:
        return s  # bucket only
    bucket, obj = s2.split("/", 1)
    obj = re.sub(r"^/+", "", obj)
    obj = re.sub(r"/{2,}", "/", obj)
    return f"gs://{bucket}/{obj}"


def upload_to_gcs(bucket_name: str, local_path: str, dest_blob: str) -> str:
    """
    Upload a file from local filesystem to GCS.

    Args:
        bucket_name: Name of the GCS bucket
        local_path: Path to the local file
        dest_blob: Destination blob path in GCS

    Returns:
        Full GCS URI (gs://bucket/path)
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob_path = normalize_blob_path(dest_blob)
    blob = bucket.blob(blob_path)
    blob.upload_from_filename(local_path)
    return f"gs://{bucket_name}/{blob_path}"


def download_from_gcs(
    bucket_name: str, blob_path: str, local_path: str
) -> None:
    """
    Download a file from GCS to local filesystem.

    Args:
        bucket_name: Name of the GCS bucket
        blob_path: Path to the blob in GCS
        local_path: Local path where file will be saved
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.download_to_filename(local_path)


def read_json_from_gcs(bucket_name: str, blob_path: str) -> dict:
    """
    Read and parse a JSON file from GCS.

    Args:
        bucket_name: Name of the GCS bucket
        blob_path: Path to the JSON blob

    Returns:
        Parsed JSON as dictionary

    Raises:
        FileNotFoundError: If blob doesn't exist
    """
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{blob_path} not found")
    return json.loads(blob.download_as_bytes())


def write_json_to_gcs(
    bucket_name: str, blob_path: str, data: dict, indent: int = 2
) -> str:
    """
    Write dictionary data as JSON to GCS.

    Args:
        bucket_name: Name of the GCS bucket
        blob_path: Destination blob path
        data: Dictionary to write as JSON
        indent: JSON indentation (default: 2)

    Returns:
        Full GCS URI (gs://bucket/path)
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob_path = normalize_blob_path(blob_path)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(
        json.dumps(data, indent=indent), content_type="application/json"
    )
    return f"gs://{bucket_name}/{blob_path}"


def read_csv_from_gcs(bucket_name: str, blob_path: str) -> pd.DataFrame:
    """
    Read a CSV file from GCS into a pandas DataFrame.

    Args:
        bucket_name: Name of the GCS bucket
        blob_path: Path to the CSV blob

    Returns:
        DataFrame with CSV data
    """
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_path)
    if not blob.exists():
        return pd.DataFrame()
    raw = blob.download_as_bytes()
    if not raw:
        return pd.DataFrame()
    return pd.read_csv(io.BytesIO(raw))


def write_csv_to_gcs(bucket_name: str, blob_path: str, df: pd.DataFrame) -> str:
    """
    Write a pandas DataFrame as CSV to GCS.

    Args:
        bucket_name: Name of the GCS bucket
        blob_path: Destination blob path
        df: DataFrame to write

    Returns:
        Full GCS URI (gs://bucket/path)
    """
    buffer = io.BytesIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob_path = normalize_blob_path(blob_path)
    blob = bucket.blob(blob_path)
    blob.upload_from_file(buffer, content_type="text/csv")
    return f"gs://{bucket_name}/{blob_path}"


def read_parquet_from_gcs(bucket_name: str, blob_path: str) -> pd.DataFrame:
    """
    Read a Parquet file from GCS into a pandas DataFrame.

    Args:
        bucket_name: Name of the GCS bucket
        blob_path: Path to the Parquet blob

    Returns:
        DataFrame with Parquet data

    Raises:
        FileNotFoundError: If blob doesn't exist
    """
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{blob_path} not found")

    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        blob.download_to_filename(tmp.name)
        return pd.read_parquet(tmp.name)


def list_blobs(
    bucket_name: str,
    prefix: Optional[str] = None,
    delimiter: Optional[str] = None,
) -> List[str]:
    """
    List blobs in a GCS bucket with optional prefix filtering.

    Args:
        bucket_name: Name of the GCS bucket
        prefix: Optional prefix to filter blobs
        delimiter: Optional delimiter for hierarchical listing

    Returns:
        List of blob names
    """
    client = storage.Client()
    blobs = client.list_blobs(bucket_name, prefix=prefix, delimiter=delimiter)
    return [blob.name for blob in blobs]


def blob_exists(bucket_name: str, blob_path: str) -> bool:
    """
    Check if a blob exists in GCS.

    Args:
        bucket_name: Name of the GCS bucket
        blob_path: Path to the blob

    Returns:
        True if blob exists, False otherwise
    """
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_path)
    return blob.exists()
