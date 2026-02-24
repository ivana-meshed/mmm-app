# Cartesian Benchmark & Analysis Implementation

## Overview

Implemented comprehensive cartesian product benchmarking and automated result analysis with visualization.

## 1. Cartesian Product Benchmark

### comprehensive_benchmark.json

**Configuration:**
- **Dimensions:** 3 (adstock × train_splits × time_aggregation)
- **Total Combinations:** 18 variants
- **Mode:** cartesian (full cross-product)

**Dimensions:**

**Adstock (3 types):**
- geometric - Meshed recommend preset
- weibull_cdf - Meta default preset
- weibull_pdf - Meshed recommend preset

**Train Splits (3 configurations):**
- 70/90 - 70% train, 20% val, 10% test
- 75/90 - 75% train, 15% val, 10% test
- 65/80 - 65% train, 15% val, 20% test

**Time Aggregation (2 levels):**
- daily - No resampling (resample_freq: none)
- weekly - Weekly aggregation (resample_freq: W)

**Example Variants Generated:**
```
geometric_70_90_daily
geometric_70_90_weekly
geometric_75_90_daily
geometric_75_90_weekly
geometric_65_80_daily
geometric_65_80_weekly
weibull_cdf_70_90_daily
weibull_cdf_70_90_weekly
... (18 total)
```

### Usage

```bash
# Run comprehensive benchmark with test mode
python scripts/benchmark_mmm.py --config benchmarks/comprehensive_benchmark.json --test-run-all

# Run full comprehensive benchmark
python scripts/benchmark_mmm.py --config benchmarks/comprehensive_benchmark.json

# Process queue
python scripts/process_queue_simple.py --loop --cleanup
```

### Expected Runtime

**Test mode (--test-run-all):**
- Iterations: 10 per variant
- Trials: 1 per variant
- Time per job: ~5-10 minutes
- Total time: ~90-180 minutes (18 jobs)

**Full mode:**
- Iterations: 1000 per variant
- Trials: 3 per variant
- Time per job: ~30-40 minutes
- Total time: ~9-12 hours (18 jobs)

## 2. Analysis Script

### analyze_benchmark_results.py

**Comprehensive analysis and visualization script.**

### Features

**1. Data Collection**
- Collects all results from benchmark run
- Matches variants to GCS results
- Extracts metrics from model_summary.json
- Handles missing/incomplete data

**2. CSV Export**
- All variants with complete metrics
- Saved to GCS: `benchmarks/{id}/results_{timestamp}.csv`
- Optional local copy: `{output_dir}/results_{timestamp}.csv`
- Includes:
  - Benchmark metadata (variant, test type)
  - Configuration (adstock, train_size, iterations, trials)
  - Model fit metrics (rsq_train, rsq_val, rsq_test, nrmse_*)
  - Decomposition metrics (decomp_rssd, mape)
  - Model metadata (model_id, timestamp)

**3. Analysis Plots** (6 visualizations)

**a) R² Comparison**
- Grouped bar chart
- Shows train/val/test R² for each variant
- Helps identify best overall fit
- Y-axis: 0-1 scale

**b) NRMSE Comparison**
- Grouped bar chart
- Shows train/val/test NRMSE for each variant
- Lower is better
- Identifies prediction accuracy

**c) Decomposition RSSD**
- Horizontal bar chart
- Sorted by RSSD value
- Shows decomposition stability
- Lower indicates better consistency

**d) Train/Val/Test Gap Analysis**
- Two scatter plots (R² gaps, NRMSE gaps)
- X-axis: train-val gap
- Y-axis: val-test gap
- Identifies overfitting patterns
- Points near origin = good generalization

**e) Metric Correlations**
- Heatmap of all metrics
- Shows relationships and tradeoffs
- Color scale: -1 (negative) to +1 (positive)
- Helps understand metric dependencies

**f) Best Models Summary**
- 4-panel comparison
- Top 10 models by:
  - R² validation (highest)
  - NRMSE validation (lowest)
  - Decomposition RSSD (lowest)
  - Generalization (smallest val-test gap)
- Identifies best performers by different criteria

**4. Summary Statistics**
- Total variants analyzed
- Mean/std/min/max/median for each metric
- Best variant by:
  - R² validation
  - NRMSE validation
  - Decomposition RSSD
- Printed to console

### Usage

**Basic usage:**
```bash
python scripts/analyze_benchmark_results.py --benchmark-id comprehensive_benchmark_20260224_120000
```

**Save locally:**
```bash
python scripts/analyze_benchmark_results.py \
  --benchmark-id comprehensive_benchmark_20260224_120000 \
  --output-dir ./analysis
```

**PDF format:**
```bash
python scripts/analyze_benchmark_results.py \
  --benchmark-id comprehensive_benchmark_20260224_120000 \
  --format pdf
```

**CSV only (no plots):**
```bash
python scripts/analyze_benchmark_results.py \
  --benchmark-id comprehensive_benchmark_20260224_120000 \
  --no-plots
```

### Output Structure

**GCS:**
```
gs://mmm-app-output/benchmarks/{benchmark_id}/
├── plan.json
├── results_{timestamp}.csv
└── plots_{timestamp}/
    ├── rsq_comparison.png
    ├── nrmse_comparison.png
    ├── decomp_rssd.png
    ├── train_val_test_gap.png
    ├── metric_correlations.png
    └── best_models_summary.png
```

**Local (if --output-dir specified):**
```
{output_dir}/
├── results_{timestamp}.csv
├── rsq_comparison.png
├── nrmse_comparison.png
├── decomp_rssd.png
├── train_val_test_gap.png
├── metric_correlations.png
└── best_models_summary.png
```

### Dependencies

**Required:**
- pandas (already in requirements.txt)
- google-cloud-storage (already in requirements.txt)

**For plotting:**
- matplotlib
- seaborn
- numpy

**Install plotting dependencies:**
```bash
pip install matplotlib seaborn numpy
```

## 3. Complete Workflow

### Step 1: Run Comprehensive Benchmark

```bash
# Test mode (1-2 hours)
python scripts/benchmark_mmm.py \
  --config benchmarks/comprehensive_benchmark.json \
  --test-run-all
```

### Step 2: Process Queue

```bash
# Process all jobs
python scripts/process_queue_simple.py --loop --cleanup

# Monitor progress
# Expected: 18 jobs complete
```

### Step 3: Verify Results

```bash
# Check GCS for results
gsutil ls gs://mmm-app-output/robyn/default/de/ | tail -20

# Should see 18 result folders with timestamps
```

### Step 4: Analyze Results

```bash
# Run analysis
python scripts/analyze_benchmark_results.py \
  --benchmark-id comprehensive_benchmark_20260224_120000 \
  --output-dir ./analysis

# Output:
# - CSV with all metrics
# - 6 visualization plots
# - Summary statistics
```

### Step 5: Review Findings

```bash
# View CSV
cat ./analysis/results_*.csv

# View plots
open ./analysis/*.png

# Or access from GCS
gsutil ls gs://mmm-app-output/benchmarks/{benchmark_id}/plots_*/
```

## 4. Interpreting Results

### Key Questions to Answer

**1. Which adstock type performs best?**
- Compare R² validation across geometric, weibull_cdf, weibull_pdf
- Look at decomp_rssd for stability
- Check metric_correlations.png

**2. Which train/val/test split generalizes best?**
- Check train_val_test_gap.png
- Look for smallest gaps
- Compare val vs test R²

**3. Daily or weekly aggregation?**
- Compare NRMSE between daily and weekly
- Check decomp_rssd for stability
- Consider business requirements

**4. Best overall configuration?**
- Review best_models_summary.png
- Check if same variant appears in multiple panels
- Balance fit quality vs generalization vs stability

### Example Analysis

**CSV excerpt:**
```csv
benchmark_variant,rsq_val,nrmse_val,decomp_rssd
geometric_75_90_weekly,0.85,0.12,0.08
weibull_cdf_70_90_daily,0.83,0.14,0.10
geometric_70_90_weekly,0.84,0.13,0.09
```

**Interpretation:**
- `geometric_75_90_weekly` has highest R² validation (0.85)
- Also has lowest NRMSE (0.12) and decomp_rssd (0.08)
- This configuration is likely the best overall choice

**Decision:**
Use geometric adstock with 75/90 train split and weekly aggregation for production.

## 5. Benefits

### Systematic Evaluation
- Tests all meaningful combinations
- No guesswork on configuration
- Reproducible results

### Visual Analysis
- 6 different perspectives on performance
- Easy to identify patterns
- Shareable with stakeholders

### Data-Driven Decisions
- Metrics exported to CSV
- Can perform additional analysis
- Supports A/B testing of configurations

### Time Savings
- Automated collection and plotting
- No manual result gathering
- Consistent analysis framework

## 6. Next Steps

After analysis:

1. **Select Best Configuration**
   - Based on analysis findings
   - Balance multiple criteria
   - Consider business constraints

2. **Validate in Production**
   - Run selected config on full data
   - Monitor real-world performance
   - Compare to benchmark predictions

3. **Document Learnings**
   - Which configurations work best
   - Any surprising patterns
   - Recommendations for future

4. **Share Results**
   - Export plots for presentations
   - Share CSV for deeper analysis
   - Update best practices

## 7. Troubleshooting

### No results found

**Check:**
```bash
# Verify jobs completed
python scripts/benchmark_mmm.py --list-results {benchmark_id}

# Check GCS manually
gsutil ls gs://mmm-app-output/robyn/default/de/
```

### Plotting fails

**Solution:**
```bash
# Install dependencies
pip install matplotlib seaborn numpy

# Or skip plots
python scripts/analyze_benchmark_results.py --benchmark-id {id} --no-plots
```

### Missing variants in results

**Possible causes:**
- Jobs still running
- Some jobs failed
- Wrong benchmark_id

**Check:**
```bash
# Load benchmark plan
gsutil cat gs://mmm-app-output/benchmarks/{benchmark_id}/plan.json
```

## Summary

✅ **Cartesian product benchmark** - Tests 18 meaningful combinations
✅ **Analysis script** - Collects results, exports CSV, generates 6 plots
✅ **Complete workflow** - From submission to visualization
✅ **Production ready** - Documented, tested, error handling

Ready for immediate use!
