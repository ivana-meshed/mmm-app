"""
Cloud storage abstraction layer.

Provides a unified interface for cloud storage operations that works with both
Google Cloud Storage (GCS) and Amazon S3.

The appropriate implementation is selected based on the CLOUD_PROVIDER environment variable:
- "gcp" (default): Use Google Cloud Storage
- "aws": Use Amazon S3
"""

import io
import json
import os
import re
import tempfile
from abc import ABC, abstractmethod
from typing import List, Optional

import pandas as pd


class CloudStorageProvider(ABC):
    """Abstract base class for cloud storage providers."""

    @abstractmethod
    def normalize_blob_path(self, path: str) -> str:
        """Normalize a blob path by removing leading slashes and collapsing multiple slashes."""
        pass

    @abstractmethod
    def normalize_uri(self, uri: str) -> str:
        """Normalize a cloud storage URI."""
        pass

    @abstractmethod
    def upload_file(self, bucket_name: str, local_path: str, dest_blob: str) -> str:
        """Upload a file from local filesystem to cloud storage."""
        pass

    @abstractmethod
    def download_file(
        self, bucket_name: str, blob_path: str, local_path: str
    ) -> None:
        """Download a file from cloud storage to local filesystem."""
        pass

    @abstractmethod
    def read_json(self, bucket_name: str, blob_path: str) -> dict:
        """Read and parse a JSON file from cloud storage."""
        pass

    @abstractmethod
    def write_json(
        self, bucket_name: str, blob_path: str, data: dict, indent: int = 2
    ) -> str:
        """Write dictionary data as JSON to cloud storage."""
        pass

    @abstractmethod
    def read_csv(self, bucket_name: str, blob_path: str) -> pd.DataFrame:
        """Read a CSV file from cloud storage into a pandas DataFrame."""
        pass

    @abstractmethod
    def write_csv(
        self, bucket_name: str, blob_path: str, df: pd.DataFrame
    ) -> str:
        """Write a pandas DataFrame as CSV to cloud storage."""
        pass

    @abstractmethod
    def read_parquet(self, bucket_name: str, blob_path: str) -> pd.DataFrame:
        """Read a Parquet file from cloud storage into a pandas DataFrame."""
        pass

    @abstractmethod
    def list_blobs(
        self,
        bucket_name: str,
        prefix: Optional[str] = None,
        delimiter: Optional[str] = None,
    ) -> List[str]:
        """List blobs in a bucket with optional prefix filtering."""
        pass

    @abstractmethod
    def blob_exists(self, bucket_name: str, blob_path: str) -> bool:
        """Check if a blob exists in cloud storage."""
        pass


class GCSProvider(CloudStorageProvider):
    """Google Cloud Storage implementation."""

    def __init__(self):
        from google.cloud import storage

        self.client = storage.Client()

    def normalize_blob_path(self, path: str) -> str:
        """Normalize a GCS blob path."""
        p = (path or "").strip()
        p = re.sub(r"^/+", "", p)  # strip leading /
        p = re.sub(r"/{2,}", "/", p)  # collapse // -> /
        return p

    def normalize_uri(self, uri: str) -> str:
        """Normalize a gs:// URI."""
        s = (uri or "").strip()
        if not s.startswith("gs://"):
            return s
        s2 = s[5:]
        if "/" not in s2:
            return s
        bucket, obj = s2.split("/", 1)
        obj = re.sub(r"^/+", "", obj)
        obj = re.sub(r"/{2,}", "/", obj)
        return f"gs://{bucket}/{obj}"

    def upload_file(
        self, bucket_name: str, local_path: str, dest_blob: str
    ) -> str:
        """Upload a file to GCS."""
        bucket = self.client.bucket(bucket_name)
        blob_path = self.normalize_blob_path(dest_blob)
        blob = bucket.blob(blob_path)
        blob.upload_from_filename(local_path)
        return f"gs://{bucket_name}/{blob_path}"

    def download_file(
        self, bucket_name: str, blob_path: str, local_path: str
    ) -> None:
        """Download a file from GCS."""
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.download_to_filename(local_path)

    def read_json(self, bucket_name: str, blob_path: str) -> dict:
        """Read JSON from GCS."""
        blob = self.client.bucket(bucket_name).blob(blob_path)
        if not blob.exists():
            raise FileNotFoundError(f"gs://{bucket_name}/{blob_path} not found")
        return json.loads(blob.download_as_bytes())

    def write_json(
        self, bucket_name: str, blob_path: str, data: dict, indent: int = 2
    ) -> str:
        """Write JSON to GCS."""
        bucket = self.client.bucket(bucket_name)
        blob_path = self.normalize_blob_path(blob_path)
        blob = bucket.blob(blob_path)
        blob.upload_from_string(
            json.dumps(data, indent=indent), content_type="application/json"
        )
        return f"gs://{bucket_name}/{blob_path}"

    def read_csv(self, bucket_name: str, blob_path: str) -> pd.DataFrame:
        """Read CSV from GCS."""
        blob = self.client.bucket(bucket_name).blob(blob_path)
        if not blob.exists():
            return pd.DataFrame()
        raw = blob.download_as_bytes()
        if not raw:
            return pd.DataFrame()
        return pd.read_csv(io.BytesIO(raw))

    def write_csv(
        self, bucket_name: str, blob_path: str, df: pd.DataFrame
    ) -> str:
        """Write CSV to GCS."""
        buffer = io.BytesIO()
        df.to_csv(buffer, index=False)
        buffer.seek(0)

        bucket = self.client.bucket(bucket_name)
        blob_path = self.normalize_blob_path(blob_path)
        blob = bucket.blob(blob_path)
        blob.upload_from_file(buffer, content_type="text/csv")
        return f"gs://{bucket_name}/{blob_path}"

    def read_parquet(self, bucket_name: str, blob_path: str) -> pd.DataFrame:
        """Read Parquet from GCS."""
        blob = self.client.bucket(bucket_name).blob(blob_path)
        if not blob.exists():
            raise FileNotFoundError(f"gs://{bucket_name}/{blob_path} not found")

        with tempfile.NamedTemporaryFile(
            suffix=".parquet", delete=False
        ) as tmp:
            blob.download_to_filename(tmp.name)
            return pd.read_parquet(tmp.name)

    def list_blobs(
        self,
        bucket_name: str,
        prefix: Optional[str] = None,
        delimiter: Optional[str] = None,
    ) -> List[str]:
        """List blobs in GCS bucket."""
        blobs = self.client.list_blobs(
            bucket_name, prefix=prefix, delimiter=delimiter
        )
        return [blob.name for blob in blobs]

    def blob_exists(self, bucket_name: str, blob_path: str) -> bool:
        """Check if blob exists in GCS."""
        blob = self.client.bucket(bucket_name).blob(blob_path)
        return blob.exists()


class S3Provider(CloudStorageProvider):
    """Amazon S3 implementation."""

    def __init__(self):
        import boto3

        self.client = boto3.client("s3")
        self.resource = boto3.resource("s3")

    def normalize_blob_path(self, path: str) -> str:
        """Normalize an S3 object key."""
        p = (path or "").strip()
        p = re.sub(r"^/+", "", p)  # strip leading /
        p = re.sub(r"/{2,}", "/", p)  # collapse // -> /
        return p

    def normalize_uri(self, uri: str) -> str:
        """Normalize an s3:// URI."""
        s = (uri or "").strip()
        if not s.startswith("s3://"):
            return s
        s2 = s[5:]
        if "/" not in s2:
            return s
        bucket, obj = s2.split("/", 1)
        obj = re.sub(r"^/+", "", obj)
        obj = re.sub(r"/{2,}", "/", obj)
        return f"s3://{bucket}/{obj}"

    def upload_file(
        self, bucket_name: str, local_path: str, dest_blob: str
    ) -> str:
        """Upload a file to S3."""
        key = self.normalize_blob_path(dest_blob)
        self.client.upload_file(local_path, bucket_name, key)
        return f"s3://{bucket_name}/{key}"

    def download_file(
        self, bucket_name: str, blob_path: str, local_path: str
    ) -> None:
        """Download a file from S3."""
        self.client.download_file(bucket_name, blob_path, local_path)

    def read_json(self, bucket_name: str, blob_path: str) -> dict:
        """Read JSON from S3."""
        try:
            response = self.client.get_object(Bucket=bucket_name, Key=blob_path)
            return json.loads(response["Body"].read())
        except self.client.exceptions.NoSuchKey:
            raise FileNotFoundError(f"s3://{bucket_name}/{blob_path} not found")

    def write_json(
        self, bucket_name: str, blob_path: str, data: dict, indent: int = 2
    ) -> str:
        """Write JSON to S3."""
        key = self.normalize_blob_path(blob_path)
        self.client.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=json.dumps(data, indent=indent),
            ContentType="application/json",
        )
        return f"s3://{bucket_name}/{key}"

    def read_csv(self, bucket_name: str, blob_path: str) -> pd.DataFrame:
        """Read CSV from S3."""
        try:
            response = self.client.get_object(Bucket=bucket_name, Key=blob_path)
            raw = response["Body"].read()
            if not raw:
                return pd.DataFrame()
            return pd.read_csv(io.BytesIO(raw))
        except self.client.exceptions.NoSuchKey:
            return pd.DataFrame()

    def write_csv(
        self, bucket_name: str, blob_path: str, df: pd.DataFrame
    ) -> str:
        """Write CSV to S3."""
        buffer = io.BytesIO()
        df.to_csv(buffer, index=False)
        buffer.seek(0)

        key = self.normalize_blob_path(blob_path)
        self.client.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=buffer.getvalue(),
            ContentType="text/csv",
        )
        return f"s3://{bucket_name}/{key}"

    def read_parquet(self, bucket_name: str, blob_path: str) -> pd.DataFrame:
        """Read Parquet from S3."""
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".parquet", delete=False
            ) as tmp:
                self.client.download_file(bucket_name, blob_path, tmp.name)
                return pd.read_parquet(tmp.name)
        except self.client.exceptions.NoSuchKey:
            raise FileNotFoundError(f"s3://{bucket_name}/{blob_path} not found")

    def list_blobs(
        self,
        bucket_name: str,
        prefix: Optional[str] = None,
        delimiter: Optional[str] = None,
    ) -> List[str]:
        """List objects in S3 bucket."""
        kwargs = {"Bucket": bucket_name}
        if prefix:
            kwargs["Prefix"] = prefix
        if delimiter:
            kwargs["Delimiter"] = delimiter

        objects = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(**kwargs):
            if "Contents" in page:
                objects.extend([obj["Key"] for obj in page["Contents"]])
        return objects

    def blob_exists(self, bucket_name: str, blob_path: str) -> bool:
        """Check if object exists in S3."""
        try:
            self.client.head_object(Bucket=bucket_name, Key=blob_path)
            return True
        except self.client.exceptions.NoSuchKey:
            return False
        except Exception:
            return False


# Singleton instance
_provider: Optional[CloudStorageProvider] = None


def get_storage_provider() -> CloudStorageProvider:
    """
    Get the appropriate cloud storage provider based on CLOUD_PROVIDER env var.

    Returns:
        CloudStorageProvider instance (GCS or S3)
    """
    global _provider
    if _provider is None:
        provider_name = os.getenv("CLOUD_PROVIDER", "gcp").lower()
        if provider_name == "aws":
            _provider = S3Provider()
        else:
            _provider = GCSProvider()
    return _provider


# Convenience functions that delegate to the provider
def normalize_blob_path(path: str) -> str:
    """Normalize a blob path."""
    return get_storage_provider().normalize_blob_path(path)


def normalize_uri(uri: str) -> str:
    """Normalize a cloud storage URI."""
    return get_storage_provider().normalize_uri(uri)


def upload_to_cloud(bucket_name: str, local_path: str, dest_blob: str) -> str:
    """Upload a file from local filesystem to cloud storage."""
    return get_storage_provider().upload_file(bucket_name, local_path, dest_blob)


def download_from_cloud(
    bucket_name: str, blob_path: str, local_path: str
) -> None:
    """Download a file from cloud storage to local filesystem."""
    get_storage_provider().download_file(bucket_name, blob_path, local_path)


def read_json_from_cloud(bucket_name: str, blob_path: str) -> dict:
    """Read and parse a JSON file from cloud storage."""
    return get_storage_provider().read_json(bucket_name, blob_path)


def write_json_to_cloud(
    bucket_name: str, blob_path: str, data: dict, indent: int = 2
) -> str:
    """Write dictionary data as JSON to cloud storage."""
    return get_storage_provider().write_json(bucket_name, blob_path, data, indent)


def read_csv_from_cloud(bucket_name: str, blob_path: str) -> pd.DataFrame:
    """Read a CSV file from cloud storage into a pandas DataFrame."""
    return get_storage_provider().read_csv(bucket_name, blob_path)


def write_csv_to_cloud(
    bucket_name: str, blob_path: str, df: pd.DataFrame
) -> str:
    """Write a pandas DataFrame as CSV to cloud storage."""
    return get_storage_provider().write_csv(bucket_name, blob_path, df)


def read_parquet_from_cloud(bucket_name: str, blob_path: str) -> pd.DataFrame:
    """Read a Parquet file from cloud storage into a pandas DataFrame."""
    return get_storage_provider().read_parquet(bucket_name, blob_path)


def list_blobs(
    bucket_name: str,
    prefix: Optional[str] = None,
    delimiter: Optional[str] = None,
) -> List[str]:
    """List blobs in a bucket with optional prefix filtering."""
    return get_storage_provider().list_blobs(bucket_name, prefix, delimiter)


def blob_exists(bucket_name: str, blob_path: str) -> bool:
    """Check if a blob exists in cloud storage."""
    return get_storage_provider().blob_exists(bucket_name, blob_path)


# Backward compatibility aliases
upload_to_gcs = upload_to_cloud
download_from_gcs = download_from_cloud
read_json_from_gcs = read_json_from_cloud
write_json_to_gcs = write_json_to_cloud
read_csv_from_gcs = read_csv_from_cloud
write_csv_to_gcs = write_csv_to_cloud
read_parquet_from_gcs = read_parquet_from_cloud
normalize_gs_uri = normalize_uri
