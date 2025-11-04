"""
Centralized settings module for the MMM application.

This module consolidates all configuration settings that were previously
scattered across multiple files. It provides a single source of truth for:
- Environment variables
- GCP project configuration
- Snowflake connection parameters
- Cloud Run job settings
- Storage bucket configuration
- Queue management settings
- Authentication settings
"""

import os
from typing import Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# GCP Project Settings
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ID: Optional[str] = os.getenv("PROJECT_ID")
"""GCP Project ID"""

PROJECT_NUMBER: Optional[str] = os.getenv("PROJECT_NUMBER")
"""GCP Project Number"""

REGION: str = os.getenv("REGION", "europe-west1")
"""GCP Region for resources"""

# ─────────────────────────────────────────────────────────────────────────────
# Cloud Run Settings
# ─────────────────────────────────────────────────────────────────────────────

SERVICE_NAME: Optional[str] = os.getenv("SERVICE_NAME")
"""Cloud Run service name"""

TRAINING_JOB_NAME: Optional[str] = os.getenv("TRAINING_JOB_NAME")
"""Cloud Run job name for training"""

# ─────────────────────────────────────────────────────────────────────────────
# Storage Settings
# ─────────────────────────────────────────────────────────────────────────────

GCS_BUCKET: str = os.getenv("GCS_BUCKET", "mmm-app-output")
"""Default GCS bucket for application data"""

ARTIFACT_REPO: str = os.getenv("ARTIFACT_REPO", "mmm-repo")
"""Artifact Registry repository name"""

# ─────────────────────────────────────────────────────────────────────────────
# Snowflake Settings
# ─────────────────────────────────────────────────────────────────────────────

SF_USER: Optional[str] = os.getenv("SF_USER")
"""Snowflake username"""

SF_ACCOUNT: Optional[str] = os.getenv("SF_ACCOUNT")
"""Snowflake account identifier"""

SF_WAREHOUSE: Optional[str] = os.getenv("SF_WAREHOUSE")
"""Snowflake warehouse name"""

SF_DATABASE: Optional[str] = os.getenv("SF_DATABASE")
"""Snowflake database name"""

SF_SCHEMA: Optional[str] = os.getenv("SF_SCHEMA")
"""Snowflake schema name"""

SF_ROLE: Optional[str] = os.getenv("SF_ROLE")
"""Snowflake role name"""

SF_PASSWORD: Optional[str] = os.getenv("SF_PASSWORD")
"""Snowflake password (fallback if key-pair auth not available)"""

SF_PRIVATE_KEY_SECRET: str = os.getenv(
    "SF_PRIVATE_KEY_SECRET", "sf-private-key"
)
"""Secret Manager secret ID for Snowflake private key"""

SF_PERSISTENT_KEY_SECRET: str = os.getenv(
    "SF_PERSISTENT_KEY_SECRET", "sf-private-key-persistent"
)
"""Secret Manager secret ID for persistent Snowflake private key"""


def get_snowflake_config() -> Optional[Dict[str, str]]:
    """
    Get Snowflake configuration from environment variables.

    Returns:
        Dictionary with Snowflake connection parameters, or None if not configured.
    """
    if not all([SF_USER, SF_ACCOUNT, SF_WAREHOUSE, SF_DATABASE, SF_SCHEMA]):
        return None

    return {
        "user": SF_USER,
        "account": SF_ACCOUNT,
        "warehouse": SF_WAREHOUSE,
        "database": SF_DATABASE,
        "schema": SF_SCHEMA,
        "role": SF_ROLE,
        "password": SF_PASSWORD,
        "private_key_secret": SF_PRIVATE_KEY_SECRET,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Queue Settings
# ─────────────────────────────────────────────────────────────────────────────

QUEUE_ROOT: str = os.getenv("QUEUE_ROOT", "robyn-queues")
"""Root path for queue storage in GCS"""

DEFAULT_QUEUE_NAME: str = os.getenv("DEFAULT_QUEUE_NAME", "default")
"""Default queue name"""

SAFE_LAG_SECONDS_AFTER_RUNNING: int = int(
    os.getenv("SAFE_LAG_SECONDS_AFTER_RUNNING", "5")
)
"""Seconds to wait after launching a job before checking status"""

# ─────────────────────────────────────────────────────────────────────────────
# Authentication Settings
# ─────────────────────────────────────────────────────────────────────────────

WIF_POOL: str = os.getenv("WIF_POOL", "github-pool")
"""Workload Identity Federation pool name"""

WIF_PROVIDER: str = os.getenv("WIF_PROVIDER", "github-oidc")
"""Workload Identity Federation provider name"""

SA_EMAIL: Optional[str] = os.getenv("SA_EMAIL")
"""Service account email for deployment"""

WEB_RUNTIME_SA: Optional[str] = os.getenv("WEB_RUNTIME_SA")
"""Service account for web service runtime"""

TRAINING_RUNTIME_SA: Optional[str] = os.getenv("TRAINING_RUNTIME_SA")
"""Service account for training job runtime"""

# ─────────────────────────────────────────────────────────────────────────────
# Job History Schema
# ─────────────────────────────────────────────────────────────────────────────

# Columns that come from the Queue Builder / params
QUEUE_PARAM_COLUMNS: List[str] = [
    "country",
    "revision",
    "date_input",
    "iterations",
    "trials",
    "train_size",
    "paid_media_spends",
    "paid_media_vars",
    "context_vars",
    "factor_vars",
    "organic_vars",
    "gcs_bucket",
    "table",
    "query",
    "dep_var",
    "date_var",
    "adstock",
    "annotations_gcs_path",
]

# Canonical job_history schema (builder params + exec/info)
JOB_HISTORY_COLUMNS: List[str] = (
    ["job_id", "state"]
    + QUEUE_PARAM_COLUMNS
    + [
        "start_time",
        "end_time",
        "duration_minutes",
        "gcs_prefix",
        "bucket",
        "exec_name",
        "execution_name",
        "message",
    ]
)

# ─────────────────────────────────────────────────────────────────────────────
# Application Settings
# ─────────────────────────────────────────────────────────────────────────────

KEEPALIVE_SECONDS: int = 10 * 60
"""Snowflake connection keepalive interval in seconds (10 minutes)"""

JOB_HISTORY_OBJECT: str = os.getenv(
    "JOBS_JOB_HISTORY_OBJECT", "robyn-jobs/job_history.csv"
)
"""GCS path for job history CSV file"""

# ─────────────────────────────────────────────────────────────────────────────
# OAuth Settings (for Streamlit authentication)
# ─────────────────────────────────────────────────────────────────────────────

AUTH_CLIENT_ID: Optional[str] = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
"""Google OAuth client ID"""

AUTH_CLIENT_SECRET: Optional[str] = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
"""Google OAuth client secret"""

AUTH_COOKIE_SECRET: Optional[str] = os.getenv("STREAMLIT_COOKIE_SECRET")
"""Streamlit cookie secret for session management"""

ALLOWED_DOMAINS_RAW: str = os.getenv("ALLOWED_DOMAINS", "mesheddata.com")
"""Comma-separated list of allowed email domains for authentication"""

ALLOWED_DOMAINS: list[str] = [
    domain.strip().lower()
    for domain in ALLOWED_DOMAINS_RAW.split(",")
    if domain.strip()
]
"""List of allowed email domains for authentication (parsed from ALLOWED_DOMAINS env var)"""

# Backward compatibility: support single ALLOWED_DOMAIN env var
if os.getenv("ALLOWED_DOMAIN"):
    _legacy_domain = os.getenv("ALLOWED_DOMAIN", "").strip().lower()
    if _legacy_domain and _legacy_domain not in ALLOWED_DOMAINS:
        ALLOWED_DOMAINS.append(_legacy_domain)
