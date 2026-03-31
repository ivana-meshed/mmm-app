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

# With an explicit hyperparameter preset (conservative / balanced / exploratory / fb / meshed)
python scripts/run_full_benchmark.py \
  --path <path_to_selected_columns.json> \
  --full-run \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments.json \
  --hyperparameter-preset exploratory

# Shorthand: --fb (Facebook/Robyn defaults) or --meshed (Meshed recommended)
python scripts/run_full_benchmark.py \
  --path <path_to_selected_columns.json> \
  --full-run \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --meshed
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
| `benchmarks/comprehensive_benchmark_fleet_marketplace.json` | Default 30-variant benchmark (geometric adstock × 3 train splits × 2 time aggregations × 5 spend-var mappings, `full` window). Use `--all-adstock` to extend to 90 variants; `--all-windows` to add the `2y` and `3y` window variants (90 → 270 with both flags). |

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

All figures below are for the fleet marketplace config:

```bash
python scripts/run_full_benchmark.py \
  --path <gs-path-to-selected_columns.json> \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  [run-mode & variant flags below]
```

**Fleet marketplace variant dimensions:**

| Dimension | Values | Default count | With flag |
|-----------|--------|--------------|-----------|
| Adstock | geometric _(+ weibull_cdf / weibull_pdf)_ | 1 | 3 (`--all-adstock`) |
| Train splits | 70_90, 75_90, 65_80 | 3 | — |
| Time aggregation | daily, weekly | 2 | — |
| Spend-var mapping | spend_to_spend, spend_to_impressions, spend_to_clicks, mixed_by_funnel_impressions, mixed_by_funnel_clicks | 5 | — |
| Seasonality window | full _(+ 2y, 3y)_ | 1 | 3 (`--all-windows`) |

**Default (geometric, full window):** 1×3×2×5×**1** = **30 combos**  
**With `--all-adstock`:** 3×3×2×5×1 = **90 combos**  
**With `--all-windows`:** 1×3×2×5×3 = **90 combos**  
**With `--all-adstock --all-windows`:** 3×3×2×5×3 = **270 combos**

#### Cartesian mode (default)

| Run mode | Adstock | Windows | Variants | Approx. time | Approx. cost | Flag(s) to add |
|----------|---------|---------|----------|-------------|-------------|----------------|
| Test (default) | geometric | full | 30 | ~0.5 h | ~$5 | _(none)_ |
| Test (all adstock) | all 3 | full | 90 | ~1.5 h | ~$15 | `--all-adstock` |
| Test (all windows) | geometric | full+2y+3y | 90 | ~1.5 h | ~$15 | `--all-windows` |
| Test (all adstock + windows) | all 3 | full+2y+3y | 270 | ~4.5 h | ~$45 | `--all-adstock --all-windows` |
| Standard | geometric | full | 30 | ~2 h | ~$25 | `--full-run` |
| Standard (all adstock) | all 3 | full | 90 | ~6 h | ~$75 | `--full-run --all-adstock` |
| Standard (all windows) | geometric | full+2y+3y | 90 | ~6 h | ~$75 | `--full-run --all-windows` |
| Standard (all adstock + windows) | all 3 | full+2y+3y | 270 | ~18 h | ~$225 | `--full-run --all-adstock --all-windows` |
| Extended | geometric | full | 30 | ~4-5 h | ~$70 | `--extended-run` |
| Extended (all adstock) | all 3 | full | 90 | ~12-15 h | ~$210 | `--extended-run --all-adstock` |
| Extended (all windows) | geometric | full+2y+3y | 90 | ~12-15 h | ~$210 | `--extended-run --all-windows` |
| Extended (all adstock + windows) | all 3 | full+2y+3y | 270 | ~40 h | ~$630 | `--extended-run --all-adstock --all-windows` |
| Production | geometric | full | 30 | ~10-12 h | ~$165 | `--production-run` |
| Production (all adstock) | all 3 | full | 90 | ~30-35 h | ~$495 | `--production-run --all-adstock` |
| Production (all windows) | geometric | full+2y+3y | 90 | ~30-35 h | ~$495 | `--production-run --all-windows` |
| Production (all adstock + windows) | all 3 | full+2y+3y | 270 | ~90-100 h | ~$1,485 | `--production-run --all-adstock --all-windows` |
| Top-10 extended | geometric | full | 10 | ~1.5 h | ~$23 | `--extended-run --top-n 10` |
| Top-10 production | geometric | full | 10 | ~3-4 h | ~$55 | `--production-run --top-n 10` |

#### Sequential mode (`--sequential`)

In sequential mode each dimension is varied **independently** (all others held at baseline), so combinations add rather than multiply.

Default (full window only): `1 adstock + 3 splits + 2 time-agg + 5 spend-var = 11`.  
With `--all-windows`: `1 + 3 + 2 + 5 + 3 windows = 14`.

| Run mode | Adstock | Windows | Variants | Approx. time | Approx. cost | Flag(s) to add |
|----------|---------|---------|----------|-------------|-------------|----------------|
| Sequential standard | geometric | full | 11 | ~45 min | ~$9 | `--full-run --sequential` |
| Sequential standard (all adstock) | all 3 | full | 13 | ~1 h | ~$11 | `--full-run --sequential --all-adstock` |
| Sequential standard (all windows) | geometric | full+2y+3y | 14 | ~1 h | ~$12 | `--full-run --sequential --all-windows` |
| Sequential extended | geometric | full | 11 | ~1.5 h | ~$26 | `--extended-run --sequential` |
| Sequential production | geometric | full | 11 | ~4 h | ~$61 | `--production-run --sequential` |

---

### Window Length

By default only the `full` window variant (all available history) is used.  Pass `--all-windows` to add the `2y` and `3y` variants and triple the number of combinations.

Window lengths are applied as a trailing lookback from the data's `end_date`:

| Window variant | `weeks_back` | Training window start (example, end = 2026-01-22) |
|---------------|-------------|--------------------------------------------------|
| `full` _(default)_ | — | All available history (no start-date override) |
| `2y` | 104 weeks | 2023-10-05 |
| `3y` | 156 weeks | 2022-09-29 |

**How window dates flow through the system:**

1. Each `seasonality_window` spec in the JSON carries a `weeks_back` value.
2. `benchmark_mmm.py` resolves `weeks_back` → absolute `start_date` / `end_date` relative to the data's `end_date`.
3. Each queue entry carries `start_date` and `end_date`.
4. The R training script (`r/run_all.R`) reads these and passes them directly to `robyn_inputs(window_start=..., window_end=...)`.

---

### Hyperparameter Presets

Presets control the range of hyperparameters (alpha, gamma, theta) passed to Robyn for each channel. Five built-in presets are available:

| Preset | Alpha | Gamma | Theta | Use case |
|--------|-------|-------|-------|----------|
| `conservative` | Narrow ranges, low saturation | Narrow | Low carryover | Stable baselines, short-cycle products |
| `balanced` | Moderate ranges (default) | Moderate | Moderate | General purpose — recommended starting point |
| `exploratory` | Wide ranges, high saturation | Wide | High carryover | Complex markets, long-cycle products |
| `fb` | Robyn/Facebook official docs | Uniform | Low–medium | Robyn documentation defaults, channel-agnostic |
| `meshed` | Meshed recommendation | Channel-type-specific | Higher than fb | Channel-differentiated; tighter saturation, stronger carryover for organic/TV |

**Shorthand CLI flags:**

```bash
# Equivalent to --hyperparameter-preset fb
python scripts/run_full_benchmark.py --path <path> --full-run     --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json     --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json     --fb

# Equivalent to --hyperparameter-preset meshed
python scripts/run_full_benchmark.py --path <path> --full-run     --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json     --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json     --meshed
```

**Preset precedence** (highest to lowest):

1. **Variant-level preset** — set directly on an adstock spec in the JSON config:
   ```json
   {"name": "weibull_cdf", "adstock": "weibull_cdf", "hyperparameter_preset": "Meta default"}
   ```
2. **Benchmark-level preset** — `--hyperparameter-preset` / `--fb` / `--meshed` CLI flag or `"hyperparameter_preset"` key in the benchmark JSON
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
bing_search_brand_cost
bing_search_nonbrand_cost
bing_search_other_cost
```

Optional additional spend columns (add to `selected_columns.json` when available):
```
google_search_nonbrand_cost_2, google_pmax_cost_2, google_other_cost_2
fb_video_cost, fb_static_cost, fb_upper_cost, fb_lower_cost, fb_app_cost
```

> **Note:** FB sub-channels (`fb_video_cost`, `fb_static_cost`, `fb_upper_cost`, `fb_lower_cost`, `fb_app_cost`) and Google split columns (`google_*_cost_2`) do not have dedicated impression/click proxy columns. Use `spend_to_spend` mapping for these channels.

**paid_media_vars** (proxy columns for impression/clicks-based spend-var variants):

*Impressions:*
```
meta_total_impressions
google_search_brand_impressions
google_search_nonbrand_impressions
google_pmax_impressions
google_display_impressions
google_youtube_impressions
bing_search_brand_impressions
bing_search_nonbrand_impressions
bing_search_other_impressions
```

*Clicks:*
```
meta_total_clicks
google_search_brand_clicks
google_search_nonbrand_clicks
google_pmax_clicks
google_display_clicks
google_youtube_clicks
bing_search_brand_clicks
bing_search_nonbrand_clicks
bing_search_other_clicks
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

The config uses `"hyperparameter_preset": "balanced"` by default. To use a different preset per adstock type, set the `hyperparameter_preset` field directly on each adstock variant in the JSON, or pass `--hyperparameter-preset <preset>` (choices: `conservative`, `balanced`, `exploratory`, `fb`, `meshed`) on the CLI to override all variants at once. Shorthand flags `--fb` and `--meshed` are also available.


---

## Appendix: Benchmark Combinations Reference

This appendix enumerates every combination that the fleet marketplace benchmark command submits to the training queue, and lists the hyperparameters applied for each adstock type and preset.

**Reference command:**
```bash
python scripts/run_full_benchmark.py \
  --path <gs-path-to-selected_columns.json> \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  [flags]
```

---

### A. Dimension Reference

| Dimension | Values | Default count | With flag |
|-----------|--------|--------------|-----------|
| Adstock | `geometric` _(default)_ / `weibull_cdf` / `weibull_pdf` | 1 | 3 (`--all-adstock`) |
| Train split | `70_90`, `75_90`, `65_80` | 3 | — |
| Time aggregation | `daily`, `weekly` | 2 | — |
| Spend-var mapping | `spend_to_spend`, `spend_to_impressions`, `spend_to_clicks`, `mixed_by_funnel_impressions`, `mixed_by_funnel_clicks` | 5 | — |
| Seasonality window | `full` _(default)_ / `2y` / `3y` | 1 | 3 (`--all-windows`) |

**Default (geometric, full window):** 1×3×2×5×**1** = **30 combinations**  
**With `--all-adstock`:** 3×3×2×5×1 = **90 combinations**  
**With `--all-windows`:** 1×3×2×5×3 = **90 combinations**  
**With `--all-adstock --all-windows`:** 3×3×2×5×3 = **270 combinations**

**Baseline values** (used for the non-varying dimension in sequential mode):

| Dimension | Baseline |
|-----------|---------|
| Adstock | geometric |
| Train split | 70_90 |
| Time aggregation | daily |
| Spend-var | spend_to_spend |
| Window | full |

---

### B. Combination Count by Flag

| Flags added to reference command | Mode | Adstock(s) | Windows | Combos | Formula |
|----------------------------------|------|-----------|---------|--------|---------|
| _(none — test)_ | cartesian | geometric | full | **30** | 1×3×2×5×1 |
| `--all-adstock` | cartesian | all 3 | full | **90** | 3×3×2×5×1 |
| `--all-windows` | cartesian | geometric | full+2y+3y | **90** | 1×3×2×5×3 |
| `--all-adstock --all-windows` | cartesian | all 3 | full+2y+3y | **270** | 3×3×2×5×3 |
| `--full-run` | cartesian | geometric | full | **30** | 1×3×2×5×1 |
| `--full-run --all-adstock` | cartesian | all 3 | full | **90** | 3×3×2×5×1 |
| `--full-run --all-windows` | cartesian | geometric | full+2y+3y | **90** | 1×3×2×5×3 |
| `--full-run --all-adstock --all-windows` | cartesian | all 3 | full+2y+3y | **270** | 3×3×2×5×3 |
| `--extended-run` | cartesian | geometric | full | **30** | 1×3×2×5×1 |
| `--extended-run --all-adstock` | cartesian | all 3 | full | **90** | 3×3×2×5×1 |
| `--production-run` | cartesian | geometric | full | **30** | 1×3×2×5×1 |
| `--production-run --all-adstock` | cartesian | all 3 | full | **90** | 3×3×2×5×1 |
| `--full-run --sequential` | sequential | geometric | full | **11** | 1+3+2+5 |
| `--full-run --sequential --all-adstock` | sequential | all 3 | full | **13** | 3+3+2+5 |
| `--full-run --sequential --all-windows` | sequential | geometric | full+2y+3y | **14** | 1+3+2+5+3 |
| `--top-n 10 --extended-run` | cartesian | geometric | full | **10** | first 10 of 30 |
| `--top-n 10 --production-run` | cartesian | geometric | full | **10** | first 10 of 30 |

---

### C. Full Combination Matrix — Geometric Only (30 Combinations)

Applies to all cartesian runs with geometric adstock and default window (test default, `--full-run`, `--extended-run`, `--production-run`).  
All 30 combos use `adstock = geometric`, `seasonality_window = full`, and `hyperparameter_preset = Meshed recommend` (or `Custom` when ranges config is provided).

| # | Train split | Time agg | Spend-var |
|---|-------------|---------|-----------|
| 1 | 70_90 | daily | spend_to_spend |
| 2 | 70_90 | daily | spend_to_impressions |
| 3 | 70_90 | daily | spend_to_clicks |
| 4 | 70_90 | daily | mixed_by_funnel_impressions |
| 5 | 70_90 | daily | mixed_by_funnel_clicks |
| 6 | 70_90 | weekly | spend_to_spend |
| 7 | 70_90 | weekly | spend_to_impressions |
| 8 | 70_90 | weekly | spend_to_clicks |
| 9 | 70_90 | weekly | mixed_by_funnel_impressions |
| 10 | 70_90 | weekly | mixed_by_funnel_clicks |
| 11 | 75_90 | daily | spend_to_spend |
| 12 | 75_90 | daily | spend_to_impressions |
| 13 | 75_90 | daily | spend_to_clicks |
| 14 | 75_90 | daily | mixed_by_funnel_impressions |
| 15 | 75_90 | daily | mixed_by_funnel_clicks |
| 16 | 75_90 | weekly | spend_to_spend |
| 17 | 75_90 | weekly | spend_to_impressions |
| 18 | 75_90 | weekly | spend_to_clicks |
| 19 | 75_90 | weekly | mixed_by_funnel_impressions |
| 20 | 75_90 | weekly | mixed_by_funnel_clicks |
| 21 | 65_80 | daily | spend_to_spend |
| 22 | 65_80 | daily | spend_to_impressions |
| 23 | 65_80 | daily | spend_to_clicks |
| 24 | 65_80 | daily | mixed_by_funnel_impressions |
| 25 | 65_80 | daily | mixed_by_funnel_clicks |
| 26 | 65_80 | weekly | spend_to_spend |
| 27 | 65_80 | weekly | spend_to_impressions |
| 28 | 65_80 | weekly | spend_to_clicks |
| 29 | 65_80 | weekly | mixed_by_funnel_impressions |
| 30 | 65_80 | weekly | mixed_by_funnel_clicks |

Add `--all-windows` to expand each of the 30 rows into 3 window variants (full / 2y / 3y) → **90 combinations**.

---

### D. All Adstock — 90 Combinations (`--all-adstock`)

**Structure:** repeat the 30-combination block from section C three times, one per adstock type.

| Block | Adstock | Combos | Hyperparameter preset |
|-------|---------|--------|-----------------------|
| 1–30 | geometric | 30 | Meshed recommend |
| 31–60 | weibull_cdf | 30 | Meta default |
| 61–90 | weibull_pdf | 30 | Meshed recommend |

Within each block the 30 combinations are identical to section C (3 splits × 2 time-agg × 5 spend-var, `full` window). Add `--all-windows` to expand each block to 90 rows → **270 total**.

---

### E. Sequential Mode — Geometric (11 Combinations, `--sequential`)

Each dimension is varied in isolation; all others are held at the baseline. Window is fixed to `full` (the default); add `--all-windows` for 3 additional window variants → 14 total.

| # | Adstock | Train split | Time agg | Spend-var | Window | Varying |
|---|---------|-------------|---------|-----------|--------|---------|
| 1 | geometric | 70_90 | daily | spend_to_spend | full | adstock baseline |
| 2 | geometric | **70_90** | daily | spend_to_spend | full | split |
| 3 | geometric | **75_90** | daily | spend_to_spend | full | split |
| 4 | geometric | **65_80** | daily | spend_to_spend | full | split |
| 5 | geometric | 70_90 | **daily** | spend_to_spend | full | time agg |
| 6 | geometric | 70_90 | **weekly** | spend_to_spend | full | time agg |
| 7 | geometric | 70_90 | daily | **spend_to_spend** | full | spend-var |
| 8 | geometric | 70_90 | daily | **spend_to_impressions** | full | spend-var |
| 9 | geometric | 70_90 | daily | **spend_to_clicks** | full | spend-var |
| 10 | geometric | 70_90 | daily | **mixed_by_funnel_impressions** | full | spend-var |
| 11 | geometric | 70_90 | daily | **mixed_by_funnel_clicks** | full | spend-var |

With `--all-windows`, 3 rows are appended (rows 12–14: window = full, 2y, 3y).

---

### F. Sequential Mode — All Adstock (13 Combinations, `--sequential --all-adstock`)

Adds the adstock dimension to section E; window is still `full` by default.

| # | Adstock | Train split | Time agg | Spend-var | Window | Varying |
|---|---------|-------------|---------|-----------|--------|---------|
| 1 | **geometric** | 70_90 | daily | spend_to_spend | full | adstock |
| 2 | **weibull_cdf** | 70_90 | daily | spend_to_spend | full | adstock |
| 3 | **weibull_pdf** | 70_90 | daily | spend_to_spend | full | adstock |
| 4–13 | geometric | _(same as E rows 2–11)_ | | | | split / time / spend |

---

### G. Train Split Reference

| Split name | `train_size` | Train % | Val % | Test % |
|-----------|-------------|---------|-------|--------|
| `70_90` | [0.70, 0.90] | 70 % | 20 % | 10 % |
| `75_90` | [0.75, 0.90] | 75 % | 15 % | 10 % |
| `65_80` | [0.65, 0.80] | 65 % | 15 % | 20 % |

---

### H. Spend-var Mapping Reference

| Variant | `paid_media_vars` | Best for |
|---------|------------------|---------|
| `spend_to_spend` | = paid_media_spends (cost columns) | Direct cost attribution |
| `spend_to_impressions` | impression columns (same order as spends) | Ad delivery volume signal |
| `spend_to_clicks` | click columns (same order as spends) | Engagement-weighted signal |
| `mixed_by_funnel_impressions` | upper-funnel channels → impressions; search/pmax/bing → spend | Funnel-aware hybrid (impressions) |
| `mixed_by_funnel_clicks` | upper-funnel channels → clicks; search/pmax/bing → spend | Funnel-aware hybrid (clicks) |

**Upper funnel** (awareness): `meta_total`, `google_display`, `google_youtube`  
**Lower funnel** (performance): `google_search_brand`, `google_search_nonbrand`, `google_pmax`, `bing_search`

---

### I. Hyperparameter Reference

#### I.1 Default preset per adstock type

| Adstock | Default `hyperparameter_preset` |
|---------|--------------------------------|
| geometric | `Meshed recommend` |
| weibull_cdf | `Meta default` |
| weibull_pdf | `Meshed recommend` |

These defaults are set in `ALL_ADSTOCK_VARIANTS` in `scripts/run_full_benchmark.py` and can be overridden via `--hyperparameter-preset` or a `hyperparameter_preset` field on each adstock variant in the JSON.

#### I.2 Built-in presets in `generic_hyperparameter_ranges_v2.json`

| Preset | Theta (decay) | Alpha (saturation) | Gamma (diminishing returns) | Best for |
|--------|--------------|--------------------|-----------------------------|---------|
| `conservative` | Narrow, low carryover | [0.4, 1.7] | [0.16, 0.51] | Fast screening, short-cycle products |
| `balanced` | Moderate | [0.5, 2.0] | [0.20, 0.60] | General purpose (default) |
| `exploratory` | Wide, high carryover | [0.5, 2.4] | [0.20, 0.69] | Complex markets, long-cycle products |
| `fb` | Low (uniform) | [0.5, 3.0] | [0.30, 1.00] | Robyn/Facebook official docs; channel-agnostic |
| `meshed` | Channel-type-specific | [0.5–1.0, 2.0–3.0] | [0.30–0.60, 0.70–0.99] | Meshed recommendation; tighter saturation for search, longer carryover for organic/TV |

> Use `--fb` or `--meshed` CLI flags as shorthand. Ranges above are approximate; exact values vary by channel type and frequency.  
> Full lookup: `benchmarks/generic_hyperparameter_ranges_v2.json` → `ranges[frequency][adstock][channel_type][preset]`

#### I.3 Example ranges — daily frequency, `balanced` preset

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

> `theta` is not applicable for weibull adstock types (they use shape/scale parameters). See `generic_hyperparameter_ranges_v2.json` for weibull ranges.

#### I.4 Iterations and trials per run mode

| Run mode | Flag | Iterations | Trials | Approx. cost/combo |
|----------|------|-----------|--------|-------------------|
| test | _(none)_ | 10 | 1 | ~$0.17 |
| standard | `--full-run` | 1,000 | 3 | ~$0.83 |
| extended | `--extended-run` | 2,000 | 5 | ~$2.33 |
| production | `--production-run` | 5,000 | 5 | ~$5.50 |

**Total cost = combos × cost/combo**  
Examples: standard geometric (90 × $0.83 = **~$75**) · extended all-adstock (270 × $2.33 = **~$630**)
