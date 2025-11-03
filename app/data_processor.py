"""
Data processing utilities for the MMM application.

Provides optimized data processing capabilities including:
- CSV to Parquet conversion with compression
- Data type optimization for memory efficiency
- GCS upload/download integration
"""

import io
import logging
import os
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from config import settings
from google.cloud import storage

logger = logging.getLogger(__name__)


class DataProcessor:
    """
    Optimized data processor with Parquet support.

    Handles conversion of CSV/DataFrame data to optimized Parquet format
    with automatic type optimization and compression. Integrates with GCS
    for cloud storage operations.
    """

    def __init__(self, gcs_bucket: Optional[str] = None):
        """
        Initialize DataProcessor.

        Args:
            gcs_bucket: GCS bucket name (defaults to settings.GCS_BUCKET)
        """
        self.gcs_bucket = gcs_bucket or settings.GCS_BUCKET
        self.storage_client = storage.Client()

    def csv_to_parquet(
        self, csv_data: pd.DataFrame, output_path: Optional[str] = None
    ) -> io.BytesIO:
        """
        Convert CSV DataFrame to Parquet format with optimization.

        Applies data type optimization for better compression and performance,
        then converts to Parquet format with optimal settings for MMM workloads.

        Args:
            csv_data: Input DataFrame to convert
            output_path: Optional local file path to save Parquet file

        Returns:
            BytesIO buffer containing Parquet data
        """

        # Optimize data types for better compression and speed
        df_optimized = self._optimize_dtypes(csv_data)

        # Create Parquet file in memory
        table = pa.Table.from_pandas(df_optimized)

        # Use memory buffer for Cloud environment
        buffer = io.BytesIO()

        # Write with optimal compression settings
        pq.write_table(
            table,
            buffer,
            compression="snappy",  # Good balance of speed vs compression
            use_dictionary=True,  # Better for categorical data
            row_group_size=50000,  # Optimize for typical MMM dataset sizes
            use_byte_stream_split=True,  # Better compression for floats
        )

        buffer.seek(0)

        if output_path:
            # Save to local file
            with open(output_path, "wb") as f:
                f.write(buffer.read())
            buffer.seek(0)

        logger.info(
            f"Converted DataFrame to Parquet: {len(df_optimized):,} rows, "
            f"Original CSV size: {df_optimized.memory_usage(deep=True).sum() / 1024**2:.1f} MB"
        )

        return buffer

    def _optimize_dtypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Optimize DataFrame data types for better performance and compression.

        Automatically detects and converts to optimal data types:
        - Integers: Use smallest type that fits the data range (int8, int16, etc.)
        - Floats: Downcast float64 to float32 where precision is maintained
        - Categorical: Convert strings with <50% unique values to category type

        Args:
            df: Input DataFrame to optimize

        Returns:
            DataFrame with optimized data types
        """
        df_opt = df.copy()

        for col in df_opt.columns:
            col_data = df_opt[col]

            # Skip date columns
            if col.lower() in [
                "date",
                "timestamp",
            ] or pd.api.types.is_datetime64_any_dtype(col_data):
                continue

            # Optimize numeric columns
            if pd.api.types.is_numeric_dtype(col_data):
                # Check if it's actually integers
                if col_data.dtype in ["float64", "float32"]:
                    # Check if all values are integers (no decimal part)
                    if col_data.notna().all() and (col_data % 1 == 0).all():
                        # Convert to smallest possible integer type
                        col_min, col_max = col_data.min(), col_data.max()
                        if col_min >= 0:
                            if col_max <= 255:
                                df_opt[col] = col_data.astype("uint8")
                            elif col_max <= 65535:
                                df_opt[col] = col_data.astype("uint16")
                            elif col_max <= 4294967295:
                                df_opt[col] = col_data.astype("uint32")
                            else:
                                df_opt[col] = col_data.astype("uint64")
                        else:
                            if col_min >= -128 and col_max <= 127:
                                df_opt[col] = col_data.astype("int8")
                            elif col_min >= -32768 and col_max <= 32767:
                                df_opt[col] = col_data.astype("int16")
                            elif (
                                col_min >= -2147483648 and col_max <= 2147483647
                            ):
                                df_opt[col] = col_data.astype("int32")
                            else:
                                df_opt[col] = col_data.astype("int64")
                    else:
                        # Keep as float but optimize precision
                        if col_data.dtype == "float64":
                            # Check if float32 is sufficient
                            if (col_data.astype("float32") == col_data).all():
                                df_opt[col] = col_data.astype("float32")

            # Optimize string/categorical columns
            elif pd.api.types.is_object_dtype(col_data):
                # Convert to category if few unique values
                unique_ratio = col_data.nunique() / len(col_data)
                if unique_ratio < 0.5:  # Less than 50% unique values
                    df_opt[col] = col_data.astype("category")

        memory_reduction = (
            1
            - df_opt.memory_usage(deep=True).sum()
            / df.memory_usage(deep=True).sum()
        ) * 100

        logger.info(f"Memory usage reduced by {memory_reduction:.1f}%")
        return df_opt

    def upload_to_gcs(self, data_buffer: io.BytesIO, gcs_path: str) -> str:
        """
        Upload Parquet buffer to GCS.

        Args:
            data_buffer: BytesIO buffer containing Parquet data
            gcs_path: Destination path in GCS bucket (relative to bucket root)

        Returns:
            Full GCS URI (gs://bucket/path)
        """
        bucket = self.storage_client.bucket(self.gcs_bucket)
        blob = bucket.blob(gcs_path)

        data_buffer.seek(0)
        blob.upload_from_file(
            data_buffer, content_type="application/octet-stream"
        )

        logger.info(
            f"Uploaded Parquet file to gs://{self.gcs_bucket}/{gcs_path}"
        )
        return f"gs://{self.gcs_bucket}/{gcs_path}"

    def read_parquet_from_gcs(self, gcs_path: str) -> pd.DataFrame:
        """
        Read Parquet file from GCS.

        Args:
            gcs_path: Path to Parquet file in GCS bucket

        Returns:
            DataFrame loaded from Parquet file
        """
        bucket = self.storage_client.bucket(self.gcs_bucket)
        blob = bucket.blob(gcs_path)

        # Download to memory buffer
        buffer = io.BytesIO()
        blob.download_to_file(buffer)
        buffer.seek(0)

        # Read Parquet from buffer
        df = pd.read_parquet(buffer)

        logger.info(
            f"Loaded Parquet file from GCS: {len(df):,} rows, "
            f"{len(df.columns)} columns"
        )

        return df
