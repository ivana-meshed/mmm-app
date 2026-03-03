# Quick Start Guide: Complete Benchmark Workflow

This guide provides a complete runthrough of commands for running a full benchmark test from `selected_columns.json` and visualizing results in the Streamlit app.

## Prerequisites

Before starting, ensure you have:

1. **Python environment** with all requirements installed:
   ```bash
   pip install -r requirements.txt
   ```

2. **GCP authentication** configured:
   ```bash
   gcloud auth application-default login
   ```

3. **Access to GCS bucket**: `mmm-app-output`

4. **Your selected_columns.json path** ready, for example:
   ```
   gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json
   ```

---

## Method 1: One-Line Command (Recommended)

The simplest way to run a complete benchmark test from start to finish.

### Command

```bash
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json \
  --full-run
```

### What This Does

1. **Downloads config** from GCS
2. **Generates 54 test variants** (adstock × train_splits × time_agg × spend_var_mapping)
3. **Submits all jobs** to queue
4. **Processes queue** until all complete
5. **Analyzes results** automatically
6. **Generates visualizations** (CSV + 6 plots)

### Options

**Test Mode (default - faster):**
```bash
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json
```
- 10 iterations, 1 trial per variant
- Takes ~1-2 hours
- Good for validation

**Full Production Mode:**
```bash
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json \
  --full-run
```
- 1000 iterations, 3 trials per variant
- Takes ~4-6 hours
- Production-quality results

### Expected Output

```
================================================================================
COMPLETE BENCHMARKING WORKFLOW
================================================================================
Mode: FULL RUN
Config path: gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json
Queue: default-dev
================================================================================

STEP 0: LOADING CONFIGURATION
================================================================================
📥 Downloading config from: gs://...
✅ Downloaded config for country: de, goal: N_UPLOADS_WEB

📊 Generated benchmark config:
   Country: de
   Goal: N_UPLOADS_WEB
   Iterations: 1000
   Trials: 3
   Expected variants: 54 (3 × 3 × 2 × 3)

================================================================================
STEP 1: SUBMITTING BENCHMARK TO QUEUE
================================================================================
🚀 Submitting benchmark: comprehensive_benchmark_20260122_113141_20260303_100000
✅ Submitted 54 job(s) to queue 'default-dev'

================================================================================
STEP 2: PROCESSING QUEUE
================================================================================
⚙️  Processing queue until all jobs complete...
✅ Job completed: geometric_70_90_daily_spend_to_spend
✅ Job completed: geometric_70_90_daily_spend_to_proxy
[... 52 more variants ...]

================================================================================
STEP 3: ANALYZING RESULTS
================================================================================
📊 Analyzing benchmark: comprehensive_benchmark_20260122_113141_20260303_100000
✅ Collected 54 results
✅ Exported CSV to: gs://mmm-app-output/benchmarks/.../results_20260303_104500.csv
✅ Plots saved to: gs://mmm-app-output/benchmarks/.../plots_20260303_104500/

✅ Analysis complete!
Results saved to: ./benchmark_analysis/
```

---

## Method 2: Step-by-Step Commands

For more control, you can run each phase separately.

### Step 1: Submit Benchmark

**Submit all 4 benchmark types:**
```bash
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all
```

**Or submit just one benchmark type:**
```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run-all
```

**Or use comprehensive cartesian benchmark:**
```bash
python scripts/benchmark_mmm.py --config benchmarks/comprehensive_benchmark.json --test-run-all
```

**Expected Output:**
```
✅ Benchmark submitted successfully!
Benchmark ID: comprehensive_benchmark_20260122_113141_20260303_100000
✅ Submitted 54 job(s) to queue 'default-dev'

💡 Process the queue with:
  python scripts/process_queue_simple.py --loop --cleanup
```

### Step 2: Process Queue

```bash
python scripts/process_queue_simple.py --loop --cleanup
```

**What this does:**
- Continuously processes jobs from queue
- Launches Cloud Run Jobs for each variant
- Monitors completion
- Verifies results in GCS
- Cleans up completed entries

**Expected Output:**
```
Processing queue: default-dev
Found 54 entries in queue

Processing entry 1/54: geometric_70_90_daily_spend_to_spend
🚀 Launching job: mmm-app-dev-training
⏳ Waiting for completion...
✅ Job completed successfully
✅ Results verified: Found 11 files at robyn/default/de/20260303_101234/

[... continues for all 54 variants ...]

✅ Queue processing complete!
Total processed: 54
Total successful: 54
```

**Monitoring:**
- Check logs in real-time
- View Cloud Run Jobs in GCP Console
- Check GCS bucket for results appearing

### Step 3: Analyze Results

```bash
python scripts/analyze_benchmark_results.py \
  --benchmark-id comprehensive_benchmark_20260122_113141_20260303_100000 \
  --output-dir ./analysis
```

**Expected Output:**
```
Analyzing benchmark: comprehensive_benchmark_20260122_113141_20260303_100000
Collecting results for benchmark...
Found 54 variants in benchmark plan

✅ Collected 54 results
✅ Exported CSV to: gs://mmm-app-output/benchmarks/.../results_20260303_104500.csv

================================================================================
SUMMARY STATISTICS
================================================================================
Total variants: 54
Best by R² validation: geometric_70_90_daily_spend_to_spend
Best by NRMSE validation: geometric_70_90_daily_spend_to_spend
Best by decomp RSSD: geometric_70_90_daily_spend_to_spend
================================================================================

Generating analysis plots...
✅ Plots saved to: gs://mmm-app-output/benchmarks/.../plots_20260303_104500/

✅ Analysis complete!

View results:
  CSV: gs://mmm-app-output/benchmarks/comprehensive_benchmark_20260122_113141_20260303_100000/
  Plots: gs://mmm-app-output/benchmarks/comprehensive_benchmark_20260122_113141_20260303_100000/plots_*/
  Local: ./analysis/
```

**Files created:**
- `results_<timestamp>.csv` - All metrics for all variants
- `rsq_comparison.png` - R² across variants
- `nrmse_comparison.png` - NRMSE across variants
- `decomp_rssd.png` - Decomposition quality
- `train_val_test_gap.png` - Generalization analysis
- `metric_correlations.png` - Metric relationships
- `best_models_summary.png` - Top performers

---

## Viewing Results in Streamlit App

### Start the App

```bash
streamlit run app/streamlit_app.py
```

**Expected Output:**
```
  You can now view your Streamlit app in your browser.

  Local URL: http://localhost:8501
  Network URL: http://192.168.1.100:8501
```

### Access Benchmark Results Page

**Option 1: Via Sidebar Navigation**
1. Open `http://localhost:8501`
2. Look in the left sidebar
3. Scroll to the bottom
4. Click **"Benchmark Results"** (with 🧪 icon)

**Option 2: Direct URL**
```
http://localhost:8501/View_Benchmark_Results
```

### What You'll See

**Page Header:**
```
🧪 Benchmark Results Visualization
Hidden page - not in navigation
```

**Select Benchmark Section:**
- Dropdown showing all available benchmarks
- Example: `comprehensive_benchmark_20260122_113141_20260303_100000`

**Summary Metrics:**
```
📊 Summary
Total Variants: 54
Average R² (validation): 0.85
Average NRMSE (validation): 0.12
Average Decomp RSSD: 0.15
```

**CSV Data Table:**
- Expandable data frame showing all results
- Columns: benchmark_test, benchmark_variant, country, adstock, train_size, rsq_val, nrmse_val, decomp_rssd, etc.
- Sortable by any column
- Searchable

**Visualization Plots:**
1. **R² Comparison**
   - Bar chart comparing R² (train/val/test) across variants
   - Identifies best performing configs

2. **NRMSE Comparison**
   - Lower is better
   - Shows prediction accuracy

3. **Decomposition RSSD**
   - Measures model stability
   - Lower indicates more stable decomposition

4. **Train/Val/Test Gap Analysis**
   - Scatter plots showing overfitting
   - Good generalization = points near diagonal

5. **Metric Correlations**
   - Heatmap showing relationships
   - Understand metric tradeoffs

6. **Best Models Summary**
   - 4 panels showing top performers by different criteria
   - Helps identify overall best configuration

**Download Button:**
- Download CSV with all results
- For further analysis in Excel/Python/R

---

## Expected Timing

### Test Mode (--test-run or --test-run-all)
- **Per variant**: ~2-5 minutes
- **54 variants**: ~1.5-2 hours total
- **Queue processing**: Most of the time
- **Analysis**: ~5 minutes

### Full Production Mode (--full-run)
- **Per variant**: ~15-30 minutes
- **54 variants**: ~4-6 hours total
- **Queue processing**: Most of the time
- **Analysis**: ~5 minutes

### Breakdown by Phase
- **Submission**: < 1 minute
- **Queue Processing**: 1.5-6 hours (depends on mode)
- **Analysis**: 5 minutes
- **Visualization**: Instant (once app loaded)

---

## Verification Commands

### Check if benchmark submitted successfully
```bash
gsutil ls gs://mmm-app-output/benchmarks/
```
Should show your benchmark folder.

### Check queue status
```bash
gsutil cat gs://mmm-app-output/robyn-queues/default-dev/queue.json | jq .
```

### Check results in GCS
```bash
gsutil ls gs://mmm-app-output/robyn/default/de/
```
Should show timestamp folders for each variant.

### Check specific variant results
```bash
gsutil ls gs://mmm-app-output/robyn/default/de/20260303_101234/
```
Should show: model_summary.json, console.log, plots, etc.

### Download CSV locally
```bash
gsutil cp gs://mmm-app-output/benchmarks/comprehensive_benchmark_*/results_*.csv ./
```

### Download plots locally
```bash
gsutil -m cp -r gs://mmm-app-output/benchmarks/comprehensive_benchmark_*/plots_*/ ./plots/
```

---

## Troubleshooting

### Issue: "Base config not found: .../Latest/selected_columns.json"

**Solution:** The benchmark configs use `version="Latest"` which is automatically resolved to the most recent timestamp. If this fails:

1. Check what versions exist:
   ```bash
   gsutil ls gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/
   ```

2. Use a specific timestamp instead of "Latest" in your benchmark config, or use `run_full_benchmark.py` which handles this automatically.

### Issue: "No benchmarks found" in Streamlit app

**Causes:**
- Benchmark hasn't been analyzed yet
- GCS permissions issue
- App needs restart

**Solutions:**
1. Verify benchmark exists:
   ```bash
   gsutil ls gs://mmm-app-output/benchmarks/
   ```

2. Verify analysis completed:
   ```bash
   gsutil ls gs://mmm-app-output/benchmarks/comprehensive_benchmark_*/results_*.csv
   ```

3. Check GCS permissions:
   ```bash
   gcloud auth application-default login
   ```

4. Restart Streamlit app:
   - Press Ctrl+C
   - Run `streamlit run app/streamlit_app.py` again

### Issue: Missing columns in CSV (e.g., adstock, resample_freq)

**Cause:** These fields are populated during variant generation.

**Solution:** This was fixed in commit e178fad. Make sure you're on the latest version:
```bash
git pull origin copilot/follow-up-on-pr-170
```

### Issue: Queue processing seems stuck

**Check:**
1. Cloud Run Jobs status in GCP Console
2. Job logs in Cloud Logging
3. Queue entries:
   ```bash
   gsutil cat gs://mmm-app-output/robyn-queues/default-dev/queue.json | jq '.[] | {status, benchmark_variant}'
   ```

**Solutions:**
- Wait longer (jobs can take 15-30 minutes each in full mode)
- Check Cloud Run Jobs quota
- Verify service account permissions

### Issue: Analysis produces "All-NaN slice" warnings

**Cause:** Results not found or metrics missing.

**Solution:** Run with debug mode:
```bash
python scripts/analyze_benchmark_results.py --benchmark-id <id> --debug
```

This shows exactly where results are being looked up and what's being found.

---

## Complete Example Walkthrough

Here's a complete example using your actual selected_columns.json:

```bash
# 1. Authenticate with GCP
gcloud auth application-default login

# 2. Run complete benchmark (test mode for faster results)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json

# Wait ~1-2 hours for completion...

# 3. Results are automatically analyzed and saved to:
# - ./benchmark_analysis/results_<timestamp>.csv
# - ./benchmark_analysis/*.png

# 4. Start Streamlit app to visualize
streamlit run app/streamlit_app.py

# 5. Navigate to Benchmark Results page
# - Click "Benchmark Results" in sidebar (bottom)
# - Or go to: http://localhost:8501/View_Benchmark_Results

# 6. Select your benchmark from dropdown
# - Look for: comprehensive_benchmark_20260122_113141_<timestamp>

# 7. Explore results:
# - View summary metrics
# - Browse CSV table
# - Examine all 6 plots
# - Download CSV for further analysis
```

That's it! You now have complete benchmark results with visualizations.

---

## Next Steps

After viewing results:

1. **Identify best configuration** based on your criteria:
   - Highest R² validation?
   - Lowest NRMSE?
   - Best generalization (lowest train/val gap)?
   - Most stable (lowest decomp RSSD)?

2. **Apply learnings** to production models:
   - Use winning adstock type
   - Apply optimal train/test split
   - Choose appropriate time aggregation
   - Select best spend→var mapping strategy

3. **Document findings** for your team

4. **Run production model** with optimized configuration

---

## Summary

**Complete workflow:**
```
1. Authenticate → 2. Run Benchmark → 3. Visualize
   (1 command)        (1 command)       (Open app)
```

**Time:** ~1-6 hours depending on mode

**Output:** 
- 54 tested configurations
- Complete metrics CSV
- 6 visualization plots
- Interactive web UI

**Result:** Data-driven MMM configuration selection

---

## Additional Resources

- **IMPLEMENTATION_GUIDE.md** - Technical details
- **USAGE_GUIDE.md** - Detailed CLI reference
- **ANALYSIS_GUIDE.md** - Result interpretation
- **DEBUGGING_GUIDE.md** - Troubleshooting help
- **ARCHITECTURE.md** - System design

For questions or issues, refer to these guides or check the logs with `--debug` flag.
