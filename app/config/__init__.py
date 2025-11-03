"""
Configuration package for MMM application.

Centralizes all configuration settings including:
- Environment variables
- GCP project settings
- Snowflake connection parameters
- Cloud Run settings
- Storage buckets
"""

from .settings import (  # GCP Settings; Cloud Run Settings; Storage Settings; Snowflake Settings; Queue Settings; Auth Settings; Job History
    ARTIFACT_REPO,
    DEFAULT_QUEUE_NAME,
    GCS_BUCKET,
    JOB_HISTORY_COLUMNS,
    PROJECT_ID,
    PROJECT_NUMBER,
    QUEUE_PARAM_COLUMNS,
    QUEUE_ROOT,
    REGION,
    SA_EMAIL,
    SAFE_LAG_SECONDS_AFTER_RUNNING,
    SERVICE_NAME,
    TRAINING_JOB_NAME,
    TRAINING_RUNTIME_SA,
    WEB_RUNTIME_SA,
    WIF_POOL,
    WIF_PROVIDER,
    get_snowflake_config,
)

__all__ = [
    "PROJECT_ID",
    "PROJECT_NUMBER",
    "REGION",
    "TRAINING_JOB_NAME",
    "SERVICE_NAME",
    "GCS_BUCKET",
    "ARTIFACT_REPO",
    "get_snowflake_config",
    "QUEUE_ROOT",
    "DEFAULT_QUEUE_NAME",
    "SAFE_LAG_SECONDS_AFTER_RUNNING",
    "WIF_POOL",
    "WIF_PROVIDER",
    "SA_EMAIL",
    "WEB_RUNTIME_SA",
    "TRAINING_RUNTIME_SA",
    "JOB_HISTORY_COLUMNS",
    "QUEUE_PARAM_COLUMNS",
]
