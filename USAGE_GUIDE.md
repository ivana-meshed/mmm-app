# Usage Guide - Benchmarking System

## One-Line Command (Recommended)

The easiest way to run a complete benchmark workflow:

```bash
# Test run (default - 10 iterations, 1 trial)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json

# Standard run (1000 iterations, 3 trials)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json \
  --full-run

# Extended run (2000 iterations, 5 trials)
python scripts/run_full_benchmark.py \
  --path <path_to_selected_columns.json> \
  --extended-run

# Production run (5000 iterations, 5 trials)
python scripts/run_full_benchmark.py \
  --path <path_to_selected_columns.json> \
  --production-run

# Top-N combinations
python scripts/run_full_benchmark.py \
  --path <path_to_selected_columns.json> \
  --top-n 10 --extended-run

# With custom queue name
python scripts/run_full_benchmark.py \
  --path <path_to_selected_columns.json> \
  --queue-name default-dev

# With per-channel hyperparameter ranges (balanced preset is the default)
python scripts/run_full_benchmark.py \
  --path <path_to_selected_columns.json> \
  --full-run \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments.json

# With an explicit hyperparameter preset
python scripts/run_full_benchmark.py \
  --path <path_to_selected_columns.json> \
  --full-run \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments.json \
  --hyperparameter-preset exploratory
```

**What it does:**
1. Downloads selected_columns.json from GCS
2. Generates comprehensive benchmark — default **18 variants** (1 adstock × 3 splits × 2 time_agg × 3 spend_var).
   Non-test runs include the `full` training window by default.
   Use `--all-adstock` for 54 variants, `--sequential` for 9 variants (per-dimension).
3. Submits all test combinations to queue
4. Processes queue until complete
5. Analyzes results and creates visualizations

**Output:**
- CSV: `./benchmark_analysis/results_{timestamp}.csv`
- Plots: `./benchmark_analysis/*.png` (6 plots)

**Expected time:**
- Test run: ~1-2 hours for 54 variants
- Full run: ~4-6 hours for 54 variants

---

## Manual Workflow (Alternative)

If you prefer step-by-step control, follow these scenarios:

## Quick Start (5 Minutes)

```bash
# 1. Authenticate with GCP
gcloud auth application-default login --impersonate-service-account=mmm-web-service-sa@datawarehouse-422511.iam.gserviceaccount.com

# 2. Run quick test
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all

# 3. Process queue
python scripts/process_queue_simple.py --loop --cleanup

# Expected: ~13 jobs complete in 1-2 hours
```

## Prerequisites

### 1. Authentication Setup

```bash
# Set up Application Default Credentials with impersonation
gcloud auth application-default login \
  --impersonate-service-account=mmm-web-service-sa@datawarehouse-422511.iam.gserviceaccount.com

# Verify authentication
gcloud auth application-default print-access-token
```

### 2. Required Access

- Read access to `mmm-app-output` GCS bucket
- Write access to `robyn-queues/` and `training-configs/` folders
- Permission to launch Cloud Run Jobs (`mmm-app-dev-training`)

### 3. Python Environment

```bash
# Activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Usage Scenarios

### Scenario 1: Quick Validation (5-10 minutes)

**Purpose:** Verify setup works before running full benchmarks

```bash
# Test single benchmark (first variant only)
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --test-run

# Process the job
python scripts/process_queue_simple.py --loop --cleanup
```

**Expected output:**
- 1 job submitted
- Completes in ~5-10 minutes
- Results at `gs://mmm-app-output/robyn/default/de/{timestamp}/`

### Scenario 2: Test Queue with Multiple Jobs (15-30 minutes)

**Purpose:** Validate queue processing with multiple jobs

```bash
# Test all variants of one benchmark
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --test-run-all

# Process queue
python scripts/process_queue_simple.py --loop --cleanup
```

**Expected output:**
- 3 jobs submitted (geometric, weibull_cdf, weibull_pdf)
- Each completes in ~5-10 minutes
- Total time: ~15-30 minutes

### Scenario 3: Single Full Benchmark (1-2 hours)

**Purpose:** Run complete benchmark with full iterations/trials

```bash
# Run one benchmark type
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json

# Process queue
python scripts/process_queue_simple.py --loop --cleanup
```

**Expected output:**
- 3 jobs submitted
- Each runs 2000 iterations, 5 trials
- Each takes ~30-40 minutes
- Total time: ~1.5-2 hours

### Scenario 4: All Benchmarks Quick Test (1-2 hours)

**Purpose:** Test all benchmark types with reduced resources

```bash
# Run ALL benchmarks with test mode
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all

# Process queue
python scripts/process_queue_simple.py --loop --cleanup
```

**Expected output:**
- 13 jobs submitted (across 4 benchmark types)
- Each job: 10 iterations, 1 trial
- Total time: ~1-2 hours

### Scenario 5: Full Benchmark Suite (4-6 hours)

**Purpose:** Complete benchmarking for production decisions

```bash
# Run ALL benchmarks with full settings
python scripts/benchmark_mmm.py --all-benchmarks

# Process queue (can run in background)
nohup python scripts/process_queue_simple.py --loop --cleanup > queue.log 2>&1 &

# Check progress
tail -f queue.log
```

**Expected output:**
- 13 jobs submitted
- Full iterations/trials per job
- Total time: ~4-6 hours

## Command Reference

### benchmark_mmm.py

```bash
# List available benchmarks
python scripts/benchmark_mmm.py --list-configs

# Preview without submitting
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --dry-run

# Run specific benchmark
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json

# Test modes
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run-all

# Run all benchmarks
python scripts/benchmark_mmm.py --all-benchmarks
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all
python scripts/benchmark_mmm.py --all-benchmarks --dry-run

# Custom queue
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --queue-name custom-queue

# Find results
python scripts/benchmark_mmm.py --list-results benchmark_id
python scripts/benchmark_mmm.py --show-results-location benchmark_id

# Collect results
python scripts/benchmark_mmm.py --collect-results benchmark_id --export-format csv
python scripts/benchmark_mmm.py --collect-results benchmark_id --export-format parquet
```

### analyze_benchmark_results.py

```bash
# Analyze results and generate plots
python scripts/analyze_benchmark_results.py --benchmark-id benchmark_id

# Save plots and CSV locally
python scripts/analyze_benchmark_results.py --benchmark-id benchmark_id --output-dir ./results

# Custom plot format
python scripts/analyze_benchmark_results.py --benchmark-id benchmark_id --format pdf

# CSV only (no plots)
python scripts/analyze_benchmark_results.py --benchmark-id benchmark_id --no-plots
```

**What it generates:**
- CSV export with all metrics
- R² comparison plot
- NRMSE comparison plot
- Decomposition RSSD plot
- Train/val/test gap analysis
- Metric correlations heatmap
- Best models summary

### run_full_benchmark.py (One-Line Command)

```bash
# Test run (default - geometric only, ~10 combos)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json

# Standard run, geometric only (18 combos cartesian, full window default)
python scripts/run_full_benchmark.py \
  --path <path_to_selected_columns.json> \
  --full-run

# Sequential run — each dimension tested independently (9 combos, faster exploration)
python scripts/run_full_benchmark.py \
  --path <path_to_selected_columns.json> \
  --full-run --sequential

# Extended run (2000 iterations, 5 trials)
python scripts/run_full_benchmark.py \
  --path <path> \
  --extended-run

# Production run (5000 iterations, 5 trials)
python scripts/run_full_benchmark.py \
  --path <path> \
  --production-run

# All adstock types, cartesian (54 combos)
python scripts/run_full_benchmark.py \
  --path <path> \
  --full-run --all-adstock

# Window-length sweep (full + 2y + 3y, multiplies combos by 3)
python scripts/run_full_benchmark.py \
  --path <path> \
  --full-run --all-windows

# Top-N combinations (5, 10, or any number)
python scripts/run_full_benchmark.py \
  --path <path> \
  --top-n 10 --extended-run

# With custom queue
python scripts/run_full_benchmark.py \
  --path <path> \
  --queue-name default-dev

# Skip queue processing (submit only)
python scripts/run_full_benchmark.py \
  --path <path> \
  --skip-queue

# Skip analysis (submit and process only)
python scripts/run_full_benchmark.py \
  --path <path> \
  --skip-analysis

# With per-channel hyperparameter ranges
python scripts/run_full_benchmark.py \
  --path <path> \
  --full-run \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments.json

# With an explicit hyperparameter preset
python scripts/run_full_benchmark.py \
  --path <path> \
  --full-run \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments.json \
  --hyperparameter-preset exploratory
```

**What it does:**
1. Downloads selected_columns.json from GCS
2. Generates comprehensive benchmark:
   - Default: **18 variants** cartesian (1 adstock × 3 splits × 2 time_agg × 3 spend_var)
   - `--all-adstock`: 54 variants (3 adstock × 3 × 2 × 3)
   - `--sequential`: 9 variants (each dimension varied independently)
   - Non-test runs include `full` training window by default
3. Submits all jobs to queue
4. Processes queue until empty
5. Analyzes results and saves to `./benchmark_analysis/`

**Sequential mode strategy (--sequential):**

Each dimension is tested one at a time using base-config defaults for the other dimensions.
Testing order is chosen to progressively build on results:

1. **adstock** — most fundamental choice; affects model fit and decomposition
2. **train_splits** — evaluation methodology; how much data to hold out
3. **time_aggregation** — daily vs weekly granularity
4. **spend_var_mapping** — media signal strategy (spend vs proxy)
5. **seasonality_window** — training period length (if `--all-windows` or `--windows` given)

Use sequential mode for initial exploration before committing to a full cartesian sweep.

### process_queue_simple.py

```bash
# Process queue until empty
python scripts/process_queue_simple.py --loop

# With cleanup (recommended)
python scripts/process_queue_simple.py --loop --cleanup

# Process single job
python scripts/process_queue_simple.py

# Custom queue
python scripts/process_queue_simple.py --queue-name custom-queue --loop

# Keep more completed jobs
python scripts/process_queue_simple.py --loop --cleanup --keep-count 20
```

## Verification Steps

### Step 1: Check Job Submission

```bash
# After running benchmark_mmm.py, check output shows:
✅ Benchmark submitted successfully!
Benchmark ID: adstock_comparison_20260212_151148
Variants queued: 3
Queue: default-dev
Plan: gs://mmm-app-output/benchmarks/adstock_comparison_20260212_151148/plan.json
```

### Step 2: Monitor Queue Processing

```bash
# During process_queue_simple.py, watch for:
📊 Queue Status: default-dev
  Total: 12
  Pending: 3
  Running: 0
  Completed: 9

Processing job 10/12
✅ Launched job: mmm-app-dev-training
```

### Step 3: Verify Job Completion

```bash
# Look for completion messages:
✅ Job completed: geometric
   Results at: gs://mmm-app-output/robyn/default/de/20260212_172440_243/
   Verifying results in GCS...
   ✓ Results verified: Found 12 files
   ✓ Key files found: model_summary.json, best_model_plots.png, console.log
```

### Step 4: Check GCS for Results

```bash
# List result folders
gsutil ls gs://mmm-app-output/robyn/default/de/ | tail -5

# Check specific result
gsutil ls gs://mmm-app-output/robyn/default/de/20260212_172440_243/

# Expected files:
# - model_summary.json
# - console.log
# - best_model_plots.png
# - allocator_metrics.csv
# - status.json
# - InputCollect.RDS
# - OutputCollect.RDS
```

## Troubleshooting

### Issue: "unrecognized arguments"

**Solution:** Ensure you're on the correct branch
```bash
git branch  # Should show: * copilot/follow-up-on-pr-170
git checkout copilot/follow-up-on-pr-170
```

### Issue: "Permission denied"

**Solution:** Re-authenticate
```bash
gcloud auth application-default login \
  --impersonate-service-account=mmm-web-service-sa@datawarehouse-422511.iam.gserviceaccount.com
```

### Issue: "No files found after 10s timeout"

**Possible causes:**
1. Job still running - wait longer
2. Job failed - check Cloud Run logs
3. Wrong GCS path - verify timestamp in logs

**Check Cloud Run logs:**
```bash
gcloud logging read "resource.type=cloud_run_job" --limit 50
```

### Issue: Jobs stuck in "running"

**Solution:**
1. Check Cloud Run console for job status
2. Review execution logs for errors
3. Cancel stuck job if necessary
4. Resubmit with --test-run first

### Issue: Results collection fails

**Solution:**
```bash
# Verify benchmark ID
python scripts/benchmark_mmm.py --list-results benchmark_id

# Check GCS manually
gsutil ls gs://mmm-app-output/benchmarks/{benchmark_id}/

# Try different export format
python scripts/benchmark_mmm.py --collect-results benchmark_id --export-format csv
```

## Tips & Best Practices

### 1. Start Small
- Always test with `--test-run` first
- Validate one benchmark before running all
- Use `--dry-run` to preview

### 2. Monitor Progress
- Keep queue processor running with `--loop`
- Use `--cleanup` to manage completed jobs
- Check Cloud Run console for job status

### 3. Verify Results
- Check GCS immediately after completion
- Review console.log for errors
- Validate metrics make sense

### 4. Resource Management
- Use `--test-run-all` for queue validation
- Full runs consume significant compute
- Consider cost before running all benchmarks

### 5. Background Processing
- Long runs should use nohup or tmux
- Monitor with `tail -f` on log files
- Set up alerts for failures

## Expected Timings

| Scenario | Jobs | Mode | Per Job | Total |
|----------|------|------|---------|-------|
| Quick test | 1 | --test-run | 5-10 min | 5-10 min |
| Queue test | 3 | --test-run-all | 5-10 min | 15-30 min |
| Single benchmark | 3 | Full | 30-40 min | 1.5-2 hrs |
| All benchmarks test | 13 | --test-run-all | 5-10 min | 1-2 hrs |
| All benchmarks full | 13 | Full | 30-40 min | 4-6 hrs |

## Next Steps

After successful execution:
1. Verify all results exist in GCS
2. Collect results for analysis
3. See **ANALYSIS_GUIDE.md** for result analysis
4. Make configuration decisions based on findings

---

## Advanced Troubleshooting

### Debugging Missing Columns in CSV

If CSV results are missing columns (e.g., adstock, resample_freq), run analysis with `--debug` flag:

```bash
python scripts/analyze_benchmark_results.py \
  --benchmark-id <your_benchmark_id> \
  --debug
```

**Good output (config found):**
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
```

Verify fields in model_summary.json:
```bash
gsutil cat gs://mmm-app-output/robyn/default/de/<timestamp>/model_summary.json | jq .adstock
gsutil cat gs://mmm-app-output/robyn/default/de/<timestamp>/model_summary.json | jq .train_size
```

### Debugging Empty Benchmark Page in Streamlit

Open the Benchmark Results page and look for debug output:
```
🔍 DEBUG: Searching GCS path: gs://mmm-app-output/benchmarks/
🔍 DEBUG: Found 3 prefixes
  - Found: comprehensive_benchmark_20260122_113141_20260225_112436
✅ DEBUG: Total benchmarks found: 2
```

**No benchmarks found (0 prefixes):** Run a benchmark first:
```bash
python scripts/run_full_benchmark.py --path <path>
```

**Permission error:** Re-authenticate:
```bash
gcloud auth application-default login
```

**Module not found:** Install requirements:
```bash
pip install -r requirements.txt
```

### Debugging Result Collection

Run analysis with debug flag and save to log:
```bash
python scripts/analyze_benchmark_results.py --benchmark-id <id> --debug 2>&1 | tee debug.log
```

**Check for missing timestamps:**
```bash
grep "No timestamp found" debug.log
```

**Check for failed result collections:**
```bash
grep "NO RESULTS FOUND" debug.log
```

**For each failed variant, verify in GCS:**
```bash
gsutil ls gs://mmm-app-output/robyn/default/de/ | tail -20
```

**Check queue entries:**
```bash
gsutil cat gs://mmm-app-output/robyn-queues/default-dev/queue.json | jq '.[] | select(.status == "completed") | .benchmark_variant'
```

### Expected File Structure

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
│           └── ...
└── robyn-queues/
    └── default-dev/
        └── queue.json  ← Must have completed entries with gcs_prefix
```

---

## Fleet / Mobility Marketplace Dataset

This section covers running benchmarks for datasets that follow the fleet/mobility marketplace column schema (Google Search, Meta, Bing, Facebook sub-channels, CRM/newsletter organic, and fleet context variables such as availability rate, occupancy, and location count).

### Config Files

| File | Purpose |
|------|---------|
| `benchmarks/channel_type_assignments_fleet_marketplace.json` | Maps all spend, impressions, and clicks column names to channel types in `generic_hyperparameter_ranges_v2.json` |
| `benchmarks/comprehensive_benchmark_fleet_marketplace.json` | Default 30-variant benchmark (geometric adstock × 3 train splits × 2 time aggregations × 5 spend-var mappings). Use `--all-adstock` to extend to 90 variants across all 3 adstock types. |

### Before Running

Edit `benchmarks/comprehensive_benchmark_fleet_marketplace.json` and replace `"FILL_IN_COUNTRY_CODE"` with your country code. Optionally change `"goal"` to `"gmv_net_eur"` or `"gmv_gross_eur"` instead of `"bookings"`.

### One-Line Command

```bash
# Default run — geometric adstock only, test mode (10 iterations, 1 trial, ~30 min, ~$5)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/<country>/<goal>/<version>/selected_columns.json \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json

# Standard run — geometric only (1000 iterations, 3 trials, ~2 h, ~$25)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/<country>/<goal>/<version>/selected_columns.json \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --full-run

# Extended run — geometric only (2000 iterations, 5 trials, ~4-5 h, ~$70)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/<country>/<goal>/<version>/selected_columns.json \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --extended-run

# Production run — geometric only (5000 iterations, 5 trials, ~10-12 h, ~$165)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/<country>/<goal>/<version>/selected_columns.json \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --production-run
```

### All Combinations (Balanced Preset)

To test all adstock types (geometric + weibull_cdf + weibull_pdf) × all window lengths × all spend-var mappings with the `balanced` hyperparameter preset:

```bash
# All combinations — standard run (90 adstock variants × 3 windows = 270 combos, ~$200)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/<country>/<goal>/<version>/selected_columns.json \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --hyperparameter-preset balanced \
  --all-adstock --all-windows --full-run --top-n 270
```

To run only the top-N variants after screening, first run a test sweep and then re-run with `--top-n`:

```bash
# Step 1: test sweep — all adstock, all windows, test mode (~$30)
python scripts/run_full_benchmark.py \
  --path <path> --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --hyperparameter-preset balanced --all-adstock --all-windows

# Step 2: production run on top-10 (~$95)
python scripts/run_full_benchmark.py \
  --path <path> --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --hyperparameter-preset balanced --all-adstock --all-windows --top-n 10 --production-run
```

### Recommended Quick Starts

```bash
# Thorough analysis — geometric adstock, top-10 variants, extended run (~$35, ~2-3 h)
python scripts/run_full_benchmark.py \
  --path <path> \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --top-n 10 --extended-run

# Production quality — geometric adstock, top-10 variants (~$55, ~4-5 h)
python scripts/run_full_benchmark.py \
  --path <path> \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --top-n 10 --production-run
```

### Cost Estimates

> **Variant counts explained:**  
> - **Dynamic config** (`run_full_benchmark.py --path` without a fleet-specific JSON): 3 spend-var mappings → **18 base combos** per adstock type (1 × 3 splits × 2 time-agg × 3 spend-var).  
> - **Fleet marketplace config** (`comprehensive_benchmark_fleet_marketplace.json`): 5 spend-var mappings → **30 base combos** per adstock type (1 × 3 × 2 × 5).  
> Adding `--all-windows` multiplies by 3; `--all-adstock` multiplies by 3.

#### Cartesian mode (default)

| Run mode | Adstock | Windows | Variants | Approx. time | Approx. cost | One-liner flag(s) |
|----------|---------|---------|----------|-------------|-------------|-----------------|
| Test (default) | geometric | — | 18 | ~25 min | ~$5 | _(none)_ |
| Test (all adstock) | all 3 | — | 54 | ~1.5 h | ~$15 | `--all-adstock` |
| Test (all adstock + all windows) | all 3 | full / 2y / 3y | 162 | ~4 h | ~$45 | `--all-adstock --all-windows` |
| Standard | geometric | full | 18 | ~2 h | ~$25 | `--full-run` |
| Standard (window sweep, geometric) | geometric | full / 2y / 3y | 54 | ~6 h | ~$75 | `--full-run --all-windows` |
| Standard (2 windows, geometric) | geometric | 2y / 3y | 36 | ~4 h | ~$50 | `--full-run --windows 2y 3y` |
| Standard (all adstock) | all 3 | full | 54 | ~6 h | ~$75 | `--full-run --all-adstock` |
| Standard (all adstock + all windows) | all 3 | full / 2y / 3y | 162 | ~18 h | ~$225 | `--full-run --all-adstock --all-windows` |
| Extended | geometric | full | 18 | ~4-5 h | ~$70 | `--extended-run` |
| Extended (window sweep, geometric) | geometric | full / 2y / 3y | 54 | ~12-15 h | ~$210 | `--extended-run --all-windows` |
| Extended (all adstock) | all 3 | full | 54 | ~12-15 h | ~$210 | `--extended-run --all-adstock` |
| Extended (all adstock + all windows) | all 3 | full / 2y / 3y | 162 | ~35-40 h | ~$630 | `--extended-run --all-adstock --all-windows` |
| Production | geometric | full | 18 | ~10-12 h | ~$165 | `--production-run` |
| Production (window sweep, geometric) | geometric | full / 2y / 3y | 54 | ~30-35 h | ~$495 | `--production-run --all-windows` |
| Production (all adstock) | all 3 | full | 54 | ~30-35 h | ~$495 | `--production-run --all-adstock` |
| Production (all adstock + all windows) | all 3 | full / 2y / 3y | 162 | ~90+ h | ~$1,485 | `--production-run --all-adstock --all-windows` |
| Top-10 extended | geometric | full | 10 | ~1.5 h | ~$23 | `--top-n 10 --extended-run` |
| Top-10 production | geometric | full | 10 | ~3-4 h | ~$55 | `--top-n 10 --production-run` |

#### Sequential mode (`--sequential`)

In sequential mode each dimension is varied **independently** (other dimensions held at baseline), so combinations add rather than multiply:  
`n = n_adstock + 3 splits + 2 time-agg + 3 spend-var [+ n_windows if >1]`

| Run mode | Adstock | Windows | Variants | Approx. time | Approx. cost | One-liner flag(s) |
|----------|---------|---------|----------|-------------|-------------|-----------------|
| Sequential standard | geometric | — | 9 | ~1 h | ~$12 | `--full-run --sequential` |
| Sequential standard (all windows) | geometric | full / 2y / 3y | 12 | ~1.5 h | ~$17 | `--full-run --sequential --all-windows` |
| Sequential standard (all adstock) | all 3 | — | 11 | ~1.5 h | ~$18 | `--full-run --sequential --all-adstock` |
| Sequential extended | geometric | — | 9 | ~3-4 h | ~$35 | `--extended-run --sequential` |
| Sequential production | geometric | — | 9 | ~8-10 h | ~$83 | `--production-run --sequential` |

### Window Length

By default, the benchmark uses the full training window defined in `selected_columns.json`. You can add a **window sweep** to test whether a shorter lookback period improves model fit:

| Flag | Description |
|------|-------------|
| `--all-windows` | Test 3 window lengths: `full`, `2y` (last 104 weeks), `3y` (last 156 weeks). Multiplies total variants by 3. |
| `--windows 2y 3y` | Test only selected window lengths (choose any subset of `full`, `2y`, `3y`). |

Example — standard run with 2-year and 3-year windows:
```bash
python scripts/run_full_benchmark.py \
  --path <path> \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --full-run --windows 2y 3y
```

Window lengths are applied as a trailing lookback from the latest data date:
- `full` — all available history (no truncation)
- `3y` — last 156 weeks (~3 years)
- `2y` — last 104 weeks (~2 years)

**How window dates flow through the system:**

1. `--windows` / `--all-windows` → `weeks_back` stored in `ALL_WINDOW_VARIANTS`
2. `generate_benchmark_config()` resolves `weeks_back` to an absolute `start_date` / `end_date` using the `end_date` from `selected_columns.json`
3. Each queue entry carries `start_date` and `end_date`
4. The R training script (`r/run_all.R`) reads these values and passes them directly to `robyn_inputs(window_start=..., window_end=...)` to filter training data

Non-test runs automatically include a `full` window variant so every result row carries an explicit `seasonality_window` label.

---

### Hyperparameter Presets

Presets control the range of hyperparameters (alpha, gamma, theta) passed to Robyn for each channel. Three built-in presets are available:

| Preset | Alpha | Gamma | Theta | Use case |
|--------|-------|-------|-------|----------|
| `conservative` | Narrow ranges, low saturation | Narrow | Low carryover | Stable baselines, short-cycle products |
| `balanced` | Moderate ranges (default) | Moderate | Moderate | General purpose — recommended starting point |
| `exploratory` | Wide ranges, high saturation | Wide | High carryover | Complex markets, long-cycle products |

**Preset precedence** (highest to lowest):

1. **Variant-level preset** — set directly on an adstock spec in the JSON config:
   ```json
   {"name": "weibull_cdf", "adstock": "weibull_cdf", "hyperparameter_preset": "Meta default"}
   ```
2. **Benchmark-level preset** — `--hyperparameter-preset` CLI flag or `"hyperparameter_preset"` key in the benchmark JSON
3. **Default** — `"balanced"` when nothing is set

When `--hyperparameter-ranges-config` is provided, per-channel ranges are resolved for each variant using that variant's effective preset. After resolution, the preset is set to `"Custom"` and the ranges are embedded directly in the queue entry as `custom_hyperparameters`. The R training script then uses these exact ranges instead of the built-in preset lookup.

**Example — use different presets per adstock type:**
```json
{
  "variants": {
    "adstock": [
      {"name": "geometric",   "adstock": "geometric",   "hyperparameter_preset": "Meshed recommend"},
      {"name": "weibull_cdf", "adstock": "weibull_cdf", "hyperparameter_preset": "Meta default"},
      {"name": "weibull_pdf", "adstock": "weibull_pdf", "hyperparameter_preset": "balanced"}
    ]
  }
}
```

Each adstock variant will resolve its per-channel ranges using its own preset when `--hyperparameter-ranges-config` is set.

**Without per-channel ranges config:** the `hyperparameter_preset` value is forwarded as-is to the R training script, which applies built-in preset lookups. In this case variant-level presets are also respected.

---

### Column Schema

The benchmark config assumes `selected_columns.json` in GCS defines the following columns.

**paid_media_spends** (priority-1 channels, in order):
```
meta_total_spend
google_search_brand_cost
google_search_nonbrand_cost
google_pmax_cost
google_display_cost
google_youtube_cost
bing_search_cost
```

Optional additional spend columns (add to `selected_columns.json` when available):
```
google_search_nonbrand_cost_2, google_pmax_cost_2, google_other_cost_2
fb_video_cost, fb_static_cost, fb_upper_cost, fb_lower_cost, fb_app_cost
```

**paid_media_vars** (proxy columns for impression/clicks-based spend-var variants):

*Impressions:*
```
meta_total_impressions
google_search_brand_impressions
google_search_nonbrand_impressions
google_pmax_impressions
google_display_impressions
google_youtube_impressions
bing_search_impressions
```

*Clicks:*
```
meta_total_clicks
google_search_brand_clicks
google_search_nonbrand_clicks
google_pmax_clicks
google_display_clicks
google_youtube_clicks
bing_search_clicks
```

**organic_vars**:
```
crm_email_sends_marketing
crm_push_sends_marketing
newsletter_sends_marketing
```

**context_vars** (priority-1):
```
availability_rate
occupancy_rate
fleet_available_units
n_locations
```

**factor_vars**:
```
holiday_flag
```

**dep_var** (choose one):
```
bookings          # primary recommendation
gmv_net_eur
gmv_gross_eur
```

### Spend-var Mapping Variants

| Variant | paid_media_vars | Best for |
|---------|----------------|---------|
| `spend_to_spend` | = paid_media_spends | Attribution via cost directly |
| `spend_to_impressions` | impression columns (all) | Ad delivery volume signal |
| `spend_to_clicks` | click columns (all) | Engagement-weighted signal |
| `mixed_by_funnel_impressions` | awareness channels → impressions; search/pmax → spend | Funnel-aware hybrid (impressions) |
| `mixed_by_funnel_clicks` | awareness channels → clicks; search/pmax → spend | Funnel-aware hybrid (clicks) |

For `mixed_by_funnel_impressions` the per-channel mapping is:
- `meta_total_spend` → `meta_total_impressions` (upper funnel)
- `google_display_cost` → `google_display_impressions` (upper funnel)
- `google_youtube_cost` → `google_youtube_impressions` (upper funnel)
- `google_search_brand_cost` → kept as spend (lower funnel)
- `google_search_nonbrand_cost` → kept as spend (lower funnel)
- `google_pmax_cost` → kept as spend (performance)
- `bing_search_cost` → kept as spend (lower funnel)

For `mixed_by_funnel_clicks` the same logic applies but awareness channels use clicks instead of impressions.

### Adjusting the Preset

The config uses `"hyperparameter_preset": "balanced"` by default. To use a different preset per adstock type, set the `hyperparameter_preset` field directly on each adstock variant in the JSON, or pass `--hyperparameter-preset exploratory` on the CLI to override all variants at once.

---

## Appendix: Benchmark Combinations Reference

This appendix enumerates every combination that `python scripts/run_full_benchmark.py --path <path> [flags]` will submit to the training queue, and lists the hyperparameter ranges that apply to each adstock type and preset.

> **Config basis:** all counts below use the **dynamically generated config** (3 spend-var mappings). The fleet marketplace JSON (`comprehensive_benchmark_fleet_marketplace.json`) adds 5 spend-var variants instead of 3, multiplying each count by 5/3.

---

### A. Dimension Reference

| Dimension | Values | Count |
|-----------|--------|-------|
| Adstock | `geometric`, `weibull_cdf`, `weibull_pdf` | up to 3 |
| Train split | `70_90` [0.7, 0.9], `75_90` [0.75, 0.9], `65_80` [0.65, 0.8] | 3 |
| Time aggregation | `daily` (resample_freq=none), `weekly` (resample_freq=W) | 2 |
| Spend-var mapping | `spend_to_spend`, `spend_to_proxy`, `mixed_by_funnel` | 3 |
| Seasonality window | `full` (all history), `2y` (104 weeks back), `3y` (156 weeks back) | up to 3 |

In **cartesian mode** (default): variants = ∏(selected dimension sizes).  
In **sequential mode** (`--sequential`): each dimension is varied in isolation; variants = Σ(selected dimension sizes).

**Baseline config** (used for the non-varying dimension in sequential mode):

| Dimension | Baseline value |
|-----------|---------------|
| Adstock | geometric |
| Train split | 70_90 |
| Time aggregation | daily |
| Spend-var mapping | spend_to_spend |
| Window | full (no date override) |

---

### B. Combination Count by Flag

| Command flags | Mode | Adstock(s) | Window(s) | Formula | Combos |
|--------------|------|-----------|----------|---------|--------|
| _(none — test default)_ | cartesian | geometric | — | 1×3×2×3 | **18** |
| `--all-adstock` | cartesian | all 3 | — | 3×3×2×3 | **54** |
| `--all-adstock --all-windows` | cartesian | all 3 | full/2y/3y | 3×3×2×3×3 | **162** |
| `--full-run` | cartesian | geometric | full | 1×3×2×3 | **18** |
| `--full-run --all-windows` | cartesian | geometric | full/2y/3y | 1×3×2×3×3 | **54** |
| `--full-run --windows 2y 3y` | cartesian | geometric | 2y/3y | 1×3×2×3×2 | **36** |
| `--full-run --windows 3y` | cartesian | geometric | 3y | 1×3×2×3 | **18** |
| `--full-run --all-adstock` | cartesian | all 3 | full | 3×3×2×3 | **54** |
| `--full-run --all-adstock --all-windows` | cartesian | all 3 | full/2y/3y | 3×3×2×3×3 | **162** |
| `--extended-run` | cartesian | geometric | full | 1×3×2×3 | **18** |
| `--extended-run --all-windows` | cartesian | geometric | full/2y/3y | 1×3×2×3×3 | **54** |
| `--extended-run --all-adstock` | cartesian | all 3 | full | 3×3×2×3 | **54** |
| `--extended-run --all-adstock --all-windows` | cartesian | all 3 | full/2y/3y | 3×3×2×3×3 | **162** |
| `--production-run` | cartesian | geometric | full | 1×3×2×3 | **18** |
| `--production-run --all-windows` | cartesian | geometric | full/2y/3y | 1×3×2×3×3 | **54** |
| `--production-run --all-adstock` | cartesian | all 3 | full | 3×3×2×3 | **54** |
| `--production-run --all-adstock --all-windows` | cartesian | all 3 | full/2y/3y | 3×3×2×3×3 | **162** |
| `--full-run --sequential` | sequential | geometric | full | 1+3+2+3 | **9** |
| `--full-run --sequential --all-windows` | sequential | geometric | full/2y/3y | 1+3+2+3+3 | **12** |
| `--full-run --sequential --all-adstock` | sequential | all 3 | full | 3+3+2+3 | **11** |
| `--top-n 10 --extended-run` | cartesian | geometric | full | first 10 of 18 | **10** |
| `--top-n 10 --production-run` | cartesian | geometric | full | first 10 of 18 | **10** |

---

### C. Full Combination Matrices

#### C.1 Default / Standard / Extended / Production — Geometric Only, Single Window

Applies to: `--full-run` / `--extended-run` / `--production-run` (18 combinations, all with `adstock=geometric`).

| # | Adstock | Train split | Time agg | Spend-var | Window |
|---|---------|-------------|---------|-----------|--------|
| 1 | geometric | 70_90 | daily | spend_to_spend | full |
| 2 | geometric | 70_90 | daily | spend_to_proxy | full |
| 3 | geometric | 70_90 | daily | mixed_by_funnel | full |
| 4 | geometric | 70_90 | weekly | spend_to_spend | full |
| 5 | geometric | 70_90 | weekly | spend_to_proxy | full |
| 6 | geometric | 70_90 | weekly | mixed_by_funnel | full |
| 7 | geometric | 75_90 | daily | spend_to_spend | full |
| 8 | geometric | 75_90 | daily | spend_to_proxy | full |
| 9 | geometric | 75_90 | daily | mixed_by_funnel | full |
| 10 | geometric | 75_90 | weekly | spend_to_spend | full |
| 11 | geometric | 75_90 | weekly | spend_to_proxy | full |
| 12 | geometric | 75_90 | weekly | mixed_by_funnel | full |
| 13 | geometric | 65_80 | daily | spend_to_spend | full |
| 14 | geometric | 65_80 | daily | spend_to_proxy | full |
| 15 | geometric | 65_80 | daily | mixed_by_funnel | full |
| 16 | geometric | 65_80 | weekly | spend_to_spend | full |
| 17 | geometric | 65_80 | weekly | spend_to_proxy | full |
| 18 | geometric | 65_80 | weekly | mixed_by_funnel | full |

**Hyperparameter preset for all 18:** `Meshed recommend` (geometric default set in `ALL_ADSTOCK_VARIANTS`).  
When `--hyperparameter-ranges-config` is passed, per-channel ranges are resolved using this preset and embedded as `custom_hyperparameters` in each queue entry; `hyperparameter_preset` is then set to `"Custom"`.

---

#### C.2 All Adstock, Single Window — 54 Combinations

Applies to: `--full-run --all-adstock` (54 combinations).

**Structure:** repeat the 18-combination block from C.1 three times, one per adstock type.

| Block | Adstock | Combos | Hyperparameter preset |
|-------|---------|--------|----------------------|
| 1–18 | geometric | 18 | Meshed recommend |
| 19–36 | weibull_cdf | 18 | Meta default |
| 37–54 | weibull_pdf | 18 | Meshed recommend |

Full enumeration (adstock varies, other dimensions identical to C.1):

| # | Adstock | Train split | Time agg | Spend-var | Window |
|---|---------|-------------|---------|-----------|--------|
| 1 | geometric | 70_90 | daily | spend_to_spend | full |
| 2 | geometric | 70_90 | daily | spend_to_proxy | full |
| 3 | geometric | 70_90 | daily | mixed_by_funnel | full |
| 4 | geometric | 70_90 | weekly | spend_to_spend | full |
| 5 | geometric | 70_90 | weekly | spend_to_proxy | full |
| 6 | geometric | 70_90 | weekly | mixed_by_funnel | full |
| 7 | geometric | 75_90 | daily | spend_to_spend | full |
| 8 | geometric | 75_90 | daily | spend_to_proxy | full |
| 9 | geometric | 75_90 | daily | mixed_by_funnel | full |
| 10 | geometric | 75_90 | weekly | spend_to_spend | full |
| 11 | geometric | 75_90 | weekly | spend_to_proxy | full |
| 12 | geometric | 75_90 | weekly | mixed_by_funnel | full |
| 13 | geometric | 65_80 | daily | spend_to_spend | full |
| 14 | geometric | 65_80 | daily | spend_to_proxy | full |
| 15 | geometric | 65_80 | daily | mixed_by_funnel | full |
| 16 | geometric | 65_80 | weekly | spend_to_spend | full |
| 17 | geometric | 65_80 | weekly | spend_to_proxy | full |
| 18 | geometric | 65_80 | weekly | mixed_by_funnel | full |
| 19 | weibull_cdf | 70_90 | daily | spend_to_spend | full |
| 20 | weibull_cdf | 70_90 | daily | spend_to_proxy | full |
| 21 | weibull_cdf | 70_90 | daily | mixed_by_funnel | full |
| 22 | weibull_cdf | 70_90 | weekly | spend_to_spend | full |
| 23 | weibull_cdf | 70_90 | weekly | spend_to_proxy | full |
| 24 | weibull_cdf | 70_90 | weekly | mixed_by_funnel | full |
| 25 | weibull_cdf | 75_90 | daily | spend_to_spend | full |
| 26 | weibull_cdf | 75_90 | daily | spend_to_proxy | full |
| 27 | weibull_cdf | 75_90 | daily | mixed_by_funnel | full |
| 28 | weibull_cdf | 75_90 | weekly | spend_to_spend | full |
| 29 | weibull_cdf | 75_90 | weekly | spend_to_proxy | full |
| 30 | weibull_cdf | 75_90 | weekly | mixed_by_funnel | full |
| 31 | weibull_cdf | 65_80 | daily | spend_to_spend | full |
| 32 | weibull_cdf | 65_80 | daily | spend_to_proxy | full |
| 33 | weibull_cdf | 65_80 | daily | mixed_by_funnel | full |
| 34 | weibull_cdf | 65_80 | weekly | spend_to_spend | full |
| 35 | weibull_cdf | 65_80 | weekly | spend_to_proxy | full |
| 36 | weibull_cdf | 65_80 | weekly | mixed_by_funnel | full |
| 37 | weibull_pdf | 70_90 | daily | spend_to_spend | full |
| 38 | weibull_pdf | 70_90 | daily | spend_to_proxy | full |
| 39 | weibull_pdf | 70_90 | daily | mixed_by_funnel | full |
| 40 | weibull_pdf | 70_90 | weekly | spend_to_spend | full |
| 41 | weibull_pdf | 70_90 | weekly | spend_to_proxy | full |
| 42 | weibull_pdf | 70_90 | weekly | mixed_by_funnel | full |
| 43 | weibull_pdf | 75_90 | daily | spend_to_spend | full |
| 44 | weibull_pdf | 75_90 | daily | spend_to_proxy | full |
| 45 | weibull_pdf | 75_90 | daily | mixed_by_funnel | full |
| 46 | weibull_pdf | 75_90 | weekly | spend_to_spend | full |
| 47 | weibull_pdf | 75_90 | weekly | spend_to_proxy | full |
| 48 | weibull_pdf | 75_90 | weekly | mixed_by_funnel | full |
| 49 | weibull_pdf | 65_80 | daily | spend_to_spend | full |
| 50 | weibull_pdf | 65_80 | daily | spend_to_proxy | full |
| 51 | weibull_pdf | 65_80 | daily | mixed_by_funnel | full |
| 52 | weibull_pdf | 65_80 | weekly | spend_to_spend | full |
| 53 | weibull_pdf | 65_80 | weekly | spend_to_proxy | full |
| 54 | weibull_pdf | 65_80 | weekly | mixed_by_funnel | full |

---

#### C.3 Window Sweep — Geometric, All 3 Windows (54 Combinations)

Applies to: `--full-run --all-windows` (54 combinations).

**Structure:** repeat the 18-combination block from C.1 three times, one per window.

| Block | Window | Start date (example, end=2026-01-22) | Combos |
|-------|--------|--------------------------------------|--------|
| 1–18 | full | no override (all available history) | 18 |
| 19–36 | 2y | 2023-10-05 (104 weeks back) | 18 |
| 37–54 | 3y | 2022-09-29 (156 weeks back) | 18 |

Within each block the 18 combinations are identical to C.1 (adstock=geometric, all 3 splits × 2 time-agg × 3 spend-var).

---

#### C.4 All Adstock + All Windows — 162 Combinations

Applies to: `--full-run --all-adstock --all-windows` (162 combinations).

**Structure:**

| Block | Adstock | Window | Combos in block | Cumulative |
|-------|---------|--------|----------------|-----------|
| 1–18 | geometric | full | 18 | 18 |
| 19–36 | geometric | 2y | 18 | 36 |
| 37–54 | geometric | 3y | 18 | 54 |
| 55–72 | weibull_cdf | full | 18 | 72 |
| 73–90 | weibull_cdf | 2y | 18 | 90 |
| 91–108 | weibull_cdf | 3y | 18 | 108 |
| 109–126 | weibull_pdf | full | 18 | 126 |
| 127–144 | weibull_pdf | 2y | 18 | 144 |
| 145–162 | weibull_pdf | 3y | 18 | 162 |

Within each block the 18 combinations follow the pattern in C.1 (3 splits × 2 time-agg × 3 spend-var).

---

#### C.5 Sequential Mode — Geometric, No Window Sweep (9 Combinations)

Applies to: `--full-run --sequential`.

Each dimension is varied in isolation; all other dimensions use the baseline values.

| # | Adstock | Train split | Time agg | Spend-var | Window | Varying dimension |
|---|---------|-------------|---------|-----------|--------|------------------|
| 1 | **geometric** | 70_90 | daily | spend_to_spend | full | adstock (baseline) |
| 2 | geometric | **70_90** | daily | spend_to_spend | full | train split |
| 3 | geometric | **75_90** | daily | spend_to_spend | full | train split |
| 4 | geometric | **65_80** | daily | spend_to_spend | full | train split |
| 5 | geometric | 70_90 | **daily** | spend_to_spend | full | time agg |
| 6 | geometric | 70_90 | **weekly** | spend_to_spend | full | time agg |
| 7 | geometric | 70_90 | daily | **spend_to_spend** | full | spend-var |
| 8 | geometric | 70_90 | daily | **spend_to_proxy** | full | spend-var |
| 9 | geometric | 70_90 | daily | **mixed_by_funnel** | full | spend-var |

> **Note:** combo #1 (adstock baseline = geometric) and combos #2 and #7 use identical config. The runner deduplicates or submits them as labeled variants.

---

#### C.6 Sequential Mode with All Windows (12 Combinations)

Applies to: `--full-run --sequential --all-windows`. Adds the window dimension sweep (3 variants) to C.5.

| # | Adstock | Train split | Time agg | Spend-var | Window | Varying dimension |
|---|---------|-------------|---------|-----------|--------|------------------|
| 1–9 | _(same as C.5)_ | | | | | adstock / split / time / spend-var |
| 10 | geometric | 70_90 | daily | spend_to_spend | **full** | window |
| 11 | geometric | 70_90 | daily | spend_to_spend | **2y** | window |
| 12 | geometric | 70_90 | daily | spend_to_spend | **3y** | window |

---

### D. Hyperparameter Reference

#### D.1 Preset Assignment per Adstock Type

The three adstock variants carry fixed preset defaults in `ALL_ADSTOCK_VARIANTS` (overridable via `--hyperparameter-preset` or a variant-level JSON field):

| Adstock | Default preset |
|---------|---------------|
| geometric | `Meshed recommend` |
| weibull_cdf | `Meta default` |
| weibull_pdf | `Meshed recommend` |

When `--hyperparameter-ranges-config` is also supplied, these presets are used to look up per-channel ranges in `generic_hyperparameter_ranges_v2.json`. The resolved ranges replace the preset in the queue entry (`hyperparameter_preset` → `"Custom"`, `custom_hyperparameters` → per-channel dict).

#### D.2 Built-in Presets in `generic_hyperparameter_ranges_v2.json`

| Preset | Theta (decay) | Alpha (saturation shape) | Gamma (diminishing returns) | Best for |
|--------|--------------|--------------------------|----------------------------|---------|
| `conservative` | Narrow / low | [0.4, 1.7] | [0.16, 0.51] | Fast screening, short-cycle products |
| `balanced` | Moderate | [0.5, 2.0] | [0.2, 0.6] | General purpose (default) |
| `exploratory` | Wide / high | [0.5, 2.4] | [0.2, 0.69] | Complex markets, long-cycle products |

> Ranges shown are approximate; exact values depend on channel type and frequency. See `benchmarks/generic_hyperparameter_ranges_v2.json` for the full lookup table.

#### D.3 Example Hyperparameter Ranges by Channel Type and Adstock (balanced preset, daily frequency)

| Channel type | Adstock | theta | alpha | gamma |
|-------------|---------|-------|-------|-------|
| search_brand | geometric | [0.00, 0.05] | [0.5, 2.0] | [0.20, 0.60] |
| search_nonbrand | geometric | [0.00, 0.08] | [0.5, 2.0] | [0.25, 0.65] |
| paid_social_performance | geometric | [0.05, 0.15] | [0.5, 2.0] | [0.30, 0.70] |
| paid_social_awareness | geometric | [0.10, 0.25] | [0.5, 1.8] | [0.35, 0.75] |
| display_prospecting | geometric | [0.10, 0.30] | [0.4, 1.8] | [0.30, 0.70] |
| video_online | geometric | [0.15, 0.35] | [0.4, 1.8] | [0.35, 0.75] |
| tv_offline | geometric | [0.20, 0.50] | [0.3, 1.5] | [0.40, 0.80] |
| crm_email | geometric | [0.00, 0.05] | [0.5, 2.0] | [0.20, 0.60] |
| search_brand | weibull_cdf | n/a | [0.5, 2.0] | [0.20, 0.60] |
| search_nonbrand | weibull_cdf | n/a | [0.5, 2.0] | [0.25, 0.65] |

> `theta` is not used by weibull adstock types (they use shape/scale parameters instead). Consult `generic_hyperparameter_ranges_v2.json` for the complete ranges per channel, frequency, and adstock.

#### D.4 Iterations and Trials per Run Mode

| Run mode | Flag | Iterations | Trials | Jobs per combo | Cost driver |
|----------|------|-----------|--------|----------------|-------------|
| test | _(none)_ | 10 | 1 | 1 | ~$0.28/combo |
| standard | `--full-run` | 1,000 | 3 | 3 | ~$1.39/combo |
| extended | `--extended-run` | 2,000 | 5 | 5 | ~$3.89/combo |
| production | `--production-run` | 5,000 | 5 | 5 | ~$9.17/combo |
