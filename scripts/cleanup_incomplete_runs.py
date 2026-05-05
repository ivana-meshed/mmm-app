#!/usr/bin/env python3
"""
Cleanup GCS runs that produced no useful output.

A run is considered "incomplete" (and eligible for deletion) when it has:
  - no plot files (.png / .pdf)   AND
  - no r2 results (model_summary.json with r2 fields, or a CSV with r2 columns)

Use --mode=any to delete runs missing *either* criterion instead of both.

Run structure expected:
  robyn/<revision>/<country>/<stamp>/<files...>

Examples
--------
  # Dry-run (safe): show what would be deleted
  python scripts/cleanup_incomplete_runs.py --dry-run

  # Delete runs missing both plots AND r2 under revision r42
  python scripts/cleanup_incomplete_runs.py --no-dry-run --prefix robyn/r42/

  # Delete runs missing either plots OR r2
  python scripts/cleanup_incomplete_runs.py --no-dry-run --mode any

  # Skip confirmation prompt (useful in CI)
  python scripts/cleanup_incomplete_runs.py --no-dry-run --yes
"""

import argparse
import io
import json
import logging
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

try:
    from google.cloud import storage
except ImportError:
    logger.error(
        "google-cloud-storage is not installed. Run: pip install google-cloud-storage"
    )
    sys.exit(1)

try:
    import pandas as pd

    _PANDAS_AVAILABLE = True
except ImportError:
    _PANDAS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _group_by_run(blobs) -> Dict[tuple, List]:
    """Group blobs by (revision, country, stamp)."""
    runs: Dict[tuple, List] = defaultdict(list)
    for b in blobs:
        parts = b.name.split("/")
        # robyn / <rev> / <country> / <stamp> / <file>
        if len(parts) >= 5 and parts[0] == "robyn" and parts[4]:
            key = (parts[1], parts[2], parts[3])
            runs[key].append(b)
    return runs


def _has_plots(blobs) -> bool:
    return any(
        b.name.lower().endswith(".png") or b.name.lower().endswith(".pdf")
        for b in blobs
    )


def _has_r2(blobs) -> bool:
    """Return True if any blob looks like it contains r2 metrics or spend output.

    Accepted signals (any one is sufficient):
    1. model_summary.json with r2/rsq fields.
    2. Any CSV whose column names contain 'r2' or 'rsq'.
    3. allocator_metrics.csv with a non-null, non-zero ``allocator_total_spend``
       value — a reliable sign that the model ran to completion and the budget
       allocator succeeded even when r2 values happen to be NA/missing.
    """
    for b in blobs:
        name_l = b.name.lower()
        # model_summary.json is the canonical source
        if name_l.endswith("model_summary.json"):
            try:
                data = json.loads(b.download_as_bytes())
                # r2 can live at top level or inside candidate_models list
                if _r2_in_dict(data):
                    return True
            except Exception:
                pass
        if _PANDAS_AVAILABLE and name_l.endswith(".csv"):
            try:
                raw = b.download_as_bytes()
                df = pd.read_csv(io.BytesIO(raw), nrows=5)
                cols_l = [c.lower() for c in df.columns]
                # Primary check: column names contain r2 or rsq
                if any("r2" in c or "rsq" in c for c in cols_l):
                    return True
                # Fallback: allocator_metrics.csv with a real spend value
                if name_l.endswith(
                    "allocator_metrics.csv"
                ) and _has_spend_value(df):
                    return True
            except Exception:
                pass
    return False


def _has_spend_value(df) -> bool:
    """Return True if the dataframe has a numeric spend column with a positive value."""
    spend_cols = [c for c in df.columns if "spend" in c.lower()]
    for col in spend_cols:
        try:
            val = pd.to_numeric(df[col].iloc[0], errors="coerce")
            if val is not None and val > 0:
                return True
        except Exception:
            pass
    return False


def _r2_in_dict(obj, depth: int = 4) -> bool:
    """Recursively look for r2 / rsq keys with non-null numeric values."""
    if depth <= 0:
        return False
    if isinstance(obj, dict):
        for k, v in obj.items():
            if ("r2" in k.lower() or "rsq" in k.lower()) and isinstance(
                v, (int, float)
            ):
                return True
            if _r2_in_dict(v, depth - 1):
                return True
    elif isinstance(obj, list):
        for item in obj[:20]:  # only scan first 20 to keep it fast
            if _r2_in_dict(item, depth - 1):
                return True
    return False


def _run_label(rev: str, country: str, stamp: str) -> str:
    return f"robyn/{rev}/{country}/{stamp}/"


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def find_incomplete_runs(
    bucket_name: str, prefix: str, mode: str
) -> Dict[tuple, List]:
    """
    Return a dict of {run_key: blobs} for runs considered incomplete.

    mode='both'  → missing BOTH plots AND r2
    mode='any'   → missing EITHER plots OR r2
    """
    client = storage.Client()
    logger.info("Listing blobs under gs://%s/%s ...", bucket_name, prefix)
    all_blobs = list(client.list_blobs(bucket_name, prefix=prefix))
    logger.info("Found %d total blobs", len(all_blobs))

    runs = _group_by_run(all_blobs)
    logger.info("Found %d distinct runs", len(runs))

    incomplete: Dict[tuple, List] = {}
    for key, blobs in runs.items():
        has_p = _has_plots(blobs)
        has_r = _has_r2(blobs)

        if mode == "any":
            useless = not has_p or not has_r
        else:  # mode == "both"
            useless = not has_p and not has_r

        if useless:
            incomplete[key] = blobs
            rev, country, stamp = key
            logger.info(
                "  INCOMPLETE  %s  [plots=%s r2=%s]",
                _run_label(rev, country, stamp),
                "✓" if has_p else "✗",
                "✓" if has_r else "✗",
            )

    return incomplete


def delete_runs(
    bucket_name: str,
    incomplete: Dict[tuple, List],
    dry_run: bool,
) -> None:
    deleted_runs = 0
    deleted_blobs = 0
    failed = 0

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    for key, blobs in incomplete.items():
        rev, country, stamp = key
        label = _run_label(rev, country, stamp)
        if dry_run:
            logger.info(
                "[DRY RUN] Would delete %d blobs from %s", len(blobs), label
            )
            deleted_blobs += len(blobs)
            deleted_runs += 1
        else:
            logger.info("Deleting %d blobs from %s ...", len(blobs), label)
            for b in blobs:
                try:
                    bucket.blob(b.name).delete()
                    deleted_blobs += 1
                except Exception as e:
                    logger.warning("  Failed to delete %s: %s", b.name, e)
                    failed += 1
            deleted_runs += 1

    action = "Would delete" if dry_run else "Deleted"
    logger.info("=" * 60)
    logger.info(
        "%s %d run(s), %d blob(s). Failed: %d",
        action,
        deleted_runs,
        deleted_blobs,
        failed,
    )
    if dry_run:
        logger.info("Re-run with --no-dry-run to actually delete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete GCS runs that have no plots and/or no r2 results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name (default: mmm-app-output)",
    )
    parser.add_argument(
        "--prefix",
        default="robyn/",
        help="GCS prefix to scan (default: robyn/)",
    )
    parser.add_argument(
        "--mode",
        choices=["both", "any"],
        default="both",
        help=(
            "Deletion criterion: 'both' = delete only when BOTH plots AND r2 are "
            "absent (default); 'any' = delete when EITHER is absent."
        ),
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="List what would be deleted without actually deleting (default: on).",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually delete blobs.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Skip the confirmation prompt.",
    )

    args = parser.parse_args()

    logger.info("Bucket  : gs://%s", args.bucket)
    logger.info("Prefix  : %s", args.prefix)
    logger.info("Mode    : %s", args.mode)
    logger.info("Dry run : %s", args.dry_run)

    incomplete = find_incomplete_runs(args.bucket, args.prefix, args.mode)

    if not incomplete:
        logger.info("No incomplete runs found. Nothing to do.")
        return

    total_blobs = sum(len(v) for v in incomplete.values())
    logger.info(
        "\nSummary: %d incomplete run(s) found (%d blobs total).",
        len(incomplete),
        total_blobs,
    )

    if not args.dry_run and not args.yes:
        answer = input(
            f"\n⚠️  About to permanently delete {total_blobs} blobs "
            f"across {len(incomplete)} runs. Type 'yes' to proceed: "
        )
        if answer.strip().lower() != "yes":
            logger.info("Aborted.")
            return

    delete_runs(args.bucket, incomplete, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
