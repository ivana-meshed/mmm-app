#!/usr/bin/env python3
"""
Download and analyse all result files for a benchmark run.

Downloads every artifact written by the R training container
(plan.json, status.json, panic_error.json, panic_error.txt,
SKIPPED.txt, model_summary.json, console.log) into a local
directory and produces an in-depth analysis, including OOM
detection (Container terminated on signal 9).

Usage:
    # Analyse a specific benchmark run
    python scripts/download_and_analyze_results.py \\
        --benchmark-id dk_benchmark_20260420_123456

    # List the 10 most-recent benchmarks, then analyse the newest
    python scripts/download_and_analyze_results.py --list-recent 10

    # Save files locally and write a CSV / JSON / text report
    python scripts/download_and_analyze_results.py \\
        --benchmark-id dk_benchmark_20260420_123456 \\
        --output-dir /tmp/benchmark_results \\
        --output-csv    /tmp/benchmark_results/report.csv \\
        --output-json   /tmp/benchmark_results/report.json \\
        --report-file   /tmp/benchmark_results/report.txt \\
        --log-file      /tmp/benchmark_results/run.log

    # Analyse without downloading (re-use previously downloaded files)
    python scripts/download_and_analyze_results.py \\
        --benchmark-id dk_benchmark_20260420_123456 \\
        --local-dir /tmp/benchmark_results --skip-download

    # Auto-generate log / report filenames inside --output-dir
    python scripts/download_and_analyze_results.py \\
        --benchmark-id dk_benchmark_20260420_123456 \\
        --output-dir /tmp/benchmark_results \\
        --auto-save

Prerequisites:
    pip install google-cloud-storage
    gcloud auth application-default login
"""

import argparse
import io
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"
LOG_DATE_FMT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FMT,
)
logger = logging.getLogger(__name__)


def _add_file_handler(path: Path) -> logging.FileHandler:
    """Attach a file handler to the root logger and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(str(path), encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATE_FMT))
    logging.getLogger().addHandler(fh)
    return fh


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
BENCHMARK_PREFIX = "benchmarks/"

# Artifacts to download per variant folder.
# console.log can be large; include it to detect OOM patterns.
ARTIFACTS = [
    "status.json",
    "panic_error.json",
    "panic_error.txt",
    "SKIPPED.txt",
    "model_summary.json",
    "console.log",
]

# Phrases in console.log that indicate an OOM kill
OOM_INDICATORS = [
    "signal 9",
    "killed",
    "out of memory",
    "cannot allocate vector",
    "Error: protect(): protection stack overflow",
    "Segmentation fault",
    "R session aborted",
]


# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------


def _storage_client():
    """Return an authenticated google-cloud-storage Client."""
    try:
        from google.cloud import (  # pylint: disable=import-outside-toplevel
            storage,
        )

        return storage.Client()
    except ImportError:
        logger.error(
            "google-cloud-storage is not installed. "
            "Run: pip install google-cloud-storage"
        )
        sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Could not initialise GCS client: %s", exc)
        logger.error(
            "Authenticate with: gcloud auth application-default login  "
            "or set GOOGLE_APPLICATION_CREDENTIALS"
        )
        sys.exit(1)


def _read_blob_text(bucket, blob_path: str) -> Optional[str]:
    """Download a blob as UTF-8 text; return None if missing or unreadable."""
    try:
        blob = bucket.blob(blob_path)
        if not blob.exists():
            logger.debug("Blob not found: gs://%s/%s", bucket.name, blob_path)
            return None
        text = blob.download_as_text(encoding="utf-8")
        logger.debug(
            "Downloaded gs://%s/%s (%d bytes)",
            bucket.name,
            blob_path,
            len(text),
        )
        return text
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug(
            "Could not read gs://%s/%s: %s", bucket.name, blob_path, exc
        )
        return None


def _save_locally(text: Optional[str], local_path: Path) -> None:
    """Write text to *local_path*, creating parent dirs as needed."""
    if text is None:
        return
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Benchmark discovery
# ---------------------------------------------------------------------------


def list_recent_benchmarks(bucket, n: int = 10) -> List[Tuple[str, datetime]]:
    """
    Return the *n* most-recent benchmark IDs found under ``benchmarks/``.

    Presence of ``benchmarks/{id}/plan.json`` is used as the marker.
    Falls back to any blob under ``benchmarks/{id}/`` when plan.json is absent.
    """
    blobs = bucket.list_blobs(prefix=BENCHMARK_PREFIX, delimiter="/")
    # Consume the iterator to populate prefixes
    _ = list(blobs)
    prefixes = list(blobs.prefixes)  # type: ignore[union-attr]

    results: List[Tuple[str, datetime]] = []
    for prefix in prefixes:
        # prefix looks like "benchmarks/run_id/"
        run_id = prefix.rstrip("/").split("/")[-1]
        # Try to get plan.json time, else first blob time
        plan_blob = bucket.blob(f"{prefix}plan.json")
        try:
            plan_blob.reload()
            ts = plan_blob.updated or datetime.now(timezone.utc)
        except Exception:  # pylint: disable=broad-except
            ts = datetime.now(timezone.utc)
        results.append((run_id, ts))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:n]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def download_benchmark(
    bucket,
    benchmark_id: str,
    output_dir: Path,
) -> Dict[str, Dict[str, Optional[str]]]:
    """
    Download all artifacts for *benchmark_id* into *output_dir*.

    Also downloads ``plan.json`` from ``benchmarks/{benchmark_id}/plan.json``.

    Returns a nested dict::

        {
            "_plan": {"plan.json": <text or None>},
            "<variant>": {
                "status.json": <text or None>,
                "panic_error.json": ...,
                ...
            },
        }
    """
    prefix = f"{BENCHMARK_PREFIX}{benchmark_id}/"
    logger.info("Listing blobs under gs://%s/%s", bucket.name, prefix)

    t0 = time.monotonic()
    try:
        all_blobs = list(bucket.list_blobs(prefix=prefix))
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to list GCS blobs: %s", exc)
        return {}

    if not all_blobs:
        logger.warning("No blobs found under gs://%s/%s", bucket.name, prefix)
        return {}

    logger.info("Found %d blob(s) total — starting download…", len(all_blobs))

    data: Dict[str, Dict[str, Optional[str]]] = {}
    downloaded = 0
    skipped = 0

    for blob in all_blobs:
        # blob.name = benchmarks/{benchmark_id}/...
        relative = blob.name[len(prefix) :]  # strip prefix
        parts = relative.split("/", 1)

        if len(parts) == 1:
            # Top-level file, e.g. plan.json
            filename = parts[0]
            key = "_plan"
        else:
            # Variant file, e.g. geo_75_90/status.json
            key = parts[0]  # variant name
            filename = parts[1]

        if filename not in ARTIFACTS and filename != "plan.json":
            logger.debug("Skipping non-artifact blob: %s", blob.name)
            skipped += 1
            continue

        try:
            text = blob.download_as_text(encoding="utf-8")
            downloaded += 1
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Could not download %s: %s", blob.name, exc)
            text = None

        data.setdefault(key, {})[filename] = text

        # Save locally
        local_path = output_dir / benchmark_id / key / filename
        _save_locally(text, local_path)
        logger.debug("  ✓ saved %s", local_path)

    elapsed = time.monotonic() - t0
    n_variants = len([k for k in data if k != "_plan"])
    logger.info(
        "Download complete in %.1fs — %d artifact(s) saved across %d variant(s)"
        " (%d blob(s) skipped as non-artifact)",
        elapsed,
        downloaded,
        n_variants,
        skipped,
    )
    return data


def load_from_local(
    benchmark_id: str, local_dir: Path
) -> Dict[str, Dict[str, Optional[str]]]:
    """
    Load previously downloaded artifacts from *local_dir/{benchmark_id}/*.
    """
    base = local_dir / benchmark_id
    if not base.exists():
        logger.error("Local directory not found: %s", base)
        return {}

    data: Dict[str, Dict[str, Optional[str]]] = {}
    for item in base.iterdir():
        if item.is_dir():
            key = item.name
            for artifact in ARTIFACTS + ["plan.json"]:
                fpath = item / artifact
                if fpath.exists():
                    data.setdefault(key, {})[artifact] = fpath.read_text(
                        encoding="utf-8"
                    )
        elif item.is_file() and item.name == "plan.json":
            data.setdefault("_plan", {})["plan.json"] = item.read_text(
                encoding="utf-8"
            )
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _parse_status(text: Optional[str]) -> Dict[str, Any]:
    """Parse status.json text → dict with 'state' and raw fields."""
    if not text:
        return {"state": "missing"}
    try:
        d = json.loads(text)
        return d
    except json.JSONDecodeError:
        return {"state": text.strip()[:80]}


def _parse_panic(
    panic_json: Optional[str], panic_txt: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """Return (error_type, error_message) from panic artifacts."""
    if panic_json:
        try:
            d = json.loads(panic_json)
            etype = d.get("error_type") or d.get("type")
            emsg = d.get("error") or d.get("message") or str(d)
            return etype, str(emsg)[:600]
        except json.JSONDecodeError:
            return None, panic_json.strip()[:600]
    if panic_txt:
        return None, panic_txt.strip()[:600]
    return None, None


def _detect_oom(
    console_log: Optional[str],
    status: Dict[str, Any],
    panic_json: Optional[str],
    panic_txt: Optional[str],
) -> bool:
    """
    Heuristic OOM detection.

    A variant is flagged as OOM when:
    - The console.log contains a known OOM indicator, OR
    - status.json is missing entirely AND panic_error.json is also missing
      (which happens when the container is killed with SIGKILL / signal 9
      before R can write any error files).
    """
    if console_log:
        cl_lower = console_log.lower()
        for indicator in OOM_INDICATORS:
            if indicator.lower() in cl_lower:
                return True

    # status.json present with a "killed" or signal-9 marker
    raw_status_str = json.dumps(status).lower()
    if "signal 9" in raw_status_str or "killed" in raw_status_str:
        return True

    # No error files at all (container killed before R trap ran)
    state = status.get("state", "missing")
    if state == "missing" and panic_json is None and panic_txt is None:
        return True

    return False


def _parse_model_summary(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return parsed model_summary.json or None."""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_metrics(summary: Optional[Dict]) -> Dict[str, Any]:
    """Pull key metrics out of a model_summary dict."""
    if not summary:
        return {}
    best = summary.get("best_model", {}) or {}
    decomp = summary.get("decomp_contribution", {}) or {}
    meta = summary.get("input_metadata", {}) or {}
    return {
        "rsq_train": best.get("rsq_train"),
        "rsq_val": best.get("rsq_val"),
        "rsq_test": best.get("rsq_test"),
        "nrmse": best.get("nrmse"),
        "nrmse_train": best.get("nrmse_train"),
        "nrmse_val": best.get("nrmse_val"),
        "nrmse_test": best.get("nrmse_test"),
        "decomp_rssd": best.get("decomp_rssd"),
        "mape": best.get("mape"),
        "paid_media_share": decomp.get("paid_media_share"),
        "baseline_share": decomp.get("baseline_share"),
        "iterations": meta.get("iterations") or summary.get("iterations"),
        "trials": meta.get("trials") or summary.get("trials"),
        "adstock": meta.get("adstock") or summary.get("adstock"),
        "train_size": meta.get("train_size") or summary.get("train_size"),
        "elapsed_sec": summary.get("elapsed_sec"),
    }


def _parse_plan(plan_text: Optional[str]) -> Optional[Dict]:
    if not plan_text:
        return None
    try:
        return json.loads(plan_text)
    except json.JSONDecodeError:
        return None


def analyse(
    benchmark_id: str,
    data: Dict[str, Dict[str, Optional[str]]],
) -> List[Dict[str, Any]]:
    """
    Build one record per variant containing:
    - derived_status  : SUCCEEDED / FAILED / SKIPPED / OOM / PENDING / UNKNOWN
    - oom             : bool
    - error_type, error_message
    - model metrics   : rsq_train … elapsed_sec (when available)
    - raw GCS artifacts for further inspection
    """
    plan_data = data.get("_plan", {})
    plan = _parse_plan(plan_data.get("plan.json"))
    plan_variants: Dict[str, Any] = {}
    if plan:
        for v in plan.get("variants", []):
            name = v.get("benchmark_variant") or v.get("variant_name", "")
            if name:
                plan_variants[name] = v
        logger.info(
            "plan.json loaded — %d planned variant(s)", len(plan_variants)
        )
    else:
        logger.warning(
            "plan.json not found or unparseable — variant list from GCS only"
        )

    # All keys except _plan are variant names
    variant_keys = sorted(k for k in data if k != "_plan")

    # Include variants listed in plan but absent from GCS (never started)
    pending_names = []
    for name in plan_variants:
        if name not in variant_keys:
            variant_keys.append(name)
            pending_names.append(name)

    if pending_names:
        logger.info(
            "%d variant(s) in plan but not yet in GCS (PENDING): %s",
            len(pending_names),
            ", ".join(pending_names),
        )

    logger.info("Analysing %d variant(s)…", len(variant_keys))

    records = []
    for i, variant in enumerate(variant_keys, 1):
        files = data.get(variant, {})
        status_text = files.get("status.json")
        panic_json_text = files.get("panic_error.json")
        panic_txt_text = files.get("panic_error.txt")
        skipped_text = files.get("SKIPPED.txt")
        summary_text = files.get("model_summary.json")
        console_text = files.get("console.log")

        status = _parse_status(status_text)
        error_type, error_message = _parse_panic(
            panic_json_text, panic_txt_text
        )
        oom = _detect_oom(console_text, status, panic_json_text, panic_txt_text)
        summary = _parse_model_summary(summary_text)
        metrics = _extract_metrics(summary)

        # Derive a clean status label
        state = status.get("state", "missing")
        if not files:
            # variant was in plan but no GCS files exist at all
            derived_status = "PENDING"
        elif skipped_text is not None:
            derived_status = "SKIPPED"
        elif oom:
            derived_status = "OOM"
        elif summary is not None:
            derived_status = "SUCCEEDED"
        elif state in ("failed", "FAILED"):
            derived_status = "FAILED"
        elif state in ("running", "RUNNING"):
            derived_status = "RUNNING"
        elif state in ("success", "SUCCESS", "completed", "COMPLETED"):
            derived_status = "SUCCEEDED"
        elif state == "missing":
            derived_status = "OOM" if oom else "UNKNOWN"
        else:
            derived_status = "FAILED" if error_message else "UNKNOWN"

        _status_icon = {
            "SUCCEEDED": "✓",
            "FAILED": "✗",
            "OOM": "💀",
            "SKIPPED": "–",
            "PENDING": "…",
            "RUNNING": "▶",
        }.get(derived_status, "?")
        rsq_str = (
            f"  R²={metrics['rsq_train']:.3f}"
            if metrics.get("rsq_train")
            else ""
        )
        logger.info(
            "[%d/%d] %s %-40s  %s%s",
            i,
            len(variant_keys),
            _status_icon,
            variant,
            derived_status,
            rsq_str,
        )
        if oom:
            logger.warning("        ↳ OOM suspected — no panic_error written")
        if error_message:
            logger.debug(
                "        ↳ error: %s", error_message.replace("\n", " ")[:120]
            )

        # Console-log tail for quick inspection (last 30 lines)
        console_tail = ""
        if console_text:
            lines = console_text.strip().splitlines()
            console_tail = "\n".join(lines[-30:])

        record: Dict[str, Any] = {
            "benchmark_id": benchmark_id,
            "variant": variant,
            "derived_status": derived_status,
            "oom": oom,
            "status_state": state,
            "error_type": error_type,
            "error_message": error_message,
            "has_model_summary": summary is not None,
            "has_console_log": console_text is not None,
            "console_tail": console_tail,
            **metrics,
        }
        records.append(record)

    return records


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_report(
    benchmark_id: str,
    records: List[Dict[str, Any]],
    report_file: Optional[Path] = None,
) -> None:
    """Print a human-readable in-depth report to stdout and optionally to a file."""
    # Capture output so we can tee it to a file if requested
    buf = io.StringIO()

    def _p(*args, **kwargs):  # thin wrapper to write to both buf and stdout
        print(*args, **kwargs)
        kwargs.pop("file", None)
        print(*args, file=buf, **kwargs)

    total = len(records)
    status_counts: Counter = Counter(r["derived_status"] for r in records)
    oom_count = sum(1 for r in records if r["oom"])

    _p()
    _p("=" * 72)
    _p(f"BENCHMARK ANALYSIS  —  {benchmark_id}")
    _p(
        f"Generated : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    _p("=" * 72)
    _p(f"\nTotal variants : {total}")
    _p("\nStatus breakdown:")
    for status, count in status_counts.most_common():
        pct = 100.0 * count / total if total else 0
        marker = "  ⚠ OOM" if status == "OOM" else ""
        _p(f"  {status:<20} {count:>4}  ({pct:.0f}%){marker}")

    if oom_count:
        _p(f"\n{'─'*72}")
        _p(
            f"⚠  {oom_count} variant(s) appear to have been OOM-killed "
            f"(Container terminated on signal 9 / SIGKILL)."
        )
        _p(
            "   Symptoms: no panic_error.json written, console.log "
            "truncated or absent,\n"
            "   status.json missing or shows no final state.\n"
            "   Fix: reduce --iterations / --trials, increase Cloud Run "
            "memory allocation,\n"
            "   or use --sequential to avoid the cartesian combination explosion."
        )

    # Succeeded variants — metrics overview
    succeeded = [r for r in records if r["derived_status"] == "SUCCEEDED"]
    if succeeded:
        _p(f"\n{'─'*72}")
        _p(f"SUCCEEDED variants ({len(succeeded)}):")
        header = f"  {'Variant':<35} {'R²trn':>6} {'R²val':>6} {'NRMSE':>7} {'RSSD':>7} {'sec':>6}"
        _p(header)
        _p("  " + "-" * (len(header) - 2))
        for r in sorted(succeeded, key=lambda x: x["variant"]):
            rsq_train = (
                f"{r['rsq_train']:.3f}"
                if r["rsq_train"] is not None
                else "  —  "
            )
            rsq_val = (
                f"{r['rsq_val']:.3f}" if r["rsq_val"] is not None else "  —  "
            )
            nrmse = f"{r['nrmse']:.4f}" if r["nrmse"] is not None else "   —   "
            rssd = (
                f"{r['decomp_rssd']:.4f}"
                if r["decomp_rssd"] is not None
                else "   —   "
            )
            elapsed = (
                f"{int(r['elapsed_sec'])}" if r.get("elapsed_sec") else "  —  "
            )
            _p(
                f"  {r['variant']:<35} {rsq_train:>6} {rsq_val:>6} "
                f"{nrmse:>7} {rssd:>7} {elapsed:>6}"
            )

    # Failed / OOM variants — detail
    bad = [
        r
        for r in records
        if r["derived_status"] in ("FAILED", "OOM", "UNKNOWN")
    ]
    if bad:
        _p(f"\n{'─'*72}")
        _p(f"FAILED / OOM variants ({len(bad)}):")
        for r in sorted(bad, key=lambda x: x["variant"]):
            _p(f"\n  Variant : {r['variant']}")
            _p(
                f"  Status  : {r['derived_status']}  (raw state: {r['status_state']})"
            )
            if r["oom"]:
                _p("  OOM     : YES — container was likely killed by SIGKILL")
            if r["error_type"]:
                _p(f"  ErrType : {r['error_type']}")
            if r["error_message"]:
                short = r["error_message"].replace("\n", " ")[:160]
                _p(f"  Message : {short}")
            if r["console_tail"]:
                _p("  Console (last 30 lines):")
                for line in r["console_tail"].splitlines():
                    _p(f"    {line}")

    # Pending variants
    pending = [r for r in records if r["derived_status"] == "PENDING"]
    if pending:
        _p(f"\n{'─'*72}")
        _p(f"PENDING variants ({len(pending)}) — not yet started:")
        for r in pending:
            _p(f"  {r['variant']}")

    _p("\n" + "=" * 72)

    if report_file:
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(buf.getvalue(), encoding="utf-8")
        logger.info("Text report written to %s", report_file)


def write_csv(records: List[Dict[str, Any]], path: str) -> None:
    """Write records to CSV (stdlib only — no pandas dependency)."""
    import csv  # pylint: disable=import-outside-toplevel

    skip_fields = {"console_tail"}
    fields = [k for k in records[0] if k not in skip_fields] if records else []
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    logger.info("CSV written to %s", path)


def write_json(records: List[Dict[str, Any]], path: str) -> None:
    """Write records to JSON (console_tail omitted for brevity)."""
    slim = [
        {k: v for k, v in r.items() if k != "console_tail"} for r in records
    ]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(slim, fh, indent=2, default=str)
    logger.info("JSON written to %s", path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download all result files for a benchmark run from GCS "
            "and produce an in-depth analysis (including OOM detection)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--benchmark-id",
        default=None,
        help="Benchmark run ID to analyse (e.g. dk_benchmark_20260420_123456). "
        "Required unless --list-recent is used.",
    )
    parser.add_argument(
        "--bucket",
        default=DEFAULT_BUCKET,
        help=f"GCS bucket (default: {DEFAULT_BUCKET})",
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/benchmark_results",
        help="Local directory to save downloaded files (default: /tmp/benchmark_results)",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Path for the CSV report (e.g. /tmp/report.csv)",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Path for the JSON report (e.g. /tmp/report.json)",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Path to save the full text report (e.g. /tmp/report.txt). "
        "Use --auto-save to generate the name automatically.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path to write all log messages for this run "
        "(e.g. /tmp/benchmark_results/run.log). "
        "Use --auto-save to generate the name automatically.",
    )
    parser.add_argument(
        "--auto-save",
        action="store_true",
        help="Automatically write log, text report, CSV, and JSON into "
        "--output-dir using the benchmark-id and timestamp as filename.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip GCS download and load from --local-dir instead.",
    )
    parser.add_argument(
        "--local-dir",
        default=None,
        help="Directory containing previously downloaded artifacts "
        "(used with --skip-download). Defaults to --output-dir.",
    )
    parser.add_argument(
        "--list-recent",
        type=int,
        metavar="N",
        default=None,
        help="List the N most-recent benchmarks and exit.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    return parser


def main() -> int:  # noqa: C901 — intentionally linear
    run_start = time.monotonic()
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ------------------------------------------------------------------ #
    # List-recent mode                                                     #
    # ------------------------------------------------------------------ #
    if args.list_recent is not None:
        client = _storage_client()
        bucket = client.bucket(args.bucket)
        recent = list_recent_benchmarks(bucket, n=args.list_recent)
        if not recent:
            print("No benchmarks found.")
            return 0
        print(
            f"\nMost-recent {len(recent)} benchmark(s) in gs://{args.bucket}/benchmarks/:"
        )
        for run_id, ts in recent:
            print(f"  {ts.strftime('%Y-%m-%d %H:%M UTC')}  {run_id}")
        print()
        return 0

    # ------------------------------------------------------------------ #
    # Require benchmark-id for all other modes                            #
    # ------------------------------------------------------------------ #
    if not args.benchmark_id:
        parser.error("--benchmark-id is required (or use --list-recent N)")

    benchmark_id = args.benchmark_id
    output_dir = Path(args.output_dir)

    # ------------------------------------------------------------------ #
    # Auto-generate output filenames when --auto-save is set              #
    # ------------------------------------------------------------------ #
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem = f"{benchmark_id}_{run_ts}"

    log_file_path: Optional[Path] = None
    report_file_path: Optional[Path] = None
    csv_path: Optional[str] = args.output_csv
    json_path: Optional[str] = args.output_json

    if args.log_file:
        log_file_path = Path(args.log_file)
    elif args.auto_save:
        log_file_path = output_dir / f"{stem}.log"

    if args.report_file:
        report_file_path = Path(args.report_file)
    elif args.auto_save:
        report_file_path = output_dir / f"{stem}_report.txt"

    if args.auto_save:
        if not csv_path:
            csv_path = str(output_dir / f"{stem}_report.csv")
        if not json_path:
            json_path = str(output_dir / f"{stem}_report.json")

    # Attach file handler as early as possible so all subsequent log
    # messages are captured.
    if log_file_path:
        _add_file_handler(log_file_path)
        logger.info("Log file: %s", log_file_path)

    # ------------------------------------------------------------------ #
    # Log run configuration                                               #
    # ------------------------------------------------------------------ #
    logger.info("=" * 60)
    logger.info("download_and_analyze_results.py")
    logger.info("  benchmark_id : %s", benchmark_id)
    logger.info("  bucket       : %s", args.bucket)
    logger.info("  output_dir   : %s", output_dir)
    logger.info("  skip_download: %s", args.skip_download)
    logger.info("  auto_save    : %s", args.auto_save)
    if log_file_path:
        logger.info("  log_file     : %s", log_file_path)
    if report_file_path:
        logger.info("  report_file  : %s", report_file_path)
    if csv_path:
        logger.info("  csv          : %s", csv_path)
    if json_path:
        logger.info("  json         : %s", json_path)
    logger.info("=" * 60)

    # ------------------------------------------------------------------ #
    # Load data                                                           #
    # ------------------------------------------------------------------ #
    if args.skip_download:
        local_dir = Path(args.local_dir) if args.local_dir else output_dir
        logger.info(
            "Loading from local directory: %s", local_dir / benchmark_id
        )
        data = load_from_local(benchmark_id, local_dir)
    else:
        client = _storage_client()
        bucket = client.bucket(args.bucket)
        logger.info(
            "Downloading benchmark '%s' from gs://%s",
            benchmark_id,
            args.bucket,
        )
        data = download_benchmark(bucket, benchmark_id, output_dir)

    if not data:
        logger.error("No data found for benchmark '%s'", benchmark_id)
        return 1

    # ------------------------------------------------------------------ #
    # Analyse                                                             #
    # ------------------------------------------------------------------ #
    logger.info("Starting analysis…")
    t_analyse = time.monotonic()
    records = analyse(benchmark_id, data)
    logger.info("Analysis complete in %.2fs", time.monotonic() - t_analyse)

    if not records:
        logger.warning("No variants to report on.")
        return 0

    # ------------------------------------------------------------------ #
    # Report                                                              #
    # ------------------------------------------------------------------ #
    print_report(benchmark_id, records, report_file=report_file_path)

    if csv_path:
        write_csv(records, csv_path)

    if json_path:
        write_json(records, json_path)

    elapsed_total = time.monotonic() - run_start
    logger.info("Total run time: %.1fs", elapsed_total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
