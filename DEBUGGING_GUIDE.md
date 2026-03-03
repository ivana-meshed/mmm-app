# Debugging Guide for Benchmark Issues

## Overview

This guide explains how to debug the two main issues:
1. Missing columns in CSV results (e.g., adstock)
2. Empty benchmark page

## Issue 1: Missing Columns in CSV

### What Was Fixed

The `_extract_metrics()` function now extracts configuration fields from `model_summary.json` first, falling back to the variant dict if not found.

**Fields affected:**
- `adstock`
- `train_size`
- `iterations`
- `trials`
- `resample_freq`

### How to Debug

Run analysis with `--debug` flag:

```bash
python scripts/analyze_benchmark_results.py \
  --benchmark-id comprehensive_benchmark_20260122_113141_20260225_112436 \
  --debug
```

### What to Look For

**Good output (config found in summary):**
```
Extracting metrics for geometric_70_90_daily_spend_to_spend:
  adstock: geometric (from summary)
  train_size: 0.7 (from summary)
  iterations: 10 (from variant)
  rsq_val: 0.85
```

**Problem output (config missing):**
```
Extracting metrics for geometric_70_90_daily_spend_to_spend:
  adstock:  (from variant)  ← Empty!
  train_size:  (from variant)  ← Empty!
```

If you see empty values, it means:
1. The model_summary.json doesn't have these fields
2. The variant dict also doesn't have them
3. Need to check why R script didn't save config to model_summary.json

### Verification

Check a specific model_summary.json file:

```bash
gsutil cat gs://mmm-app-output/robyn/default/de/20260225_112436/model_summary.json | jq .adstock
gsutil cat gs://mmm-app-output/robyn/default/de/20260225_112436/model_summary.json | jq .train_size
```

Should see actual values like:
```json
"geometric"
0.7
```

## Issue 2: Empty Benchmark Page

### What Was Fixed

Added comprehensive error handling and debug output to the Streamlit page.

### How to Debug

1. Open the Benchmark Results page in Streamlit
2. Look for debug output on the page itself:

```
🔍 DEBUG: Searching GCS path: gs://mmm-app-output/benchmarks/
🔍 DEBUG: Found 3 prefixes
  - Found: comprehensive_benchmark_20260122_113141_20260225_112436
  - Found: adstock_comparison_20260225_110000
✅ DEBUG: Total benchmarks found: 2
```

### Common Issues and Solutions

#### No Benchmarks Found (0 prefixes)

**Cause:** No benchmark folders in GCS

**Verify:**
```bash
gsutil ls gs://mmm-app-output/benchmarks/
```

**Solution:** Run a benchmark first:
```bash
python scripts/run_full_benchmark.py --path <path> --test-run
```

#### Permission Error

**Output:**
```
❌ Error listing benchmarks: 403 Insufficient Permission
```

**Solution:** Check GCP authentication:
```bash
gcloud auth application-default login
```

#### Module Not Found

**Output:**
```
❌ Error listing benchmarks: No module named 'google.cloud'
```

**Solution:** Install requirements:
```bash
pip install -r requirements.txt
```

## Result Collection Debugging

### Understanding the Logs

When running `analyze_benchmark_results.py`, you'll see:

**For each variant:**
```
Collecting result for variant: geometric_70_90_daily_spend_to_spend
  Variant config: adstock=geometric, train_size=0.7
  Using timestamp from queue: 20260225_112436
  Trying exact path: robyn/default/de/20260225_112436/model_summary.json
  ✓ Found result at exact path
```

**When timestamp mapping works:**
- Shows "Using timestamp from queue: ..."
- Tries exact path
- Usually succeeds quickly

**When timestamp mapping fails:**
```
  No timestamp found in map for weibull_cdf_75_90_weekly_mixed
  Falling back to search in: robyn/default/de/
  Found 23 model_summary.json files
  Checking blob 1/23: ...
```

### Common Problems

#### "No timestamp found in map"

**Cause:** Queue doesn't have an entry for this variant

**Check:**
```bash
gsutil cat gs://mmm-app-output/robyn-queues/default-dev/queue.json | jq '.[] | select(.benchmark_variant == "geometric_70_90_daily_spend_to_spend")'
```

Should show a completed entry with `gcs_prefix` containing timestamp.

**Solution:** The job may not have run yet or failed. Check queue status.

#### "Exact path not found"

**Cause:** Result not at expected location

**Verify:**
```bash
gsutil ls gs://mmm-app-output/robyn/default/de/20260225_112436/
```

Should see `model_summary.json` and other files.

**Solution:** 
- Check if job actually completed
- Verify timestamp is correct
- Check GCS upload worked

#### "NO RESULTS FOUND for variant"

**Cause:** Neither exact path nor fallback matching found results

**Debug steps:**
1. Check if any results exist for country:
   ```bash
   gsutil ls gs://mmm-app-output/robyn/default/de/
   ```

2. Check a recent model_summary.json:
   ```bash
   gsutil cat gs://mmm-app-output/robyn/default/de/<timestamp>/model_summary.json | jq .
   ```

3. Check if config matches:
   ```bash
   # Check adstock in summary
   gsutil cat gs://mmm-app-output/robyn/default/de/<timestamp>/model_summary.json | jq .adstock
   
   # Should match variant's expected adstock
   ```

## Quick Debugging Workflow

1. **Run analysis with debug flag:**
   ```bash
   python scripts/analyze_benchmark_results.py --benchmark-id <id> --debug 2>&1 | tee debug.log
   ```

2. **Check for missing timestamps:**
   ```bash
   grep "No timestamp found" debug.log
   ```

3. **Check for failed result collections:**
   ```bash
   grep "NO RESULTS FOUND" debug.log
   ```

4. **For each failed variant, verify in GCS:**
   ```bash
   gsutil ls gs://mmm-app-output/robyn/default/de/ | tail -20
   ```

5. **Check queue entries:**
   ```bash
   gsutil cat gs://mmm-app-output/robyn-queues/default-dev/queue.json | jq '.[] | select(.status == "completed") | .benchmark_variant'
   ```

## Expected File Structure

```
gs://mmm-app-output/
├── benchmarks/
│   └── comprehensive_benchmark_20260122_113141_20260225_112436/
│       ├── plan.json
│       ├── results_20260225_114052.csv
│       └── plots_20260225_114053/
│           ├── rsq_comparison.png
│           ├── nrmse_comparison.png
│           └── ...
├── robyn/
│   └── default/
│       └── de/
│           ├── 20260225_112436/
│           │   ├── model_summary.json  ← Must have config fields
│           │   ├── console.log
│           │   └── ...
│           ├── 20260225_112520/
│           └── ...
└── robyn-queues/
    └── default-dev/
        └── queue.json  ← Must have completed entries with gcs_prefix
```

## Getting Help

If issues persist after debugging:

1. Share the debug log:
   ```bash
   python scripts/analyze_benchmark_results.py --benchmark-id <id> --debug > debug.log 2>&1
   ```

2. Share benchmark page output (copy debug messages from UI)

3. Share a sample model_summary.json:
   ```bash
   gsutil cat gs://mmm-app-output/robyn/default/de/<timestamp>/model_summary.json > sample_summary.json
   ```

4. Share queue status:
   ```bash
   gsutil cat gs://mmm-app-output/robyn-queues/default-dev/queue.json | jq '.[] | select(.benchmark_test != null)' > queue_benchmarks.json
   ```
