#!/usr/bin/env python3
"""Run a full MMM benchmark from a local CSV file.

This script is a thin wrapper around ``run_full_benchmark.py`` that takes a
local CSV file as raw data instead of an existing GCS selected_columns.json.
It:

1. Reads the CSV, optionally filters rows to a single market/country name.
2. Uploads the data as Parquet to GCS at
   ``mapped-datasets/{country_code}/{timestamp}/raw.parquet``
   (and syncs the ``latest`` pointer).
3. Determines column assignments via one of two modes:

   **Mapping-file mode** (default) — reads a curated ``selected_columns.json``-
   style JSON file (see ``--columns-mapping``).  By default
   ``data/dk/dk_selected_columns_mapping_v2_clean.json`` is used when the
   country-code is ``dk``.  These mapping files live in ``data/dk/`` and are
   also uploaded to GCS under ``benchmarks/dk/`` for reference.

   **Auto-classify mode** (``--no-mapping``) — heuristically classifies all
   CSV columns into Robyn categories based on naming patterns:

   * **paid_media_spends** – hardcoded 10-channel list for this dataset.
   * **paid_media_vars** – defaults to the same list as paid_media_spends.
   * **context_vars** – continuous demand-drivers (weather, fleet, pricing…).
   * **factor_vars** – binary indicator columns (``IS_*``, ``PMAX_LOCAL``).
   * **organic_vars** – CRM email channel metrics (``CRM_EMAIL_*``).

4. Writes ``selected_columns.json`` and uploads it to GCS at
   ``training_data/{country_code}/{goal}/{timestamp}/selected_columns.json``.
5. Calls ``run_full_benchmark.py --path gs://... <extra_args>`` so that the
   full benchmark pipeline runs exactly as if you had created the config
   through the Streamlit UI.

Usage
-----
    # Test run using default mapping (dk_selected_columns_mapping_v2_clean.json)
    python scripts/run_benchmark_from_csv.py \\
        --csv data/dk/mmm_data_v2_final_holidays_and_school.csv

    # Explicit mapping file
    python scripts/run_benchmark_from_csv.py \\
        --csv data/dk/mmm_data_v2_final_holidays_and_school.csv \\
        --columns-mapping data/dk/dk_context_supply_plus_occ7d_clean.json

    # Auto-classify columns instead of using a mapping file
    python scripts/run_benchmark_from_csv.py \\
        --csv data/dk/mmm_data_v2_final_holidays_and_school.csv --no-mapping

    # Standard run with all extra flags forwarded to run_full_benchmark.py
    python scripts/run_benchmark_from_csv.py \\
        --csv data/dk/mmm_data_v2_final_holidays_and_school.csv \\
        --full-run --all-adstock --queue-name default-dev

    # Choose a different goal (dependent variable)
    python scripts/run_benchmark_from_csv.py \\
        --csv data/dk/mmm_data_v2_final_holidays_and_school.csv \\
        --goal GMV_NET_EUR --dep-var-type revenue --full-run

    # Skip queue processing (only submit benchmark, do not wait for results)
    python scripts/run_benchmark_from_csv.py \\
        --csv data/dk/mmm_data_v2_final_holidays_and_school.csv \\
        --full-run --skip-queue

    # TV + radio channels — test/dev run (fast: 100 iter, 1 trial, submit only)
    python scripts/run_benchmark_from_csv.py \\
        --csv data/dk/mmm_data_v2_with_tv.csv \\
        --columns-mapping benchmark_analysis/dk_json_configs_clean/dk_final_with_tv_config.json \\
        --full-run --queue-name default-dev \\
        --iterations 100 --trials 1 --skip-queue

    # TV + radio channels — full production run
    python scripts/run_benchmark_from_csv.py \\
        --csv data/dk/mmm_data_v2_with_tv.csv \\
        --columns-mapping benchmark_analysis/dk_json_configs_clean/dk_final_with_tv_config.json \\
        --full-run --queue-name default

    # TV + radio channels — submit without waiting for results
    python scripts/run_benchmark_from_csv.py \\
        --csv data/dk/mmm_data_v2_with_tv.csv \\
        --columns-mapping benchmark_analysis/dk_json_configs_clean/dk_final_with_tv_config.json \\
        --full-run --queue-name default-dev --skip-queue
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Optional heavy imports – pandas and pyarrow are required for CSV→Parquet
# ---------------------------------------------------------------------------
try:
    import pandas as pd
except ImportError as exc:
    sys.exit(
        f"pandas is required: pip install pandas\n(original error: {exc})"
    )

try:
    import pyarrow as pa  # noqa: F401 – imported to verify availability
    import pyarrow.parquet as pq
except ImportError as exc:
    sys.exit(
        f"pyarrow is required: pip install pyarrow\n(original error: {exc})"
    )

# Add app directory to Python path so GCS utils are importable
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from google.cloud import storage

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROJECT_ID = os.getenv("PROJECT_ID", "datawarehouse-422511")
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")

# Repository root (two levels up from this script)
REPO_ROOT = Path(__file__).parent.parent

# Default mapping file used when country-code is 'dk' and no --columns-mapping
# flag is given.
DEFAULT_DK_MAPPING = (
    REPO_ROOT / "data" / "dk" / "dk_selected_columns_mapping_v2_clean.json"
)

# Directory that holds all DK column-mapping config files; its contents are
# uploaded to GCS at the start of every run.
DK_CONFIG_DIR = REPO_ROOT / "data" / "dk"
GCS_CONFIG_PREFIX = "benchmarks/dk"

# Column rename map: renames CSV columns so they match the curated DK mapping
# JSON files (which use supply/CRM terminology that differs from raw export).
# Applied to the DataFrame BEFORE uploading to GCS and before column
# classification, so the Parquet and selected_columns.json use consistent names.
COLUMN_RENAME_MAP: Dict[str, str] = {
    # Raw export name              →  Mapping JSON name
    "CRM_REACHABLE_AUDIENCE": "CRM_REACHABLE_USERS",
    "ACTIVE_VEHICLES": "FLEET_TOTAL_UNITS",
    "N_PARTNERS": "ACTIVE_PARTNER_COUNT",
    # Required by dk_context_expanded_test_clean.json (FLEET_AVAILABLE_UNITS)
    "ACTIVE_INCL_HIDDEN_VEHICLES": "FLEET_AVAILABLE_UNITS",
}

# Fallback paid-media spend columns used in auto-classify mode (--no-mapping).
# These are the 10 channels confirmed present in the Denmark CSV.
PAID_MEDIA_SPENDS: List[str] = [
    "GOOGLE_SEARCH_BRAND_COST",
    "GOOGLE_SEARCH_NONBRAND_COST_2",
    "GOOGLE_PMAX_COST_2",
    "GOOGLE_OTHER_COST_2",
    "BING_SEARCH_BRAND_COST",
    "BING_SEARCH_NONBRAND_COST",
    "BING_OTHER_COST",
    "FB_UPPER_COST",
    "FB_LOWER_COST",
    "FB_APP_COST",
]

# Columns that are never used as model variables (identifiers, free text, etc.)
_NON_MODELLING_COLS = frozenset(
    {
        "MARKET_NAME",
        "DATE",
        "HOLIDAY_NAMES",
        "WEATHER_CODE",
        "WEATHER_CONDITION",
        "WEATHER_BUCKET",
    }
)

# Suffixes that identify paid-media delivery metrics (impressions / clicks).
# These are NOT added to context_vars because the benchmark's spend_var_mapping
# dimension uses them as paid_media_vars overrides.
# NOTE: checked AFTER the organic-var test so that CRM_EMAIL_*_CLICKS columns
# are correctly classified as organic vars rather than media delivery metrics.
_MEDIA_METRIC_SUFFIXES = (
    "_IMPRESSIONS",
    "_CLICKS",
    "_IMPRESSIONS_2",
    "_CLICKS_2",
)

# Columns that aggregate totals across channels – typically redundant with
# their sub-channel counterparts already captured as spend or context columns.
_TOTAL_AGG_COLS = frozenset(
    {
        "FB_TOTAL_IMPRESSIONS",
        "FB_TOTAL_CLICKS",
        "GOOGLE_TOTAL_IMPRESSIONS",
        "GOOGLE_TOTAL_CLICKS",
        "GOOGLE_TOTAL_COST",
        "BING_TOTAL_IMPRESSIONS",
        "BING_TOTAL_CLICKS",
        "BING_TOTAL_COST",
        # FB_TOTAL_COST is used as a paid_media_spend, so NOT here
    }
)

# Potential dependent-variable columns – excluded from predictor sets
_POTENTIAL_DEP_VARS = frozenset(
    {"GMV_NET_EUR", "GMV_GROSS_EUR", "BOOKINGS", "UPLOAD_VALUE"}
)


# ---------------------------------------------------------------------------
# Column classification helpers
# ---------------------------------------------------------------------------


def _is_factor_var(col: str) -> bool:
    """Return True for binary indicator columns."""
    return col.startswith("IS_") or col == "PMAX_LOCAL"


def _is_organic_var(col: str) -> bool:
    """Return True for CRM e-mail channel columns."""
    return col.startswith("CRM_EMAIL_")


def _is_media_metric(col: str, paid_media_spends_set: frozenset) -> bool:
    """Return True for paid-media delivery metrics (impressions / clicks / cost).

    Any column ending with ``_COST`` that is not already in
    ``paid_media_spends_set`` is treated as a redundant spend variant and
    excluded from context_vars to avoid multicollinearity.  Impression and
    click columns are reserved for the benchmark's spend_var_mapping proxy
    tests.

    This check must be called AFTER ``_is_organic_var`` so that
    ``CRM_EMAIL_*_CLICKS`` columns are not mis-classified.
    """
    if any(col.endswith(s) for s in _MEDIA_METRIC_SUFFIXES):
        return True
    # Exclude non-selected cost variants (e.g. GOOGLE_SEARCH_BRAND_COST)
    if col.endswith("_COST") and col not in paid_media_spends_set:
        return True
    return False


def classify_columns(
    all_columns: List[str],
    paid_media_spends: List[str],
    dep_var: str,
) -> Dict[str, List[str]]:
    """Classify CSV columns into Robyn variable categories (auto-classify mode).

    Parameters
    ----------
    all_columns:
        All column names present in the CSV (uppercase).
    paid_media_spends:
        The hardcoded list of paid-media spend columns.
    dep_var:
        The dependent variable column name.

    Returns
    -------
    dict with keys: paid_media_spends, paid_media_vars, context_vars,
    factor_vars, organic_vars.
    """
    spends_set = frozenset(paid_media_spends)
    skip = (
        _NON_MODELLING_COLS
        | _TOTAL_AGG_COLS
        | _POTENTIAL_DEP_VARS
        | spends_set
        | {dep_var}
    )

    factor_vars: List[str] = []
    organic_vars: List[str] = []
    context_vars: List[str] = []

    for col in all_columns:
        if col in skip:
            continue
        if _is_factor_var(col):
            factor_vars.append(col)
        elif _is_organic_var(col):
            organic_vars.append(col)
        elif _is_media_metric(col, spends_set):
            # Paid-media delivery metrics: reserved for benchmark proxy mapping
            continue
        else:
            context_vars.append(col)

    # paid_media_vars defaults to paid_media_spends (spend→spend);
    # the benchmark's spend_var_mapping dimension tests proxy variants.
    present_spends = [s for s in paid_media_spends if s in set(all_columns)]
    paid_media_vars = present_spends

    logger.info("📊 Column classification summary (auto-classify mode):")
    logger.info(f"   paid_media_spends : {len(present_spends)} columns")
    logger.info(f"   paid_media_vars   : {len(paid_media_vars)} columns")
    logger.info(f"   context_vars      : {len(context_vars)} columns")
    logger.info(f"   factor_vars       : {len(factor_vars)} columns")
    logger.info(f"   organic_vars      : {len(organic_vars)} columns")

    return {
        "paid_media_spends": present_spends,
        "paid_media_vars": paid_media_vars,
        "var_to_spend_mapping": {},
        "context_vars": context_vars,
        "factor_vars": factor_vars,
        "organic_vars": organic_vars,
    }


def load_columns_from_mapping(
    mapping_path: Path,
    all_columns: List[str],
) -> Dict[str, List[str]]:
    """Load column assignments from a curated JSON mapping file.

    Parameters
    ----------
    mapping_path:
        Path to a ``selected_columns.json``-style JSON file (e.g.
        ``data/dk/dk_selected_columns_mapping_v2_clean.json``).
    all_columns:
        Column names present in the CSV (uppercase).  Used only for
        existence-checking and warning about missing columns.

    Returns
    -------
    dict with keys: paid_media_spends, paid_media_vars, var_to_spend_mapping,
    context_vars, factor_vars, organic_vars.
    """
    with open(mapping_path) as fh:
        mapping = json.load(fh)

    col_set = set(all_columns)

    def _filter(key: str) -> List[str]:
        cols = mapping.get(key) or []
        present = [c for c in cols if c in col_set]
        missing = [c for c in cols if c not in col_set]
        if missing:
            logger.warning(
                f"⚠️  {key}: {len(missing)} column(s) from mapping not found "
                f"in CSV and will be skipped: {missing}"
            )
        return present

    paid_media_spends = _filter("paid_media_spends")
    paid_media_vars = _filter("paid_media_vars")
    context_vars = _filter("context_vars")
    factor_vars = _filter("factor_vars")
    organic_vars = _filter("organic_vars")

    # Preserve var_to_spend_mapping entries only for columns that exist
    raw_vsm = mapping.get("var_to_spend_mapping") or {}
    var_to_spend_mapping = {
        k: v
        for k, v in raw_vsm.items()
        if k in col_set and v in col_set
    }

    logger.info(
        f"📊 Column classification summary "
        f"(mapping: {mapping_path.name}):"
    )
    logger.info(f"   paid_media_spends : {len(paid_media_spends)} columns")
    logger.info(f"   paid_media_vars   : {len(paid_media_vars)} columns")
    logger.info(f"   context_vars      : {len(context_vars)} columns")
    logger.info(f"   factor_vars       : {len(factor_vars)} columns")
    logger.info(f"   organic_vars      : {len(organic_vars)} columns")
    if mapping.get("name"):
        logger.info(f"   mapping name      : {mapping['name']}")
    if mapping.get("description"):
        logger.info(f"   description       : {mapping['description']}")

    return {
        "paid_media_spends": paid_media_spends,
        "paid_media_vars": paid_media_vars,
        "var_to_spend_mapping": var_to_spend_mapping,
        "context_vars": context_vars,
        "factor_vars": factor_vars,
        "organic_vars": organic_vars,
    }


# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------


def _gcs_upload_bytes(
    client: storage.Client,
    bucket_name: str,
    blob_path: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> None:
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(data, content_type=content_type)
    logger.info(f"   ✅ Uploaded: gs://{bucket_name}/{blob_path}")


def upload_parquet(
    df: "pd.DataFrame",
    client: storage.Client,
    bucket_name: str,
    country_code: str,
    timestamp: str,
) -> str:
    """Upload DataFrame as Parquet to GCS and return the versioned GCS path."""
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        tmp_path = tmp.name

    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), tmp_path)

    with open(tmp_path, "rb") as fh:
        parquet_bytes = fh.read()

    Path(tmp_path).unlink(missing_ok=True)

    versioned_path = (
        f"mapped-datasets/{country_code.lower()}/{timestamp}/raw.parquet"
    )
    latest_path = (
        f"mapped-datasets/{country_code.lower()}/latest/raw.parquet"
    )

    logger.info("📤 Uploading data to GCS…")
    _gcs_upload_bytes(
        client, bucket_name, versioned_path, parquet_bytes
    )
    # Keep a "latest" pointer so Run_Experiment.py can find the data
    _gcs_upload_bytes(client, bucket_name, latest_path, parquet_bytes)

    return f"gs://{bucket_name}/{versioned_path}"


def upload_selected_columns(
    config: Dict[str, Any],
    client: storage.Client,
    bucket_name: str,
    country_code: str,
    goal: str,
    timestamp: str,
) -> str:
    """Upload selected_columns.json to GCS and return the GCS path."""
    blob_path = (
        f"training_data/{country_code.lower()}/{goal}/{timestamp}"
        f"/selected_columns.json"
    )
    data = json.dumps(config, indent=2).encode("utf-8")
    logger.info("📤 Uploading selected_columns.json to GCS…")
    _gcs_upload_bytes(
        client, bucket_name, blob_path, data, content_type="application/json"
    )
    return f"gs://{bucket_name}/{blob_path}"

def upload_config_files(
    client: storage.Client,
    bucket_name: str,
    config_dir: Path,
    gcs_prefix: str,
) -> None:
    """Upload all JSON mapping files from *config_dir* to GCS.

    Files are stored at ``{gcs_prefix}/{filename}`` so they can be
    inspected or reused without checking the repository.
    """
    json_files = sorted(config_dir.glob("*.json"))
    if not json_files:
        logger.warning(f"⚠️  No JSON files found in {config_dir}")
        return

    logger.info(
        f"📤 Uploading {len(json_files)} config file(s) to "
        f"gs://{bucket_name}/{gcs_prefix}/"
    )
    for json_file in json_files:
        blob_path = f"{gcs_prefix}/{json_file.name}"
        data = json_file.read_bytes()
        _gcs_upload_bytes(
            client, bucket_name, blob_path, data,
            content_type="application/json",
        )





def _make_timestamp() -> str:
    """Return a ``YYYYMMDD_HHMMSS`` timestamp in CET."""
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+

        now = datetime.now(ZoneInfo("Europe/Paris"))
    except ImportError:
        now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a local CSV file for MMM benchmarking and launch "
            "run_full_benchmark.py."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # --- wrapper-specific arguments ---
    parser.add_argument(
        "--csv",
        required=True,
        metavar="PATH",
        help="Path to the local CSV file containing the raw marketing data.",
    )
    parser.add_argument(
        "--country-name",
        default="Denmark",
        metavar="NAME",
        help=(
            "Value of the MARKET_NAME column to filter for "
            "(default: Denmark). Case-insensitive."
        ),
    )
    parser.add_argument(
        "--country-code",
        default="dk",
        metavar="CODE",
        help=(
            "Two-letter country code used for GCS paths and Robyn "
            "(default: dk)."
        ),
    )
    parser.add_argument(
        "--goal",
        default="BOOKINGS",
        metavar="COLUMN",
        help=(
            "Dependent variable column name (default: BOOKINGS). "
            "Must exist in the CSV. Other common options: "
            "GMV_NET_EUR, GMV_GROSS_EUR."
        ),
    )
    parser.add_argument(
        "--dep-var-type",
        default="revenue",
        choices=["revenue", "conversion"],
        help="Robyn dep_var_type (default: revenue).",
    )
    parser.add_argument(
        "--bucket",
        default=GCS_BUCKET,
        metavar="BUCKET",
        help=f"GCS bucket name (default: {GCS_BUCKET}).",
    )

    mapping_group = parser.add_mutually_exclusive_group()
    mapping_group.add_argument(
        "--columns-mapping",
        metavar="PATH",
        default=None,
        help=(
            "Path to a JSON mapping file that specifies paid_media_spends, "
            "paid_media_vars, var_to_spend_mapping, organic_vars, "
            "context_vars, and factor_vars directly (e.g. "
            "data/dk/dk_selected_columns_mapping_v2_clean.json). "
            "When country-code is 'dk' and this flag is omitted, "
            "data/dk/dk_selected_columns_mapping_v2_clean.json is used "
            "by default."
        ),
    )
    mapping_group.add_argument(
        "--no-mapping",
        action="store_true",
        default=False,
        help=(
            "Disable mapping-file mode and fall back to auto-classifying "
            "columns from naming patterns."
        ),
    )

    # Collect any remaining args to forward verbatim to run_full_benchmark.py
    args, extra_args = parser.parse_known_args()

    # ------------------------------------------------------------------
    # 1. Load and validate CSV
    # ------------------------------------------------------------------
    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"❌ CSV file not found: {csv_path}")

    logger.info(f"📂 Loading CSV: {csv_path}")
    df = pd.read_csv(csv_path)

    # Normalize column names to uppercase
    df.columns = [c.upper() for c in df.columns]

    # Drop duplicate columns introduced by uppercasing (e.g. a CSV may contain
    # both 'IS_HOLIDAY' and 'is_holiday' which collapse to the same name).
    # pyarrow raises ValueError on duplicate column names, so we keep the first
    # occurrence of each name.
    n_before = len(df.columns)
    df = df.loc[:, ~df.columns.duplicated()]
    n_dropped = n_before - len(df.columns)
    if n_dropped:
        logger.info(
            f"   Dropped {n_dropped} duplicate column(s) after uppercasing"
        )

    logger.info(
        f"   Loaded {len(df):,} rows × {len(df.columns)} columns"
    )

    # Apply column renames so the uploaded Parquet uses the same names as the
    # curated mapping JSON files (e.g. CRM_REACHABLE_AUDIENCE → CRM_REACHABLE_USERS)
    rename_applied = {
        old: new
        for old, new in COLUMN_RENAME_MAP.items()
        if old in df.columns
    }
    if rename_applied:
        df.rename(columns=rename_applied, inplace=True)
        logger.info(
            f"   Renamed {len(rename_applied)} column(s) to match mapping files:"
        )
        for old, new in rename_applied.items():
            logger.info(f"     {old} → {new}")

    # Clip media/spend columns to 0 — floating-point precision can produce
    # tiny negative values (e.g. -2.84e-14) that cause Robyn to reject the
    # data with "Media must be >=0".
    media_cols = [
        c
        for c in df.columns
        if any(kw in c for kw in ("COST", "SPEND", "CLICKS", "IMPRESSIONS"))
    ]
    if media_cols:
        df[media_cols] = df[media_cols].clip(lower=0)
        logger.info(
            f"   Clipped {len(media_cols)} media column(s) to min=0 "
            f"(removes floating-point noise)"
        )

    # Filter to the requested country
    if "MARKET_NAME" in df.columns:
        original_rows = len(df)
        available_markets = sorted(
            df["MARKET_NAME"].dropna().unique().tolist()
        )
        df = df[
            df["MARKET_NAME"].str.strip().str.upper()
            == args.country_name.upper()
        ].copy()
        logger.info(
            f"   Filtered to '{args.country_name}': "
            f"{len(df):,} rows (from {original_rows:,})"
        )
        if df.empty:
            sys.exit(
                f"❌ No rows found for MARKET_NAME='{args.country_name}'. "
                f"Available values: {available_markets}"
            )
    else:
        logger.warning(
            "⚠️  MARKET_NAME column not found – using all rows without "
            "country filtering."
        )

    # Validate dep_var
    dep_var = args.goal.upper()
    if dep_var not in df.columns:
        sys.exit(
            f"❌ Dependent variable '{dep_var}' not found in CSV columns. "
            f"Available: {sorted(df.columns.tolist())}"
        )

    # ------------------------------------------------------------------
    # 2. Infer date range
    # ------------------------------------------------------------------
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    if "DATE" in df.columns:
        dates = pd.to_datetime(df["DATE"], errors="coerce").dropna()
        if not dates.empty:
            start_date = dates.min().strftime("%Y-%m-%d")
            end_date = dates.max().strftime("%Y-%m-%d")
            logger.info(
                f"   Date range: {start_date} → {end_date} "
                f"({len(dates):,} valid dates)"
            )

    # ------------------------------------------------------------------
    # 3. Classify columns
    # ------------------------------------------------------------------
    all_cols = df.columns.tolist()

    if args.no_mapping:
        # Explicit auto-classify mode
        classification = classify_columns(all_cols, PAID_MEDIA_SPENDS, dep_var)
        mapping_source = "auto-classify"
    else:
        # Resolve mapping file path
        if args.columns_mapping:
            mapping_path = Path(args.columns_mapping)
            if not mapping_path.is_absolute():
                mapping_path = REPO_ROOT / mapping_path
        elif args.country_code.lower() == "dk" and DEFAULT_DK_MAPPING.exists():
            mapping_path = DEFAULT_DK_MAPPING
            logger.info(
                f"📄 Using default DK mapping: {mapping_path.name}"
            )
        else:
            mapping_path = None

        if mapping_path is not None:
            if not mapping_path.exists():
                sys.exit(f"❌ Mapping file not found: {mapping_path}")
            classification = load_columns_from_mapping(mapping_path, all_cols)
            mapping_source = str(mapping_path)
        else:
            # No mapping file available → fall back to auto-classify
            logger.info(
                "ℹ️  No mapping file found; falling back to auto-classify mode."
            )
            classification = classify_columns(
                all_cols, PAID_MEDIA_SPENDS, dep_var
            )
            mapping_source = "auto-classify"

    # ------------------------------------------------------------------
    # 4. Build selected_columns.json
    # ------------------------------------------------------------------
    timestamp = _make_timestamp()

    all_drivers = list(
        dict.fromkeys(  # deduplicate while preserving order
            classification["paid_media_spends"]
            + classification["paid_media_vars"]
            + classification["organic_vars"]
            + classification["context_vars"]
            + classification["factor_vars"]
        )
    )

    selected_columns: Dict[str, Any] = {
        "country": args.country_code.lower(),
        "selected_goal": dep_var,
        "dep_var": dep_var,
        "dep_var_type": args.dep_var_type,
        "date_var": "date",
        "paid_media_spends": classification["paid_media_spends"],
        "paid_media_vars": classification["paid_media_vars"],
        "var_to_spend_mapping": classification.get("var_to_spend_mapping", {}),
        "organic_vars": classification["organic_vars"],
        "context_vars": classification["context_vars"],
        "factor_vars": classification["factor_vars"],
        "all_selected_drivers": all_drivers,
        "data_version": timestamp,
        "meta_version": "Latest",
        "timestamp": timestamp,
    }
    if start_date:
        selected_columns["start_date"] = start_date
    if end_date:
        selected_columns["end_date"] = end_date

    logger.info("📋 selected_columns.json preview:")
    logger.info(f"   country           : {selected_columns['country']}")
    logger.info(f"   selected_goal     : {selected_columns['selected_goal']}")
    logger.info(
        f"   start_date        : {selected_columns.get('start_date', '(not set)')}"
    )
    logger.info(
        f"   end_date          : {selected_columns.get('end_date', '(not set)')}"
    )
    logger.info(
        f"   paid_media_spends ({len(selected_columns['paid_media_spends'])}): "
        f"{selected_columns['paid_media_spends']}"
    )
    logger.info(
        f"   paid_media_vars   ({len(selected_columns['paid_media_vars'])}): "
        f"{selected_columns['paid_media_vars']}"
    )
    logger.info(f"   mapping source    : {mapping_source}")

    # ------------------------------------------------------------------
    # 5. Upload data + configs to GCS
    # ------------------------------------------------------------------
    logger.info(
        f"🔗 Connecting to GCS project={PROJECT_ID}, bucket={args.bucket}"
    )
    gcs_client = storage.Client(project=PROJECT_ID)

    # Upload JSON config files from data/dk/ to benchmarks/dk/ in GCS
    if DK_CONFIG_DIR.exists() and args.country_code.lower() == "dk":
        upload_config_files(
            gcs_client, args.bucket, DK_CONFIG_DIR, GCS_CONFIG_PREFIX
        )

    upload_parquet(df, gcs_client, args.bucket, args.country_code, timestamp)

    gcs_config_path = upload_selected_columns(
        selected_columns,
        gcs_client,
        args.bucket,
        args.country_code,
        dep_var.lower(),
        timestamp,
    )

    logger.info(f"✅ GCS config path: {gcs_config_path}")
    logger.info("")

    # ------------------------------------------------------------------
    # 6. Delegate to run_full_benchmark.py
    # ------------------------------------------------------------------
    script = str(Path(__file__).parent / "run_full_benchmark.py")

    # Automatically inject --hyperparameter-ranges-config and
    # --channel-type-assignments-config when the repo's standard files
    # exist and the caller hasn't already supplied those flags.  Without
    # the ranges config, all preset variants (balanced / fb / meshed) fall
    # back to the same default ranges in the R script and produce identical
    # results, making the preset comparison meaningless.
    auto_flags: list = []
    HP_RANGES_FLAG = "--hyperparameter-ranges-config"
    CT_ASSIGNMENTS_FLAG = "--channel-type-assignments-config"

    default_hp_ranges = REPO_ROOT / "benchmarks" / "generic_hyperparameter_ranges_v2.json"
    default_ct_assign = REPO_ROOT / "benchmarks" / "channel_type_assignments.json"

    if HP_RANGES_FLAG not in extra_args and default_hp_ranges.exists():
        auto_flags += [HP_RANGES_FLAG, str(default_hp_ranges)]
        logger.info(
            f"ℹ️  Auto-injecting {HP_RANGES_FLAG} "
            f"(ensures presets produce distinct results)"
        )

    if CT_ASSIGNMENTS_FLAG not in extra_args and default_ct_assign.exists():
        auto_flags += [CT_ASSIGNMENTS_FLAG, str(default_ct_assign)]
        logger.info(
            f"ℹ️  Auto-injecting {CT_ASSIGNMENTS_FLAG}"
        )

    cmd = (
        [sys.executable, script, "--path", gcs_config_path]
        + auto_flags
        + extra_args
    )

    logger.info("=" * 70)
    logger.info("DELEGATING TO run_full_benchmark.py")
    logger.info("=" * 70)
    logger.info(f"Command: {' '.join(cmd)}")
    logger.info("")

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
