#!/usr/bin/env python3
"""Upload DK benchmark data and configs to GCS, then print the run command.

This script is the minimal setup step you need to run before calling
``run_full_benchmark.py --path``.  It:

1. Reads and filters ``mmm_data_v2_final_holidays_and_school.csv`` for Denmark.
2. Renames columns so they match the curated JSON mapping files.
3. Uploads the data as Parquet to GCS at
   ``mapped-datasets/dk/{timestamp}/raw.parquet`` (and ``latest`` pointer).
4. Takes a column-mapping JSON from ``data/dk/`` and writes it as
   ``training_data/dk/{goal}/{timestamp}/selected_columns.json`` to GCS,
   with ``dep_var``, ``dep_var_type``, ``date_var``, ``start_date``, and
   ``end_date`` fields added.
5. Uploads all JSON configs from ``data/dk/`` to ``benchmarks/dk/`` in GCS.
6. Prints the exact ``python scripts/run_full_benchmark.py --path ...`` command
   ready to copy-paste.

Usage
-----
    # Upload with default mapping (dk_selected_columns_mapping_v2_clean.json)
    python scripts/setup_dk_benchmark.py

    # Upload with a specific context variant mapping
    python scripts/setup_dk_benchmark.py \\
        --mapping data/dk/dk_context_supply_plus_occ7d_clean.json

    # Upload all six context variants in one go (prints one --path per variant)
    python scripts/setup_dk_benchmark.py --all-variants

    # Choose a different dependent variable
    python scripts/setup_dk_benchmark.py --goal GMV_NET_EUR
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pandas as pd
except ImportError as exc:
    sys.exit(f"pandas is required: pip install pandas\n({exc})")

try:
    import pyarrow as pa  # noqa: F401
    import pyarrow.parquet as pq
except ImportError as exc:
    sys.exit(f"pyarrow is required: pip install pyarrow\n({exc})")

from google.cloud import storage

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
PROJECT_ID = os.getenv("PROJECT_ID", "datawarehouse-422511")
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")

DEFAULT_CSV = (
    REPO_ROOT / "data" / "dk" / "mmm_data_v2_final_holidays_and_school.csv"
)
DK_CONFIG_DIR = REPO_ROOT / "data" / "dk"
DEFAULT_MAPPING = DK_CONFIG_DIR / "dk_selected_columns_mapping_v2_clean.json"

# Context-variant mapping files in recommended test order
CONTEXT_VARIANTS: List[Path] = [
    DK_CONFIG_DIR / "dk_context_minimal_clean.json",
    DK_CONFIG_DIR / "dk_context_supply_clean.json",
    DK_CONFIG_DIR / "dk_context_supply_plus_occ7d_clean.json",
    DK_CONFIG_DIR / "dk_context_occ_current_test_clean.json",
    DK_CONFIG_DIR / "dk_context_expanded_test_clean.json",
    DK_CONFIG_DIR / "dk_context_occ30d_test_clean.json",
]

# Rename raw CSV columns to match the curated mapping JSON names
COLUMN_RENAME_MAP: Dict[str, str] = {
    "CRM_REACHABLE_AUDIENCE": "CRM_REACHABLE_USERS",
    "ACTIVE_VEHICLES": "FLEET_TOTAL_UNITS",
    "N_PARTNERS": "ACTIVE_PARTNER_COUNT",
    "ACTIVE_INCL_HIDDEN_VEHICLES": "FLEET_AVAILABLE_UNITS",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_timestamp() -> str:
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+

        now = datetime.now(ZoneInfo("Europe/Paris"))
    except ImportError:
        now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d_%H%M%S")


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
    logger.info(f"   ✅ gs://{bucket_name}/{blob_path}")


def load_and_prepare_csv(
    csv_path: Path,
    country_name: str = "Denmark",
) -> "pd.DataFrame":
    """Load CSV, uppercase columns, apply renames, filter to country."""
    logger.info(f"📂 Loading CSV: {csv_path}")
    df = pd.read_csv(csv_path)
    df.columns = [c.upper() for c in df.columns]
    logger.info(f"   Raw: {len(df):,} rows × {len(df.columns)} columns")

    # Drop any duplicate column names present in the raw CSV (keep last
    # occurrence so the most-recently-added column wins).
    dupes = [c for c in df.columns if df.columns.tolist().count(c) > 1]
    if dupes:
        unique_dupes = sorted(set(dupes))
        logger.warning(
            f"   ⚠️  Duplicate column(s) in raw CSV: {unique_dupes} — "
            "keeping last occurrence of each"
        )
        df = df.loc[:, ~df.columns.duplicated(keep="last")]

    # Apply column renames — skip if target already exists (new CSV has both
    # old and new names; prefer the already-correctly-named column and drop
    # the legacy one to avoid duplicate column errors in PyArrow).
    rename_applied: Dict[str, str] = {}
    drop_legacy: List[str] = []
    for old, new in COLUMN_RENAME_MAP.items():
        if old not in df.columns:
            continue
        if new in df.columns:
            # Target already present — drop the legacy column instead
            drop_legacy.append(old)
            logger.info(
                f"   Skipping rename {old} → {new} (target already exists);"
                f" dropping legacy column '{old}'"
            )
        else:
            rename_applied[old] = new
    if drop_legacy:
        df.drop(columns=drop_legacy, inplace=True)
    if rename_applied:
        df.rename(columns=rename_applied, inplace=True)
        logger.info(f"   Renamed {len(rename_applied)} column(s):")
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

    # Filter to country
    if "MARKET_NAME" in df.columns:
        original = len(df)
        df = df[
            df["MARKET_NAME"].str.strip().str.upper() == country_name.upper()
        ].copy()
        logger.info(
            f"   Filtered to '{country_name}': {len(df):,} rows (from {original:,})"
        )
        if df.empty:
            available = sorted(df["MARKET_NAME"].dropna().unique().tolist())
            sys.exit(
                f"❌ No rows for MARKET_NAME='{country_name}'. "
                f"Available: {available}"
            )

    return df


def upload_parquet(
    df: "pd.DataFrame",
    client: storage.Client,
    bucket_name: str,
    country_code: str,
    timestamp: str,
) -> str:
    """Upload DataFrame as Parquet, return versioned GCS path."""
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        tmp_path = tmp.name

    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), tmp_path)
    with open(tmp_path, "rb") as fh:
        parquet_bytes = fh.read()
    Path(tmp_path).unlink(missing_ok=True)

    versioned = f"mapped-datasets/{country_code}/{timestamp}/raw.parquet"
    latest = f"mapped-datasets/{country_code}/latest/raw.parquet"

    logger.info("📤 Uploading Parquet data…")
    _gcs_upload_bytes(client, bucket_name, versioned, parquet_bytes)
    _gcs_upload_bytes(client, bucket_name, latest, parquet_bytes)
    return f"gs://{bucket_name}/{versioned}"


def build_selected_columns(
    mapping_path: Path,
    df: "pd.DataFrame",
    dep_var: str,
    dep_var_type: str,
    timestamp: str,
    country_code: str,
) -> Dict[str, Any]:
    """Build a selected_columns.json dict from mapping + CSV metadata."""
    with open(mapping_path) as fh:
        mapping = json.load(fh)

    col_set = set(df.columns.tolist())

    def _filter(key: str) -> List[str]:
        cols = mapping.get(key) or []
        present = [c for c in cols if c in col_set]
        missing = [c for c in cols if c not in col_set]
        if missing:
            logger.warning(
                f"   ⚠️  {key}: missing {missing} (not in renamed CSV, skipped)"
            )
        return present

    paid_media_spends = _filter("paid_media_spends")
    paid_media_vars = _filter("paid_media_vars")
    organic_vars = _filter("organic_vars")
    context_vars = _filter("context_vars")
    factor_vars = _filter("factor_vars")

    raw_vsm = mapping.get("var_to_spend_mapping") or {}
    var_to_spend_mapping = {
        k: v for k, v in raw_vsm.items() if k in col_set and v in col_set
    }

    all_drivers = list(
        dict.fromkeys(
            paid_media_spends
            + paid_media_vars
            + organic_vars
            + context_vars
            + factor_vars
        )
    )

    # Infer date range from CSV
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    if "DATE" in df.columns:
        dates = pd.to_datetime(df["DATE"], errors="coerce").dropna()
        if not dates.empty:
            start_date = dates.min().strftime("%Y-%m-%d")
            end_date = dates.max().strftime("%Y-%m-%d")

    goal = mapping.get("selected_goal", dep_var)

    sc: Dict[str, Any] = {
        "country": country_code.lower(),
        "selected_goal": goal,
        "dep_var": goal,
        "dep_var_type": dep_var_type,
        "date_var": "date",
        "paid_media_spends": paid_media_spends,
        "paid_media_vars": paid_media_vars,
        "var_to_spend_mapping": var_to_spend_mapping,
        "organic_vars": organic_vars,
        "context_vars": context_vars,
        "factor_vars": factor_vars,
        "all_selected_drivers": all_drivers,
        "data_version": timestamp,
        "meta_version": "Latest",
        "timestamp": timestamp,
    }
    if start_date:
        sc["start_date"] = start_date
    if end_date:
        sc["end_date"] = end_date

    # Preserve descriptive metadata from the mapping file
    for meta_key in ("name", "description"):
        if mapping.get(meta_key):
            sc[meta_key] = mapping[meta_key]

    return sc


def upload_selected_columns(
    sc: Dict[str, Any],
    client: storage.Client,
    bucket_name: str,
    country_code: str,
    timestamp: str,
) -> str:
    """Upload selected_columns.json to GCS, return GCS path."""
    goal = sc["selected_goal"]
    blob_path = (
        f"training_data/{country_code}/{goal}/{timestamp}/selected_columns.json"
    )
    data = json.dumps(sc, indent=2).encode("utf-8")
    logger.info("📤 Uploading selected_columns.json…")
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
    """Upload all JSON files from config_dir to GCS."""
    json_files = sorted(config_dir.glob("*.json"))
    if not json_files:
        return
    logger.info(
        f"📤 Uploading {len(json_files)} config JSON(s) to "
        f"gs://{bucket_name}/{gcs_prefix}/"
    )
    for jf in json_files:
        blob_path = f"{gcs_prefix}/{jf.name}"
        _gcs_upload_bytes(
            client,
            bucket_name,
            blob_path,
            jf.read_bytes(),
            content_type="application/json",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def setup_variant(
    mapping_path: Path,
    df: "pd.DataFrame",
    client: storage.Client,
    bucket_name: str,
    country_code: str,
    dep_var: str,
    dep_var_type: str,
    timestamp: str,
) -> str:
    """Upload data + selected_columns for one mapping file variant.

    Returns the GCS path to selected_columns.json.
    """
    logger.info(f"\n📄 Variant: {mapping_path.name}")
    sc = build_selected_columns(
        mapping_path, df, dep_var, dep_var_type, timestamp, country_code
    )
    logger.info(
        f"   paid_media_spends : {len(sc['paid_media_spends'])} channels"
    )
    logger.info(f"   paid_media_vars   : {len(sc['paid_media_vars'])} channels")
    logger.info(f"   context_vars      : {len(sc['context_vars'])}")
    logger.info(f"   factor_vars       : {len(sc['factor_vars'])}")
    logger.info(f"   organic_vars      : {len(sc['organic_vars'])}")

    gcs_path = upload_selected_columns(
        sc, client, bucket_name, country_code, timestamp
    )
    return gcs_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Upload DK benchmark data to GCS and print the run_full_benchmark.py command."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--csv",
        default=str(DEFAULT_CSV),
        metavar="PATH",
        help=f"Path to the raw DK CSV file (default: {DEFAULT_CSV.name}).",
    )
    parser.add_argument(
        "--mapping",
        default=str(DEFAULT_MAPPING),
        metavar="PATH",
        help=(
            "Path to a column-mapping JSON file "
            f"(default: {DEFAULT_MAPPING.name})."
        ),
    )
    parser.add_argument(
        "--all-variants",
        action="store_true",
        default=False,
        help="Upload all six context-variant mapping files, not just the default.",
    )
    parser.add_argument(
        "--goal",
        default="BOOKINGS",
        metavar="COLUMN",
        help="Dependent variable (default: BOOKINGS).",
    )
    parser.add_argument(
        "--dep-var-type",
        default="revenue",
        choices=["revenue", "conversion"],
        help="Robyn dep_var_type (default: revenue).",
    )
    parser.add_argument(
        "--country-code",
        default="dk",
        metavar="CODE",
        help="Two-letter country code (default: dk).",
    )
    parser.add_argument(
        "--country-name",
        default="Denmark",
        metavar="NAME",
        help="MARKET_NAME filter value (default: Denmark).",
    )
    parser.add_argument(
        "--bucket",
        default=GCS_BUCKET,
        metavar="BUCKET",
        help=f"GCS bucket name (default: {GCS_BUCKET}).",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"❌ CSV not found: {csv_path}")

    # Prepare DataFrame (load, rename, filter)
    df = load_and_prepare_csv(csv_path, args.country_name)

    # Connect to GCS
    logger.info(f"\n🔗 GCS project={PROJECT_ID}, bucket={args.bucket}")
    client = storage.Client(project=PROJECT_ID)

    # Timestamp shared across all uploads in this run
    timestamp = _make_timestamp()

    # Upload JSON config files to benchmarks/dk/
    upload_config_files(client, args.bucket, DK_CONFIG_DIR, "benchmarks/dk")

    # Upload Parquet (once, shared by all variants)
    upload_parquet(df, client, args.bucket, args.country_code, timestamp)

    # Determine which mapping files to upload
    if args.all_variants:
        mapping_paths = [p for p in CONTEXT_VARIANTS if p.exists()]
        if not mapping_paths:
            sys.exit("❌ No context variant files found in data/dk/")
    else:
        mapping_paths = [Path(args.mapping)]

    # Upload selected_columns.json for each variant
    gcs_paths: List[str] = []
    for mp in mapping_paths:
        if not mp.exists():
            logger.warning(f"⚠️  Mapping file not found, skipping: {mp}")
            continue
        gcs_path = setup_variant(
            mp,
            df,
            client,
            args.bucket,
            args.country_code,
            args.goal,
            args.dep_var_type,
            timestamp,
        )
        gcs_paths.append(gcs_path)

    # Print the ready-to-use commands
    print("\n" + "=" * 72)
    print("✅  SETUP COMPLETE — ready to run benchmark(s)")
    print("=" * 72)
    print()
    for gcs_path in gcs_paths:
        print(f"# {gcs_path.split('training_data/')[-1].split('/selected')[0]}")
        print()
        print("# Quick test run (geometric adstock, full window, 1 preset):")
        print(
            f"python scripts/run_full_benchmark.py "
            f"--path {gcs_path} \\\n"
            f"  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \\\n"
            f"  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\\n"
            f"  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json"
        )
        print()
        print("# Standard full run (geometric adstock, full window, 1 preset):")
        print(
            f"python scripts/run_full_benchmark.py \\\n"
            f"  --path {gcs_path} \\\n"
            f"  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \\\n"
            f"  --full-run \\\n"
            f"  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\\n"
            f"  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json"
        )
        print()
        print("# Sequential production — geometric adstock, full window,")
        print("# all splits (3) + spend-var mappings (5) + 3 hyperparameter")
        print("# Sequential: 1 adstock + 3 splits + 2 time-agg + 5 spend-var")
        print("# + 3 hyperparameter presets = 14 combos (~$78, ~5 h):")
        print(
            f"python scripts/run_full_benchmark.py \\\n"
            f"  --path {gcs_path} \\\n"
            f"  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \\\n"
            f"  --full-run \\\n"
            f"  --sequential \\\n"
            f"  --compare-presets \\\n"
            f"  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\\n"
            f"  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json"
        )
        print()


if __name__ == "__main__":
    main()
