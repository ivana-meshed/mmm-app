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
2. Generates comprehensive benchmark config (54 variants)
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
# Test run (default)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json

# Standard run (1000 iterations, 3 trials)
python scripts/run_full_benchmark.py \
  --path <path_to_selected_columns.json> \
  --full-run

# Extended run (2000 iterations, 5 trials)
python scripts/run_full_benchmark.py \
  --path <path> \
  --extended-run

# Production run (5000 iterations, 5 trials)
python scripts/run_full_benchmark.py \
  --path <path> \
  --production-run

# Top-N combinations (5, 10, or 54)
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
2. Generates comprehensive benchmark (54 variants):
   - 3 adstock types (geometric, weibull_cdf, weibull_pdf)
   - 3 train splits (70/90, 75/90, 65/80)
   - 2 time aggregations (daily, weekly)
   - 3 spend→var mappings (spend_to_spend, spend_to_proxy, mixed_by_funnel)
3. Submits all 54 jobs to queue
4. Processes queue until empty
5. Analyzes results and saves to `./benchmark_analysis/`

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
