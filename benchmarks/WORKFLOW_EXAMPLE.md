# Benchmarking Workflow Example

This guide walks through a complete benchmarking workflow from setup to analysis.

## Scenario

You want to determine whether geometric or Weibull CDF adstock works better for your German market data, and whether daily or weekly aggregation improves model stability.

## Step 1: Prepare Base Configuration

First, ensure you have a `selected_columns.json` from the "Prepare Training Data" page:

```
gs://mmm-app-output/training_data/de/UPLOAD_VALUE/20251211_115528/selected_columns.json
```

This file contains:
- Selected media channels
- Context variables
- Factor variables
- Organic variables
- Data version reference

## Step 2: Create Benchmark Configuration

Create `benchmarks/my_test.json`:

```json
{
  "name": "de_adstock_and_aggregation",
  "description": "Test adstock types and time aggregation for German market",
  "base_config": {
    "country": "de",
    "goal": "UPLOAD_VALUE",
    "version": "20251211_115528"
  },
  "iterations": 2000,
  "trials": 5,
  "max_combinations": 10,
  "variants": {
    "adstock": [
      {
        "name": "geometric",
        "description": "Geometric adstock (exponential decay)",
        "type": "geometric"
      },
      {
        "name": "weibull_cdf",
        "description": "Weibull CDF adstock",
        "type": "weibull_cdf"
      }
    ],
    "time_aggregation": [
      {
        "name": "daily",
        "description": "Daily granularity",
        "frequency": "none"
      },
      {
        "name": "weekly",
        "description": "Weekly aggregation",
        "frequency": "W"
      }
    ]
  }
}
```

This will generate 4 variants:
1. geometric + daily
2. geometric + weekly
3. weibull_cdf + daily
4. weibull_cdf + weekly

## Step 3: Test Configuration (Dry Run)

First, verify that your configuration generates the expected variants:

```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/my_test.json \
  --dry-run
```

Expected output:
```
2024-02-10 18:00:00 - INFO - Loaded benchmark: de_adstock_and_aggregation
2024-02-10 18:00:00 - INFO - Description: Test adstock types and time aggregation for German market
2024-02-10 18:00:00 - INFO - Loaded base config: de/UPLOAD_VALUE
2024-02-10 18:00:00 - INFO - Generated 4 test variants
2024-02-10 18:00:00 - INFO - Dry run - not submitting jobs

Generated 4 variants:
1. adstock: geometric
2. adstock: weibull_cdf
3. time_aggregation: daily
4. time_aggregation: weekly

Benchmark ID: de_adstock_and_aggregation_20240210_180000
Plan saved: gs://mmm-app-output/benchmarks/de_adstock_and_aggregation_20240210_180000/plan.json
```

## Step 4: Submit Benchmark

When ready, submit the benchmark to the training queue:

```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/my_test.json \
  --queue-name default
```

Expected output:
```
✅ Benchmark submitted successfully!
Benchmark ID: de_adstock_and_aggregation_20240210_180000
Variants queued: 4
Queue: default
Plan: gs://mmm-app-output/benchmarks/de_adstock_and_aggregation_20240210_180000/plan.json

Monitor progress in the Streamlit app (Run Experiment → Queue Monitor)
```

## Step 5: Monitor Execution

Navigate to the Streamlit app → "Run Experiment" → "Queue Monitor" tab.

You'll see your benchmark jobs in the queue with status:
- **PENDING**: Waiting to execute
- **RUNNING**: Currently training
- **SUCCEEDED**: Training completed successfully
- **FAILED**: Training failed (check logs)

Each job will have metadata showing:
- `benchmark_id`: Your benchmark identifier
- `benchmark_test`: Type of test (adstock, time_aggregation, etc.)
- `benchmark_variant`: Specific variant name

Expected execution time:
- 4 variants × ~15 min/job = ~60 minutes total
- Jobs run sequentially through the queue

## Step 6: Collect Results

Once all jobs complete, collect the results:

```bash
python scripts/benchmark_mmm.py \
  --collect-results de_adstock_and_aggregation_20240210_180000 \
  --export-format csv
```

This will:
1. Search GCS for `model_summary.json` files matching your benchmark
2. Extract performance metrics from each variant
3. Export aggregated results to CSV

Output location:
```
gs://mmm-app-output/benchmarks/de_adstock_and_aggregation_20240210_180000/results_20240210_190000.csv
```

## Step 7: Analyze Results

Download the results CSV and analyze:

```python
import pandas as pd

# Load results
df = pd.read_csv('results.csv')

# View summary statistics
print(df[['benchmark_variant', 'rsq_val', 'nrmse_val', 'decomp_rssd']].describe())

# Compare by test type
print("\nAdstock Comparison:")
print(df[df['benchmark_test'] == 'adstock'].groupby('benchmark_variant')[
    ['rsq_val', 'nrmse_val', 'decomp_rssd']
].mean())

print("\nTime Aggregation Comparison:")
print(df[df['benchmark_test'] == 'time_aggregation'].groupby('benchmark_variant')[
    ['rsq_val', 'nrmse_val', 'decomp_rssd']
].mean())

# Find best overall model
best_idx = df['rsq_val'].idxmax()
best = df.loc[best_idx]
print(f"\nBest Model: {best['benchmark_variant']}")
print(f"  R² (val): {best['rsq_val']:.4f}")
print(f"  NRMSE (val): {best['nrmse_val']:.4f}")
print(f"  Decomp RSSD: {best['decomp_rssd']:.4f}")
```

## Step 8: Visualize Comparisons

Create visualizations to understand tradeoffs:

```python
import plotly.express as px

# Validation performance vs decomposition stability
fig = px.scatter(
    df,
    x='nrmse_val',
    y='decomp_rssd',
    color='benchmark_test',
    hover_data=['benchmark_variant', 'rsq_val'],
    title='Model Performance: Fit vs Stability'
)
fig.show()

# Compare test types
fig = px.box(
    df,
    x='benchmark_test',
    y='rsq_val',
    color='benchmark_variant',
    title='R² by Test Type'
)
fig.show()
```

## Step 9: Document Findings

Based on results, document your findings:

```markdown
## Benchmark Results: DE Adstock & Aggregation

**Date**: 2024-02-10
**Benchmark ID**: de_adstock_and_aggregation_20240210_180000
**Variants Tested**: 4

### Key Findings

1. **Adstock Type**
   - Geometric: R²=0.85, NRMSE=0.12, decomp.rssd=0.08
   - Weibull CDF: R²=0.83, NRMSE=0.14, decomp.rssd=0.09
   - **Winner**: Geometric (better fit, lower error)

2. **Time Aggregation**
   - Daily: R²=0.87, decomp.rssd=0.10
   - Weekly: R²=0.82, decomp.rssd=0.06
   - **Winner**: Daily for fit, Weekly for stability

3. **Recommendation**
   - Use geometric adstock for DE market
   - Prefer daily granularity for better fit
   - Consider weekly if allocator stability is critical

### Next Steps
- Test geometric adstock on other markets
- Investigate spend→var mapping with daily data
```

## Step 10: Apply Learnings

Use benchmark insights to configure production models:

1. **Update default settings** based on findings
2. **Create presets** for similar markets
3. **Run validation** on holdout period
4. **Document patterns** for future reference

## Advanced: Multi-Market Benchmarking

To test across multiple markets:

```json
{
  "name": "multi_market_adstock",
  "description": "Compare adstock types across markets",
  "variants": {
    "markets": [
      {
        "name": "de_geometric",
        "base_config": {"country": "de", ...},
        "adstock": "geometric"
      },
      {
        "name": "de_weibull",
        "base_config": {"country": "de", ...},
        "adstock": "weibull_cdf"
      },
      {
        "name": "uk_geometric",
        "base_config": {"country": "uk", ...},
        "adstock": "geometric"
      }
    ]
  }
}
```

## Troubleshooting

### Jobs Not Starting
- Check queue is running in Streamlit app
- Verify Cloud Run job configuration
- Check GCS bucket permissions

### Results Not Found
- Ensure all jobs completed successfully
- Check GCS paths match expected pattern
- Verify benchmark_id is correct

### Unexpected Results
- Review job logs in Cloud Logging
- Check input data quality
- Verify configuration parameters

### High Compute Costs
- Reduce iterations/trials for exploration
- Use max_combinations to limit variants
- Test on smaller date ranges first

## Best Practices

1. **Start Small**: Test 2-3 variants before scaling up
2. **Document Everything**: Save configurations and findings
3. **Iterate**: Use results to refine next benchmarks
4. **Validate**: Test findings on holdout data
5. **Share**: Build institutional knowledge across team
