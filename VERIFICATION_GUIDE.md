# Verification Guide: How to Validate Benchmark Results

This guide helps you verify that your benchmark runs completed successfully and produced the expected output.

## Quick Status Check (30 seconds)

Run these commands for a quick overview:

```bash
# 1. Check the last few result folders created
gsutil ls gs://mmm-app-output/robyn/default/de/ | tail -20

# 2. Count how many result folders you have
gsutil ls gs://mmm-app-output/robyn/default/de/ | wc -l

# 3. Check files in the most recent result
LATEST=$(gsutil ls gs://mmm-app-output/robyn/default/de/ | tail -1)
gsutil ls $LATEST
```

**Expected:** You should see result folders with timestamps, and each should contain multiple files.

---

## Detailed Verification Steps

### Step 1: Verify All Jobs Completed

Check your queue processor output or logs to confirm all jobs completed:

```bash
# If you saved your process_queue_simple.py output
grep "✅ Job completed" <your_log_file>

# Count completed jobs
grep "✅ Job completed" <your_log_file> | wc -l
```

**Expected Output:**
```
✅ Job completed: geometric
✅ Job completed: weibull_cdf
✅ Job completed: weibull_pdf
```

**What to verify:**
- ✅ Number of completions matches number of variants you submitted
- ✅ Each variant name appears
- ✅ No jobs stuck in "running" status
- ✅ No "failed" messages

---

### Step 2: Check GCS for Results

List all result folders to verify they exist:

```bash
# List all results for your country
gsutil ls gs://mmm-app-output/robyn/default/de/

# Look for your benchmark timestamps
# Format: YYYYMMDD_HHMMSS_mmm/
```

**Expected Output:**
```
gs://mmm-app-output/robyn/default/de/20260212_171100_658/
gs://mmm-app-output/robyn/default/de/20260212_172433_189/
gs://mmm-app-output/robyn/default/de/20260212_172436_638/
gs://mmm-app-output/robyn/default/de/20260212_172440_243/
```

**What to verify:**
- ✅ Folders exist with expected timestamps
- ✅ Number of folders matches number of jobs
- ✅ Timestamps align with when you ran the benchmarks

---

### Step 3: Validate Key Files Exist

Check one of the result folders in detail:

```bash
# Pick a specific result folder (use your timestamp)
gsutil ls gs://mmm-app-output/robyn/default/de/20260212_172440_243/
```

**Expected Files:**
```
gs://.../InputCollect.RDS          ✓ Required
gs://.../OutputCollect.RDS         ✓ Required
gs://.../OutputModels.RDS          ✓ Required
gs://.../allocator_metrics.csv    ✓ Required
gs://.../allocator_metrics.txt    ✓ Required
gs://.../best_model_id.txt        ✓ Required
gs://.../console.log              ✓ Required
gs://.../model_summary.json       ✓ Required
gs://.../status.json              ✓ Required
gs://.../timings.csv              ✓ Optional
gs://.../Robyn_*/                 ✓ Directory
gs://.../allocator_plots_*/       ✓ Directory
gs://.../debug/                   ✓ Directory
gs://.../output_models_data/      ✓ Directory
```

**What to verify:**
- ✅ All required files are present
- ✅ No files are 0 bytes (check with `gsutil ls -l`)
- ✅ Subdirectories exist

**Check file sizes:**
```bash
gsutil ls -lh gs://mmm-app-output/robyn/default/de/20260212_172440_243/
```

**Red flags:**
- ❌ Missing model_summary.json (job failed)
- ❌ console.log is 0 bytes (job crashed)
- ❌ No allocator_metrics files (allocation failed)

---

### Step 4: Inspect model_summary.json

Download and inspect the model summary to verify metrics:

```bash
# View model summary
gsutil cat gs://mmm-app-output/robyn/default/de/20260212_172440_243/model_summary.json | jq .

# Or download it
gsutil cp gs://mmm-app-output/robyn/default/de/20260212_172440_243/model_summary.json /tmp/
cat /tmp/model_summary.json | jq .
```

**Key metrics to check:**

```json
{
  "rsq_train": 0.85,     // Should be between 0 and 1
  "rsq_val": 0.82,       // Should be between 0 and 1
  "rsq_test": 0.80,      // Should be between 0 and 1
  "nrmse_train": 0.15,   // Lower is better (< 0.3 is good)
  "nrmse_val": 0.18,     // Lower is better
  "nrmse_test": 0.20,    // Lower is better
  "decomp_rssd": 0.05,   // Lower is better (< 0.1 is good)
  "mape": 12.5           // Percentage error
}
```

**What to verify:**
- ✅ R² values between 0 and 1 (closer to 1 is better)
- ✅ NRMSE values reasonable (< 0.5)
- ✅ No NaN or null values
- ✅ decomp.rssd reasonable (< 0.2)

**Red flags:**
- ❌ R² > 1 or < 0 (data issue)
- ❌ NRMSE > 1 (poor fit)
- ❌ All metrics are 0 or null (job failed)

---

### Step 5: Check console.log for Errors

Review the console log to ensure no errors occurred:

```bash
# Search for errors
gsutil cat gs://mmm-app-output/robyn/default/de/20260212_172440_243/console.log | grep -i error

# Search for warnings
gsutil cat gs://mmm-app-output/robyn/default/de/20260212_172440_243/console.log | grep -i warning

# Check last few lines for completion message
gsutil cat gs://mmm-app-output/robyn/default/de/20260212_172440_243/console.log | tail -50
```

**Expected (good) patterns:**
```
✓ Model training completed successfully
✓ Best model selected
✓ Plots generated
✓ Results saved to GCS
```

**Red flags:**
```
❌ Error: ...
❌ CRITICAL: ...
❌ Failed to ...
❌ Traceback ...
```

**Common harmless warnings (OK to ignore):**
```
⚠️ Warning: Using default hyperparameters
⚠️ Note: Some channels have low signal
```

---

### Step 6: Collect Results for Analysis

Use the benchmark script to collect all results:

```bash
# Find your benchmark ID from the submission output or plan file
# Format: <test_type>_YYYYMMDD_HHMMSS or <test_type>_YYYYMMDD_HHMMSS_testall

# List available benchmarks
gsutil ls gs://mmm-app-output/benchmarks/

# Collect results
python scripts/benchmark_mmm.py --collect-results adstock_comparison_20260212_151148_testall --export-format csv

# This creates: results.csv
```

**Expected Output:**
```
Loading benchmark plan...
Found 3 variants in benchmark
Collecting results...
✓ geometric: Found results
✓ weibull_cdf: Found results
✓ weibull_pdf: Found results
Exported results to: results.csv
```

**What to verify:**
- ✅ All variants found
- ✅ CSV created successfully
- ✅ CSV has data (not empty)

---

## Success Criteria Checklist

Use this checklist to confirm everything is good:

### Job Completion
- [ ] All jobs show "✅ Job completed" in logs
- [ ] No jobs stuck in "running" status
- [ ] No "failed" jobs
- [ ] Number of completions matches number of submitted variants

### GCS Results
- [ ] Result folders exist for each job
- [ ] Folder timestamps align with job execution times
- [ ] Folders are in correct location (`robyn/default/{country}/{timestamp}/`)

### Required Files
- [ ] model_summary.json exists and has content
- [ ] console.log exists and shows completion
- [ ] allocator_metrics.csv exists
- [ ] best_model_id.txt exists
- [ ] All RDS files present
- [ ] Plot directories exist

### Metrics Quality
- [ ] R² values between 0 and 1
- [ ] NRMSE values reasonable (< 0.5)
- [ ] decomp.rssd reasonable (< 0.2)
- [ ] No NaN or null values in metrics
- [ ] Metrics make business sense

### Logs Clean
- [ ] No critical errors in console.log
- [ ] No Python tracebacks
- [ ] Job completed successfully message present
- [ ] Only harmless warnings (if any)

### Result Collection
- [ ] Can collect results with --collect-results
- [ ] CSV/Parquet export works
- [ ] Exported file has all variants
- [ ] Exported metrics match individual files

---

## Common Issues and Solutions

### Issue 1: Missing Files

**Problem:** Some files missing from results folder

**Diagnosis:**
```bash
# Check what files exist
gsutil ls gs://mmm-app-output/robyn/default/de/TIMESTAMP/

# Check console.log for errors
gsutil cat gs://mmm-app-output/robyn/default/de/TIMESTAMP/console.log | tail -100
```

**Common causes:**
- Job crashed before completion
- Upload to GCS failed
- Disk space issue in container

**Solution:**
- Check console.log for error messages
- Re-run the specific variant
- Check Cloud Run logs for the execution

### Issue 2: Metrics Look Wrong

**Problem:** R² > 1, NRMSE very high, or NaN values

**Diagnosis:**
```bash
# Check model summary
gsutil cat gs://mmm-app-output/robyn/default/de/TIMESTAMP/model_summary.json | jq .
```

**Common causes:**
- Data quality issues
- Incorrect column mapping
- Hyperparameter issues
- Training didn't converge

**Solution:**
- Review data preparation
- Check selected_columns.json
- Review console.log for convergence warnings
- Try different hyperparameters

### Issue 3: Can't Collect Results

**Problem:** --collect-results fails or finds no results

**Diagnosis:**
```bash
# Check if benchmark plan exists
gsutil cat gs://mmm-app-output/benchmarks/BENCHMARK_ID/plan.json

# Check if results exist
gsutil ls gs://mmm-app-output/robyn/default/de/
```

**Common causes:**
- Wrong benchmark ID
- Results not uploaded yet
- Results in unexpected location

**Solution:**
- Verify benchmark ID from submission output
- Wait for jobs to complete
- Manually check GCS paths in plan.json

### Issue 4: Jobs Still Running

**Problem:** Jobs not completing after long time

**Diagnosis:**
```bash
# Check Cloud Run executions
gcloud run jobs executions list --job=mmm-app-dev-training --region=europe-west1

# Check specific execution logs
gcloud run jobs executions logs <execution-id> --region=europe-west1
```

**Common causes:**
- Large dataset (still processing)
- Container crash (restarting)
- Timeout issues

**Solution:**
- Wait longer (full runs can take 30+ minutes)
- Check Cloud Run logs for errors
- Increase timeout if needed
- Consider using --test-run-all for testing

---

## Analyzing Results

Once verification is complete, analyze your results:

### 1. Load Results

```python
import pandas as pd

# Load collected results
df = pd.read_csv('results.csv')

# View summary
print(df.columns)
print(df.head())
```

### 2. Compare Variants

```python
# Group by variant and compare metrics
comparison = df.groupby('benchmark_variant')[
    ['rsq_train', 'rsq_val', 'rsq_test', 'nrmse_val', 'decomp_rssd']
].mean()

print(comparison)

# Sort by validation R²
print(comparison.sort_values('rsq_val', ascending=False))
```

### 3. Identify Best Configuration

```python
# Find best by validation R²
best_rsq = df.loc[df['rsq_val'].idxmax()]
print(f"Best R² variant: {best_rsq['benchmark_variant']}")
print(f"R² val: {best_rsq['rsq_val']:.3f}")

# Find best by NRMSE
best_nrmse = df.loc[df['nrmse_val'].idxmin()]
print(f"Best NRMSE variant: {best_nrmse['benchmark_variant']}")
print(f"NRMSE val: {best_nrmse['nrmse_val']:.3f}")
```

### 4. Visualize Comparison

```python
import matplotlib.pyplot as plt

# Plot R² comparison
df.plot(x='benchmark_variant', y=['rsq_train', 'rsq_val', 'rsq_test'], kind='bar')
plt.title('R² Comparison Across Variants')
plt.ylabel('R²')
plt.xticks(rotation=45)
plt.tight_layout()
plt.savefig('rsq_comparison.png')

# Plot NRMSE comparison
df.plot(x='benchmark_variant', y=['nrmse_train', 'nrmse_val', 'nrmse_test'], kind='bar')
plt.title('NRMSE Comparison Across Variants')
plt.ylabel('NRMSE')
plt.xticks(rotation=45)
plt.tight_layout()
plt.savefig('nrmse_comparison.png')
```

---

## What to Do Next

### If Everything Passed ✅

Congratulations! Your benchmark ran successfully.

**Next steps:**
1. **Analyze patterns** - Which configuration performed best?
2. **Document findings** - Record insights about what worked
3. **Apply learnings** - Use best configuration for production
4. **Share results** - Update team on findings
5. **Archive data** - Save results for future reference

### If Issues Found ⚠️

Some jobs had problems.

**Next steps:**
1. **Identify failures** - Which specific jobs failed?
2. **Review logs** - Check console.log for error messages
3. **Fix and re-run** - Address issues and resubmit failed jobs
4. **Update configuration** - Adjust parameters if needed
5. **Test again** - Verify fixes with --test-run-all

### Running More Benchmarks

Want to test more configurations?

```bash
# Try different benchmark types
python scripts/benchmark_mmm.py --config benchmarks/train_val_test_splits.json --test-run-all
python scripts/benchmark_mmm.py --config benchmarks/time_aggregation.json --test-run-all
python scripts/benchmark_mmm.py --config benchmarks/spend_var_mapping.json --test-run-all

# Or run everything
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all
```

---

## Quick Reference Commands

```bash
# List all result folders
gsutil ls gs://mmm-app-output/robyn/default/de/

# Check specific result
gsutil ls gs://mmm-app-output/robyn/default/de/TIMESTAMP/

# View model metrics
gsutil cat gs://mmm-app-output/robyn/default/de/TIMESTAMP/model_summary.json | jq .

# Check for errors
gsutil cat gs://mmm-app-output/robyn/default/de/TIMESTAMP/console.log | grep -i error

# Collect results
python scripts/benchmark_mmm.py --collect-results BENCHMARK_ID --export-format csv

# Count results
gsutil ls gs://mmm-app-output/robyn/default/de/ | wc -l

# Check file sizes
gsutil ls -lh gs://mmm-app-output/robyn/default/de/TIMESTAMP/

# Download all results locally
gsutil -m cp -r gs://mmm-app-output/robyn/default/de/TIMESTAMP/ ./local_results/
```

---

## Need Help?

If you're still unsure about your results:

1. **Check documentation:**
   - TESTING_GUIDE.md - Full testing workflow
   - BENCHMARKING_GUIDE.md - Benchmark system overview
   - TROUBLESHOOTING_ARGS.md - Common issues

2. **Review logs:**
   - Process queue output
   - Cloud Run execution logs
   - console.log files in results

3. **Verify configuration:**
   - Check benchmark plan.json
   - Review selected_columns.json
   - Validate data_gcs_path

4. **Ask specific questions:**
   - What error message do you see?
   - Which specific job failed?
   - What do the metrics show?

---

**Happy benchmarking!** 🎉
