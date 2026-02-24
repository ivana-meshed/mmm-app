# Testing Guide for PR #170 Scripts

**Quick Reference:** Step-by-step instructions to test the benchmarking and queue processing scripts.

## Table of Contents
- [Prerequisites](#prerequisites)
- [Quick Test (5 minutes)](#quick-test-5-minutes)
- [Test All Variants (NEW)](#test-all-variants-new)
- [Full Test Workflow](#full-test-workflow)
- [Expected Outputs](#expected-outputs)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

### 1. Google Cloud Authentication

**Set up Application Default Credentials:**

```bash
# Authenticate with Google Cloud
gcloud auth application-default login

# Set the credentials environment variable (ALL ON ONE LINE!)
export GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json
```

⚠️ **Critical:** Keep the export command on ONE line. A line break will cause "permission denied" errors.

### 2. Verify Authentication

```bash
# Test GCS access
gsutil ls gs://mmm-app-output/

# Test Cloud Run access
gcloud run jobs list --region=europe-west1 --project=datawarehouse-422511
```

**Expected:** List of buckets/jobs without errors.

### 3. Check Python Environment

```bash
# Verify Python version
python --version  # Should be 3.8+

# Check if scripts are executable
ls -la scripts/benchmark_mmm.py scripts/process_queue_simple.py
```

**Expected:** Both files should have execute permissions (`-rwxr-xr-x`).

---

## Quick Test (5 minutes)

This minimal test validates that everything works without consuming many resources.

### Step 1: List Available Benchmarks

```bash
python scripts/benchmark_mmm.py --list-configs
```

**Expected Output:**
```
Available benchmark configurations:

1. adstock_comparison (3 variants)
   Path: benchmarks/adstock_comparison.json
   Description: Compare different adstock transformation types

2. train_val_test_splits (5 variants)
   Path: benchmarks/train_val_test_splits.json
   Description: Test different train/validation/test split ratios

3. time_aggregation (2 variants)
   Path: benchmarks/time_aggregation.json
   Description: Compare daily vs weekly time aggregation

4. spend_var_mapping (3 variants)
   Path: benchmarks/spend_var_mapping.json
   Description: Test different spend-to-variable mapping strategies

5. comprehensive_benchmark (30 variants)
   Path: benchmarks/comprehensive_benchmark.json
   Description: Comprehensive test combining multiple dimensions
```

✅ **Success:** You see a list of benchmarks with variant counts.

### Step 2: Dry Run (Preview Without Submitting)

```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --dry-run
```

**Expected Output:**
```
2026-02-24 11:10:15,123 - INFO - Loaded benchmark: adstock_comparison
2026-02-24 11:10:15,124 - INFO - Description: Compare different adstock transformation types
2026-02-24 11:10:15,456 - INFO - Loaded base config: de/UPLOAD_VALUE
2026-02-24 11:10:15,456 - INFO - Generated 3 test variants

🔍 DRY RUN MODE - No jobs will be submitted

Benchmark: adstock_comparison
Variants to generate: 3

Variant 1: geometric
  - adstock: geometric
  - iterations: 2000
  - trials: 5

Variant 2: weibull_cdf
  - adstock: weibull_cdf
  - iterations: 2000
  - trials: 5

Variant 3: weibull_pdf
  - adstock: weibull_pdf
  - iterations: 2000
  - trials: 5

💡 To actually run this benchmark, remove --dry-run flag
```

✅ **Success:** You see 3 variants listed without job submission.

### Step 3: Test Run (Minimal Resources)

```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run
```

**Expected Output:**
```
2026-02-24 11:12:30,789 - INFO - Loaded benchmark: adstock_comparison
2026-02-24 11:12:30,790 - INFO - Description: Compare different adstock transformation types
2026-02-24 11:12:31,123 - INFO - Loaded base config: de/UPLOAD_VALUE
2026-02-24 11:12:31,123 - INFO - Generated 3 test variants
2026-02-24 11:12:31,234 - INFO - Saved benchmark plan: gs://mmm-app-output/benchmarks/adstock_comparison_20260224_111231/plan.json

🧪 TEST RUN MODE
Generated 3 total variants, but TEST MODE only runs the first one
Iterations: 10 (reduced from 2000)
Trials: 1 (reduced from 5)
Testing variant: geometric

💡 To run all 3 variants, use --config without --test-run

2026-02-24 11:12:31,567 - INFO - Saved queue: gs://mmm-app-output/robyn-queues/default-dev/queue.json
2026-02-24 11:12:31,568 - INFO - Submitted 1 benchmark jobs to queue 'default-dev'

✅ Benchmark submitted successfully!
Benchmark ID: adstock_comparison_20260224_111231_test
Variants queued: 1
Queue: default-dev
Plan: gs://mmm-app-output/benchmarks/adstock_comparison_20260224_111231_test/plan.json

💡 Monitor progress in the Streamlit app (Run Experiment → Queue Monitor)

Or manually trigger queue processing with:
  python scripts/process_queue_simple.py --loop --cleanup
```

✅ **Success:** Job added to queue, benchmark ID displayed.

**Time:** ~10 seconds

### Step 4: Process the Queue

```bash
python scripts/process_queue_simple.py --loop --cleanup
```

**Expected Output:**
```
2026-02-24 11:13:00,123 - INFO - Using impersonated credentials for: mmm-web-service-sa@...
2026-02-24 11:13:00,123 - INFO - ============================================================
2026-02-24 11:13:00,123 - INFO - MMM Queue Processor (Standalone)
2026-02-24 11:13:00,123 - INFO - ============================================================
2026-02-24 11:13:00,123 - INFO - Queue: default-dev
2026-02-24 11:13:00,123 - INFO - Bucket: mmm-app-output
2026-02-24 11:13:00,123 - INFO - Project: datawarehouse-422511
2026-02-24 11:13:00,123 - INFO - Region: europe-west1
2026-02-24 11:13:00,123 - INFO - Training Job: mmm-app-dev-training
2026-02-24 11:13:00,123 - INFO - Mode: loop until empty
2026-02-24 11:13:00,123 - INFO - Cleanup: Yes (keep 10 recent completed jobs)
2026-02-24 11:13:00,123 - INFO - ============================================================
2026-02-24 11:13:00,123 - INFO - 🧹 Performing cleanup...
2026-02-24 11:13:01,234 - INFO - Loaded queue 'default-dev' from GCS
2026-02-24 11:13:01,235 - INFO - No cleanup needed: 8 completed jobs (keep_count=10)

2026-02-24 11:13:01,567 - INFO - Loaded queue 'default-dev' from GCS
2026-02-24 11:13:01,567 - INFO - 📊 Queue Status: default-dev
2026-02-24 11:13:01,567 - INFO -   Total: 9
2026-02-24 11:13:01,567 - INFO -   Pending: 1
2026-02-24 11:13:01,567 - INFO -   Running: 0
2026-02-24 11:13:01,567 - INFO -   Completed: 8
2026-02-24 11:13:01,567 - INFO -   Failed: 0

2026-02-24 11:13:01,890 - INFO - Processing job 9/9
2026-02-24 11:13:01,890 - INFO -   Country: de
2026-02-24 11:13:01,890 - INFO -   Revision: default
2026-02-24 11:13:01,890 - INFO -   Benchmark variant: geometric
2026-02-24 11:13:01,890 - INFO -   Benchmark test: adstock
2026-02-24 11:13:01,890 - INFO -   Job ID: N/A
2026-02-24 11:13:01,890 - INFO - 📂 Results will be saved to:
2026-02-24 11:13:01,890 - INFO -    gs://mmm-app-output/robyn/default/de/20260224_111301_890/
2026-02-24 11:13:01,890 - INFO -    Key files: model_summary.json, best_model_plots.png, console.log

2026-02-24 11:13:02,456 - INFO - 📋 Job configuration:
2026-02-24 11:13:02,456 - INFO -    country: de
2026-02-24 11:13:02,456 - INFO -    revision: default
2026-02-24 11:13:02,456 - INFO -    timestamp: 20260224_111301_890
2026-02-24 11:13:02,456 - INFO -    data_gcs_path: gs://mmm-app-output/mapped-datasets/de/20251211_115528/raw.parquet
2026-02-24 11:13:02,456 - INFO -    benchmark_variant: geometric
2026-02-24 11:13:02,456 - INFO -    Uploaded job config to: gs://mmm-app-output/training-configs/20260224_111301_890/job_config.json

2026-02-24 11:13:02,789 - INFO - ✅ Launched job: mmm-app-dev-training
2026-02-24 11:13:02,789 - INFO -    Execution: projects/datawarehouse-422511/locations/europe-west1/jobs/mmm-app-dev-training/executions/mmm-app-dev-training-abc123

2026-02-24 11:13:03,123 - INFO - ✅ Job launched successfully
2026-02-24 11:13:03,123 - INFO -    Execution ID: projects/.../mmm-app-dev-training-abc123
2026-02-24 11:13:03,123 - INFO -
2026-02-24 11:13:03,123 - INFO - 💡 To check results when job completes:
2026-02-24 11:13:03,123 - INFO -    gsutil ls gs://mmm-app-output/robyn/default/de/20260224_111301_890/
2026-02-24 11:13:03,123 - INFO -    gsutil cat gs://mmm-app-output/robyn/default/de/20260224_111301_890/model_summary.json

[Queue processor continues polling for job completion...]
```

✅ **Success:** Job launched on Cloud Run, result path logged.

**Time:** Job launching ~5 seconds, then waits for completion (2-5 minutes for test-run).

### Step 5: Verify Results (After Job Completes)

**Wait for completion message:**
```
2026-02-24 11:18:45,123 - INFO - ✅ Job completed: geometric
2026-02-24 11:18:45,123 - INFO -    Results at: gs://mmm-app-output/robyn/default/de/20260224_111301_890/
2026-02-24 11:18:45,123 - INFO -    Verifying results in GCS...
2026-02-24 11:18:47,456 - INFO -    ✓ Results verified: Found 12 files
2026-02-24 11:18:47,456 - INFO -    ✓ Key files found: model_summary.json, best_model_plots.png, console.log
```

**Manually check results:**
```bash
# List files in result directory
gsutil ls gs://mmm-app-output/robyn/default/de/20260224_111301_890/

# View model summary
gsutil cat gs://mmm-app-output/robyn/default/de/20260224_111301_890/model_summary.json | jq .
```

✅ **Success:** You see model files in GCS at the logged path.

**Total Time:** ~5-10 minutes for test-run mode.

---

## Test All Variants (NEW)

This option tests **ALL** variants with reduced resources to validate queue processing with multiple jobs.

### When to Use

- Test that queue processor handles multiple jobs correctly
- Verify all variants in a benchmark can be generated
- Validate end-to-end workflow without waiting hours

### Step 1: Submit All Variants with Test Mode

```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run-all
```

**Expected Output:**
```
2026-02-24 12:45:00,456 - INFO - Generated 3 test variants

🧪 TEST RUN ALL MODE
Generated 3 variants - ALL will run with reduced resources
Iterations: 10 (reduced from 2000)
Trials: 1 (reduced from 5)

Variants to test:
  1. geometric
  2. weibull_cdf
  3. weibull_pdf

💡 This tests queue processing with multiple jobs
💡 Expected time: ~15-30 minutes
💡 To test just one variant, use --test-run instead

✅ Benchmark submitted successfully!
Benchmark ID: adstock_comparison_20260224_124500_testall
Variants queued: 3
Queue: default-dev
```

✅ **Success:** All 3 variants queued with reduced iterations/trials.

**Time:** ~10 seconds

### Step 2: Process All Jobs

```bash
python scripts/process_queue_simple.py --loop --cleanup
```

**What Happens:**
1. Launches job 1 (geometric) → runs ~5 min → completes ✓
2. Launches job 2 (weibull_cdf) → runs ~5 min → completes ✓
3. Launches job 3 (weibull_pdf) → runs ~5 min → completes ✓

**Expected Output for Each Job:**
```
2026-02-24 12:46:00,123 - INFO - Processing job X/3
2026-02-24 12:46:00,123 - INFO -   Benchmark variant: geometric
2026-02-24 12:46:00,456 - INFO - ✅ Launched job: mmm-app-dev-training
2026-02-24 12:46:00,456 - INFO -    Results will be saved to: gs://mmm-app-output/robyn/default/de/TIMESTAMP/

[Job runs for ~5 minutes]

2026-02-24 12:51:00,789 - INFO - ✅ Job completed: geometric
2026-02-24 12:51:00,789 - INFO -    Results at: gs://mmm-app-output/robyn/default/de/TIMESTAMP/
2026-02-24 12:51:00,789 - INFO -    Verifying results in GCS...
2026-02-24 12:51:02,123 - INFO -    ✓ Results verified: Found 12 files
```

✅ **Success:** All jobs complete, results verified.

**Total Time:** ~15-30 minutes for 3 variants.

### Step 3: Verify All Results

```bash
# List all result directories
gsutil ls gs://mmm-app-output/robyn/default/de/

# Should see 3 new directories with timestamps
```

**Expected:** 3 result directories, each containing model files.

### Timing for Different Benchmarks

| Benchmark | Variants | Test-Run-All Time |
|-----------|----------|-------------------|
| adstock_comparison | 3 | ~15-30 min |
| time_aggregation | 2 | ~10-20 min |
| spend_var_mapping | 3 | ~15-30 min |
| train_val_test_splits | 5 | ~25-50 min |

### Comparison: Test Modes

| Mode | Command | Variants | Time | Purpose |
|------|---------|----------|------|---------|
| **test-run** | `--test-run` | First only | ~5 min | Quick validation |
| **test-run-all** | `--test-run-all` | All | ~15-30 min | Queue validation |
| **Full** | (no flag) | All | ~1-2 hours | Production |

---

## Full Test Workflow

This section shows a complete benchmark run with all variants.

### Test Scenario: Compare Adstock Types

**Goal:** Determine which adstock transformation (geometric, Weibull CDF, Weibull PDF) performs best.

### Step 1: Review Configuration

```bash
cat benchmarks/adstock_comparison.json
```

**You should see:**
- 3 variants (geometric, weibull_cdf, weibull_pdf)
- 2000 iterations each
- 5 trials each

### Step 2: Submit All Variants

```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json
```

**Expected:**
```
✅ Benchmark submitted successfully!
Benchmark ID: adstock_comparison_20260224_120000
Variants queued: 3
Queue: default-dev
Plan: gs://mmm-app-output/benchmarks/adstock_comparison_20260224_120000/plan.json
```

**Time:** ~10 seconds

### Step 3: Process Queue

```bash
python scripts/process_queue_simple.py --loop --cleanup
```

**What Happens:**
1. Processor launches first job on Cloud Run
2. Waits for completion (15-30 minutes per job)
3. Verifies results exist in GCS
4. Launches next job
5. Repeats until all jobs complete

**Expected Console Output:**
- Job 1/3 launches → waits → completes
- Job 2/3 launches → waits → completes
- Job 3/3 launches → waits → completes
- "✅ No more pending jobs"

**Total Time:** ~45-90 minutes for 3 full jobs.

### Step 4: Check Results Location

Each job creates results in a unique directory:

```bash
# List all result directories
gsutil ls gs://mmm-app-output/robyn/default/de/

# Check specific job results
gsutil ls gs://mmm-app-output/robyn/default/de/20260224_120100_123/
```

**You should see:**
```
gs://mmm-app-output/robyn/default/de/20260224_120100_123/best_model_plots.png
gs://mmm-app-output/robyn/default/de/20260224_120100_123/console.log
gs://mmm-app-output/robyn/default/de/20260224_120100_123/model_summary.json
gs://mmm-app-output/robyn/default/de/20260224_120100_123/pareto_front.csv
...
```

### Step 5: Collect Results

```bash
python scripts/benchmark_mmm.py \
  --collect-results adstock_comparison_20260224_120000 \
  --export-format csv
```

**Expected:**
```
2026-02-24 14:30:00,123 - INFO - Collecting results for benchmark: adstock_comparison_20260224_120000
2026-02-24 14:30:01,456 - INFO - Found 3 completed jobs
2026-02-24 14:30:02,789 - INFO - Downloading model_summary.json from job 1/3...
2026-02-24 14:30:03,123 - INFO - Downloading model_summary.json from job 2/3...
2026-02-24 14:30:03,456 - INFO - Downloading model_summary.json from job 3/3...
2026-02-24 14:30:04,789 - INFO - Extracted metrics from 3 jobs
2026-02-24 14:30:04,790 - INFO - ✅ Results exported to: adstock_comparison_20260224_120000_results.csv

Results Summary:
  Total variants: 3
  Metrics collected: rsq_train, rsq_val, rsq_test, nrmse_val, decomp_rssd
  
💡 Analyze results with:
  python -c "import pandas as pd; df = pd.read_csv('adstock_comparison_20260224_120000_results.csv'); print(df[['benchmark_variant', 'rsq_val', 'nrmse_val']].sort_values('rsq_val', ascending=False))"
```

**Time:** ~30 seconds to collect and export.

### Step 6: Analyze Results

```bash
# View CSV in terminal
cat adstock_comparison_20260224_120000_results.csv

# Or load in Python
python -c "
import pandas as pd
df = pd.read_csv('adstock_comparison_20260224_120000_results.csv')
print(df[['benchmark_variant', 'rsq_train', 'rsq_val', 'nrmse_val']].to_string())
"
```

**Expected Output:**
```
  benchmark_variant  rsq_train  rsq_val  nrmse_val
0          geometric      0.892    0.856      0.234
1       weibull_cdf      0.901    0.867      0.221
2       weibull_pdf      0.887    0.849      0.245
```

✅ **Success:** You can compare metrics across adstock types.

---

## Expected Outputs

### What Gets Created

**1. Benchmark Plan (in GCS)**
```
gs://mmm-app-output/benchmarks/adstock_comparison_20260224_120000/plan.json
```
Contains the benchmark configuration and all variant details.

**2. Job Configs (in GCS)**
```
gs://mmm-app-output/training-configs/20260224_120100_123/job_config.json
gs://mmm-app-output/training-configs/20260224_120200_456/job_config.json
gs://mmm-app-output/training-configs/20260224_120300_789/job_config.json
```
One config per job, contains all parameters R script needs.

**3. Results (in GCS)**
```
gs://mmm-app-output/robyn/default/de/20260224_120100_123/
  ├── model_summary.json
  ├── best_model_plots.png
  ├── console.log
  ├── pareto_front.csv
  └── ... (other Robyn output files)
```

**4. Results CSV (local)**
```
adstock_comparison_20260224_120000_results.csv
```
Aggregated metrics from all variants.

### Expected Timing

| Operation | Test Mode | Full Mode |
|-----------|-----------|-----------|
| Submit benchmark | ~10 sec | ~10 sec |
| Launch job | ~5 sec | ~5 sec |
| Job execution | ~2-5 min | ~15-30 min |
| Result verification | ~10 sec | ~10 sec |
| Collect results | ~30 sec | ~30 sec |
| **Total (3 variants)** | ~10-20 min | ~1-2 hours |

### Key Log Indicators

**✅ Success Indicators:**
- `✅ Benchmark submitted successfully!`
- `✅ Launched job: mmm-app-dev-training`
- `✓ Results verified: Found X files`
- `✓ Key files found: model_summary.json, best_model_plots.png, console.log`

**⚠️ Warning Indicators:**
- `⚠️  No files found after 10s timeout`
- `⚠️  Results may still be uploading or job may have failed`

**❌ Error Indicators:**
- `❌ Failed to launch job`
- `❌ Error loading benchmark config`
- `CommandException: One or more URLs matched no objects` (when checking GCS)

---

## Troubleshooting

### Issue 1: "ModuleNotFoundError: No module named 'google'"

**Cause:** Python dependencies not installed.

**Fix:**
```bash
pip install -r requirements.txt
```

### Issue 2: "Permission denied" when setting GOOGLE_APPLICATION_CREDENTIALS

**Cause:** Line break in export command.

**Fix:** Ensure command is on one line:
```bash
# Wrong (causes error)
export GOOGLE_APPLICATION_CREDENTIALS=
/path/to/file.json

# Correct (works)
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/file.json
```

### Issue 3: "No files found after 10s timeout"

**Cause:** Job may still be running or failed.

**Check job status:**
```bash
# List recent job executions
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=5

# Check specific execution logs
gcloud run jobs executions logs read EXECUTION_NAME \
  --region=europe-west1 \
  --limit=100
```

**Expected:** Job should show "Succeeded" status after 15-30 minutes.

### Issue 4: Results at wrong GCS path

**Cause:** This was the bug PR #170 fixed!

**Verify fix is working:**
1. Check Python logs for result path
2. Verify R script uses same timestamp
3. Check that path exists in GCS

**The fix ensures both Python and R use the same timestamp.**

### Issue 5: Queue processor stuck

**Symptom:** No progress, keeps checking status.

**Check:**
```bash
# View queue status
gsutil cat gs://mmm-app-output/robyn-queues/default-dev/queue.json | jq .

# Check Cloud Run jobs
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=5
```

**Fix:** If job is genuinely stuck, you may need to cancel and restart:
```bash
# Stop the queue processor (Ctrl+C)
# Update queue status manually if needed
```

### Issue 6: Benchmark config not found

**Error:** `FileNotFoundError: benchmarks/xyz.json`

**Check available configs:**
```bash
ls benchmarks/*.json
```

**Use correct path:**
```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json
```

### Issue 7: Job completes but no model_summary.json

**Cause:** Job may have failed during R execution.

**Check R logs:**
```bash
# Get execution name from queue processor output
# Then check logs
gcloud run jobs executions logs read EXECUTION_NAME \
  --region=europe-west1 \
  --limit=200
```

**Look for R errors** in the logs.

---

## Success Criteria

You know everything is working when:

✅ **Phase 1: Submission**
- Benchmark configs load without errors
- Variants are generated (count matches config)
- Jobs added to queue successfully
- Benchmark plan saved to GCS

✅ **Phase 2: Execution**
- Queue processor launches jobs on Cloud Run
- Job configs uploaded to GCS
- Execution IDs returned successfully
- Result paths logged clearly

✅ **Phase 3: Completion**
- Jobs complete within expected time
- Result verification finds files in GCS
- Key files exist: model_summary.json, console.log, best_model_plots.png
- Results at paths shown in logs

✅ **Phase 4: Collection**
- Results collected from all jobs
- Metrics extracted successfully
- CSV exported with all variants
- Data ready for analysis

---

## Next Steps

After confirming tests work:

1. **Run Production Benchmarks**
   ```bash
   python scripts/benchmark_mmm.py --config benchmarks/spend_var_mapping.json
   python scripts/benchmark_mmm.py --config benchmarks/train_val_test_splits.json
   ```

2. **Analyze Results**
   - Compare metrics across variants
   - Identify best configurations
   - Document findings

3. **Apply Learnings**
   - Update default configurations
   - Create variant-specific presets
   - Share insights with team

---

## Additional Resources

- **BENCHMARKING_GUIDE.md** - Complete benchmarking documentation
- **benchmarks/README.md** - Benchmark configuration reference
- **benchmarks/WORKFLOW_EXAMPLE.md** - Detailed workflow examples
- **JOB_CONFIG_FIX.md** - Technical details of result path fix
- **DATA_FLOW_VERIFICATION.md** - System architecture and data flow

---

## Quick Reference Commands

```bash
# List benchmarks
python scripts/benchmark_mmm.py --list-configs

# Preview (no submission)
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --dry-run

# Quick test (minimal resources)
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run

# Full run
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json

# Process queue
python scripts/process_queue_simple.py --loop --cleanup

# Check results
gsutil ls gs://mmm-app-output/robyn/default/de/

# Collect results
python scripts/benchmark_mmm.py --collect-results BENCHMARK_ID --export-format csv
```

**For help:**
```bash
python scripts/benchmark_mmm.py --help
python scripts/process_queue_simple.py --help
```
