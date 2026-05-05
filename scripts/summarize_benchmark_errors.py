#!/usr/bin/env python3
"""
Benchmark Error Summary Script

Reads a GCS file listing (e.g. benchmark_files_last7days.txt) and downloads
status.json, panic_error.json, panic_error.txt and SKIPPED.txt for every
benchmark variant.  Produces a human-readable summary on stdout and
optionally writes CSV / JSON output files.

Usage:
    python scripts/summarize_benchmark_errors.py \\
        --file-list benchmark_files_last7days.txt \\
        --output-csv  benchmark_errors.csv \\
        --output-json benchmark_errors.json

    # dry-run (parse the listing, do NOT download from GCS):
    python scripts/summarize_benchmark_errors.py \\
        --file-list benchmark_files_last7days.txt --dry-run

Prerequisites:
    pip install google-cloud-storage
    gcloud auth application-default login   # or set GOOGLE_APPLICATION_CREDENTIALS
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
DEFAULT_BUCKET = "mmm-app-output"
BENCHMARK_PREFIX = "benchmarks/"

# Files we care about per variant folder
INTERESTING_FILES = {
    "status.json",
    "panic_error.json",
    "panic_error.txt",
    "SKIPPED.txt",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_gcs_uri(uri: str) -> Tuple[str, str]:
    """Return (bucket_name, blob_path) from a gs:// URI."""
    assert uri.startswith("gs://"), f"Not a gs:// URI: {uri}"
    without_scheme = uri[len("gs://") :]
    bucket, _, blob = without_scheme.partition("/")
    return bucket, blob


def parse_file_listing(path: str) -> Dict[str, Dict[str, str]]:
    """
    Parse the file listing and group interesting files by variant folder.

    Returns:
        {variant_gcs_prefix: {filename: full_gcs_uri}}
        e.g.
        {
          "gs://mmm-app-output/benchmarks/run_xyz/variant_abc": {
              "status.json": "gs://mmm-app-output/.../status.json",
              "panic_error.json": "gs://mmm-app-output/.../panic_error.json",
          }
        }
    """
    variants: Dict[str, Dict[str, str]] = defaultdict(dict)
    with open(path) as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or not line.startswith("gs://"):
                continue
            filename = line.rsplit("/", 1)[-1]
            if filename not in INTERESTING_FILES:
                continue
            folder = line.rsplit("/", 1)[0]
            variants[folder][filename] = line
    return dict(variants)


def read_blob_text(
    storage_client: Any, bucket_name: str, blob_path: str
) -> Optional[str]:
    """Download a GCS blob as a UTF-8 string, return None on failure."""
    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        if not blob.exists():
            return None
        return blob.download_as_text(encoding="utf-8")
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug(
            "Could not read gs://%s/%s: %s", bucket_name, blob_path, exc
        )
        return None


def variant_label(gcs_prefix: str) -> Tuple[str, str]:
    """
    Split a variant prefix into (benchmark_run_id, variant_name).

    e.g. "gs://mmm-app-output/benchmarks/dk_benchmark_20260413/geo_75_90"
         -> ("dk_benchmark_20260413", "geo_75_90")
    """
    # strip gs://bucket/benchmarks/
    without_scheme = gcs_prefix[len("gs://") :]
    parts = without_scheme.split("/")
    # parts: [bucket, "benchmarks", run_id, variant, ...]
    if len(parts) >= 4:
        return parts[2], "/".join(parts[3:])
    return gcs_prefix, ""


# ---------------------------------------------------------------------------
# Core collection
# ---------------------------------------------------------------------------
def collect_variant_info(
    variants: Dict[str, Dict[str, str]],
    storage_client: Any,
    dry_run: bool = False,
) -> List[Dict]:
    """
    For each variant folder download the interesting files and build a record.

    Returns a list of dicts with keys:
        run_id, variant, status, error_type, error_message,
        skipped, raw_status, raw_panic_json, raw_panic_txt
    """
    records = []
    total = len(variants)
    for idx, (prefix, files) in enumerate(sorted(variants.items()), 1):
        run_id, variant = variant_label(prefix)
        logger.info("[%d/%d] %s / %s", idx, total, run_id, variant)

        record: Dict[str, Any] = {
            "run_id": run_id,
            "variant": variant,
            "gcs_prefix": prefix,
            "status": "unknown",
            "error_type": None,
            "error_message": None,
            "skipped": "SKIPPED.txt" in files,
            "raw_status": None,
            "raw_panic_json": None,
            "raw_panic_txt": None,
        }

        if dry_run:
            record["status"] = "dry_run"
            records.append(record)
            continue

        # --- status.json ---
        if "status.json" in files:
            bucket_name, blob_path = parse_gcs_uri(files["status.json"])
            text = read_blob_text(storage_client, bucket_name, blob_path)
            record["raw_status"] = text
            if text:
                try:
                    data = json.loads(text)
                    record["status"] = data.get("status", "unknown")
                except json.JSONDecodeError:
                    record["status"] = text.strip()[:120]

        # --- panic_error.json ---
        if "panic_error.json" in files:
            bucket_name, blob_path = parse_gcs_uri(files["panic_error.json"])
            text = read_blob_text(storage_client, bucket_name, blob_path)
            record["raw_panic_json"] = text
            if text:
                try:
                    data = json.loads(text)
                    record["error_type"] = data.get(
                        "error_type", data.get("type", None)
                    )
                    record["error_message"] = (
                        data.get("error", data.get("message", str(data)))
                    )[:500]
                except json.JSONDecodeError:
                    record["error_message"] = text.strip()[:500]

        # --- panic_error.txt fallback ---
        if record["error_message"] is None and "panic_error.txt" in files:
            bucket_name, blob_path = parse_gcs_uri(files["panic_error.txt"])
            text = read_blob_text(storage_client, bucket_name, blob_path)
            record["raw_panic_txt"] = text
            if text:
                record["error_message"] = text.strip()[:500]

        # Derive status from presence of error files if status.json is absent
        if record["status"] == "unknown":
            if record["error_message"]:
                record["status"] = "failed"
            elif record["skipped"]:
                record["status"] = "skipped"

        records.append(record)

    return records


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_summary(records: List[Dict]) -> None:
    """Print a human-readable summary to stdout."""
    total = len(records)
    status_counts: Counter = Counter(r["status"] for r in records)
    error_counts: Counter = Counter(
        r["error_message"] for r in records if r["error_message"]
    )
    error_type_counts: Counter = Counter(
        r["error_type"] for r in records if r["error_type"]
    )
    run_fail_counts: Counter = Counter(
        r["run_id"]
        for r in records
        if r["status"] not in ("success", "completed", "dry_run", "skipped")
    )

    print("\n" + "=" * 70)
    print("BENCHMARK ERROR SUMMARY")
    print("=" * 70)
    print(f"\nTotal variants scanned : {total}")
    print("\n--- Status breakdown ---")
    for status, count in status_counts.most_common():
        pct = 100.0 * count / total if total else 0
        print(f"  {status:<25} {count:>5}  ({pct:.1f}%)")

    failed = [r for r in records if r["error_message"]]
    print(f"\n--- Variants with errors : {len(failed)} ---")

    if error_type_counts:
        print("\nTop error TYPES:")
        for etype, count in error_type_counts.most_common(15):
            print(f"  {count:>4}x  {etype}")

    if error_counts:
        print("\nTop error MESSAGES (truncated to 120 chars):")
        for msg, count in error_counts.most_common(20):
            short = msg.replace("\n", " ")[:120]
            print(f"  {count:>4}x  {short}")

    if run_fail_counts:
        print("\nRuns with most failures:")
        for run_id, count in run_fail_counts.most_common(15):
            print(f"  {count:>4} failed  {run_id}")

    print("\n--- Failed variants (detail) ---")
    for rec in sorted(failed, key=lambda r: (r["run_id"], r["variant"])):
        msg_short = (rec["error_message"] or "").replace("\n", " ")[:120]
        print(f"\n  Run     : {rec['run_id']}")
        print(f"  Variant : {rec['variant']}")
        print(f"  Status  : {rec['status']}")
        if rec["error_type"]:
            print(f"  ErrType : {rec['error_type']}")
        print(f"  Message : {msg_short}")

    print("\n" + "=" * 70)


def write_csv(records: List[Dict], path: str) -> None:
    """Write records to a CSV file (no pandas dependency)."""
    import csv

    fields = [
        "run_id",
        "variant",
        "status",
        "skipped",
        "error_type",
        "error_message",
        "gcs_prefix",
    ]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    logger.info("CSV written to %s", path)


def write_json(records: List[Dict], path: str) -> None:
    """Write records to a JSON file (omitting raw blob content)."""
    slim = [
        {
            k: v
            for k, v in rec.items()
            if k not in ("raw_status", "raw_panic_json", "raw_panic_txt")
        }
        for rec in records
    ]
    with open(path, "w") as fh:
        json.dump(slim, fh, indent=2)
    logger.info("JSON written to %s", path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarise errors from a benchmark GCS file listing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--file-list",
        default="benchmark_files_last7days.txt",
        help="Path to the GCS file listing (default: benchmark_files_last7days.txt)",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Optional path to write a CSV summary (e.g. /tmp/errors.csv)",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to write a JSON summary (e.g. /tmp/errors.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse the listing but do NOT download anything from GCS",
    )
    parser.add_argument(
        "--bucket",
        default=DEFAULT_BUCKET,
        help=f"GCS bucket name (default: {DEFAULT_BUCKET})",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable DEBUG logging"
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    file_list = args.file_list
    if not Path(file_list).exists():
        logger.error("File list not found: %s", file_list)
        return 1

    logger.info("Parsing file listing: %s", file_list)
    variants = parse_file_listing(file_list)
    logger.info("Found %d benchmark variant folders", len(variants))

    storage_client = None
    if not args.dry_run:
        try:
            from google.cloud import storage as gcs

            storage_client = gcs.Client()
        except ImportError:
            logger.error(
                "google-cloud-storage is not installed. "
                "Run: pip install google-cloud-storage  "
                "Or use --dry-run to skip GCS downloads."
            )
            return 1
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Could not initialise GCS client: %s", exc)
            logger.error(
                "Ensure you are authenticated: "
                "gcloud auth application-default login"
            )
            return 1

    records = collect_variant_info(
        variants, storage_client, dry_run=args.dry_run
    )

    print_summary(records)

    if args.output_csv:
        write_csv(records, args.output_csv)

    if args.output_json:
        write_json(records, args.output_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
