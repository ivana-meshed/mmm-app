# MMM Benchmarking Guide

A clear, step-by-step guide for using the benchmarking system to test different model configurations.

## Prerequisites

### Authentication

Set up your Google Cloud credentials:

```bash
# Use Application Default Credentials (this is what works!)
export GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json
```

This uses the impersonated credentials file created by `gcloud auth application-default login`.

### Verify Setup

```bash
# Check you can access GCS
gsutil ls gs://mmm-app-output/

# Check you can list Cloud Run jobs
gcloud run jobs list --region=europe-west1
```

## Quick Start

### 1. List Available Benchmarks

```bash
python scripts/benchmark_mmm.py --list-configs
```

This shows all pre-configured benchmarks in the `benchmarks/` directory.

### 2. Run a Benchmark

```bash
# Run adstock comparison benchmark
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json
```

This creates job configurations and adds them to the queue.

### 3. Process the Queue

```bash
# Process all pending jobs
python scripts/process_queue_simple.py --loop
```

This launches the jobs on Cloud Run and processes them until the queue is empty.

## Complete Workflow Example

Here's a complete example testing different adstock types:

### Step 1: Review Available Benchmarks

```bash
ls benchmarks/
# adstock_comparison.json
# spend_var_mapping.json
# train_val_test_splits.json
# time_aggregation.json
# comprehensive_benchmark.json
```

### Step 2: Run Adstock Comparison

```bash
# First do a dry run to see what will be created
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --dry-run

# Output shows 3 variants will be created:
# - geometric
# - weibull_cdf
# - weibull_pdf

# Now submit for real
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json
```

### Step 3: Process the Jobs

```bash
# Set authentication
export GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json

# Process all pending jobs
python scripts/process_queue_simple.py --loop
```

You'll see output like:
```
INFO - Loaded queue 'default-dev' from GCS
INFO - Total: 3
INFO - Pending: 3
INFO - Processing job 1/3
INFO - ✅ Launched job: mmm-app-dev-training
...
INFO - ✅ Processed 3 job(s)
```

### Step 4: Monitor Progress

Monitor jobs in the Cloud Console:
```
https://console.cloud.google.com/run/jobs/executions?project=datawarehouse-422511
```

Or via command line:
```bash
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=10
```

### Step 5: Collect Results

Once jobs complete (15-30 minutes each):

```bash
python scripts/benchmark_mmm.py \
  --collect-results adstock_comparison_20260211_120000 \
  --export-format csv
```

Results are saved to:
```
gs://mmm-app-output/benchmarks/adstock_comparison_20260211_120000/results.csv
```

### Step 6: Analyze Results

```python
import pandas as pd

# Load results
df = pd.read_csv('results.csv')

# Compare variants
summary = df.groupby('benchmark_variant')[['rsq_train', 'rsq_val', 'rsq_test', 'nrmse_val', 'decomp_rssd']].mean()
print(summary)

# Find best performer
best = df.loc[df['rsq_val'].idxmax()]
print(f"Best variant: {best['benchmark_variant']}")
print(f"Val R²: {best['rsq_val']:.3f}")
print(f"Test R²: {best['rsq_test']:.3f}")

# Visualize tradeoffs
import plotly.express as px
fig = px.scatter(df, x='nrmse_val', y='decomp_rssd', 
                 color='benchmark_variant',
                 hover_data=['rsq_val', 'rsq_test'])
fig.show()
```

## Benchmark Configuration

### JSON Structure

Create a benchmark configuration file:

```json
{
  "name": "my_test",
  "description": "Testing adstock types",
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
        "type": "geometric",
        "hyperparameter_preset": "Meshed recommend"
      },
      {
        "name": "weibull_cdf",
        "type": "weibull_cdf",
        "hyperparameter_preset": "Meta default"
      }
    ]
  }
}
```

### Configuration Fields

- **name**: Identifier for this benchmark
- **description**: What you're testing
- **base_config**: Reference configuration
  - `country`: Country code (e.g., "de", "uk")
  - `goal`: Target variable (e.g., "UPLOAD_VALUE")
  - `version`: Timestamp of prepared training data
- **iterations**: Robyn iterations per variant (default: 2000)
- **trials**: Robyn trials per variant (default: 5)
- **max_combinations**: Limit total variants generated
- **variants**: Test specifications (see below)

## Test Types

### 1. Adstock Comparison

Test different adstock transformation types:

```json
{
  "variants": {
    "adstock": [
      {"name": "geometric", "type": "geometric"},
      {"name": "weibull_cdf", "type": "weibull_cdf"},
      {"name": "weibull_pdf", "type": "weibull_pdf"}
    ]
  }
}
```

**Evaluation focus:**
- Model fit (R², NRMSE)
- Decomposition quality (decomp.rssd)
- Channel-specific patterns

### 2. Train/Val/Test Splits

Test different split ratios:

```json
{
  "variants": {
    "train_splits": [
      {"name": "70_90", "train_size": [0.7, 0.9]},
      {"name": "75_90", "train_size": [0.75, 0.9]},
      {"name": "80_90", "train_size": [0.8, 0.9]}
    ]
  }
}
```

**Evaluation focus:**
- Val vs test performance gap
- Overfitting indicators
- Generalization capability

### 3. Time Aggregation

Compare different time granularities:

```json
{
  "variants": {
    "time_aggregation": [
      {"name": "daily", "frequency": "none"},
      {"name": "weekly", "frequency": "W"}
    ]
  }
}
```

**Evaluation focus:**
- Fit quality vs granularity tradeoff
- Allocator stability
- Budget optimization feasibility

### 4. Spend→Var Mapping

Test different mapping strategies:

```json
{
  "variants": {
    "spend_var_mapping": [
      {"name": "all_spend", "type": "spend_to_spend"},
      {"name": "all_proxy", "type": "spend_to_proxy"}
    ]
  }
}
```

**Evaluation focus:**
- R² and NRMSE comparison
- ROAS by channel
- Coefficient stability

## Results Analysis

### Metrics Collected

Each benchmark variant produces:

**Model Fit:**
- `rsq_train`, `rsq_val`, `rsq_test` - R² scores
- `nrmse_train`, `nrmse_val`, `nrmse_test` - Normalized RMSE
- `mape` - Mean absolute percentage error

**Decomposition:**
- `decomp_rssd` - Decomposition stability metric

**Business Metrics:**
- `channel_roas` - ROAS per channel (JSON)
- `channel_contributions` - Contribution percentages

**Metadata:**
- `training_time_mins` - Execution time
- `status` - Job completion status
- `pareto_model_count` - Number of Pareto front models

### Analysis Example

```python
import pandas as pd
import plotly.express as px

# Load results
df = pd.read_csv('gs://mmm-app-output/benchmarks/test_id/results.csv')

# Basic comparison
print("\nMean metrics by variant:")
print(df.groupby('benchmark_variant')[['rsq_val', 'nrmse_val', 'decomp_rssd']].mean())

# Find best model
best = df.nlargest(1, 'rsq_val')
print(f"\nBest model: {best['benchmark_variant'].values[0]}")
print(f"Val R²: {best['rsq_val'].values[0]:.3f}")
print(f"Test R²: {best['rsq_test'].values[0]:.3f}")

# Visualize tradeoffs
fig = px.scatter(df, 
                 x='nrmse_val', 
                 y='decomp_rssd',
                 color='benchmark_variant',
                 size='rsq_val',
                 hover_data=['rsq_test', 'training_time_mins'])
fig.update_layout(title='Model Performance Tradeoffs')
fig.show()

# Check consistency across metrics
fig2 = px.scatter(df,
                  x='rsq_val',
                  y='rsq_test',
                  color='benchmark_variant',
                  hover_data=['nrmse_val', 'decomp_rssd'])
fig2.add_shape(type='line', x0=0, y0=0, x1=1, y1=1, 
               line=dict(dash='dash', color='gray'))
fig2.update_layout(title='Validation vs Test Performance')
fig2.show()
```

## Common Use Cases

### Use Case 1: Test Adstock Types

**Goal:** Determine which adstock type works best for your data.

```bash
# Run comparison
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json
python scripts/process_queue_simple.py --loop

# Wait for completion, then analyze
python -c "
import pandas as pd
df = pd.read_csv('results.csv')
print(df.groupby('benchmark_variant')[['rsq_val', 'decomp_rssd']].mean())
"
```

### Use Case 2: Find Optimal Train/Val/Test Split

**Goal:** Find the split ratio that balances fit quality and generalization.

```bash
# Run split tests
python scripts/benchmark_mmm.py --config benchmarks/train_val_test_splits.json
python scripts/process_queue_simple.py --loop

# Analyze val vs test gap
python -c "
import pandas as pd
df = pd.read_csv('results.csv')
df['val_test_gap'] = df['rsq_val'] - df['rsq_test']
print(df[['benchmark_variant', 'rsq_val', 'rsq_test', 'val_test_gap']].sort_values('val_test_gap'))
"
```

### Use Case 3: Daily vs Weekly Aggregation

**Goal:** Determine if weekly aggregation improves stability.

```bash
# Run time aggregation test
python scripts/benchmark_mmm.py --config benchmarks/time_aggregation.json
python scripts/process_queue_simple.py --loop

# Compare stability
python -c "
import pandas as pd
df = pd.read_csv('results.csv')
print(df.groupby('benchmark_variant')[['rsq_val', 'decomp_rssd']].agg(['mean', 'std']))
"
```

### Use Case 4: Spend→Spend vs Spend→Proxy

**Goal:** Test if using proxy variables (sessions, clicks) is better than spend.

```bash
# Run mapping test
python scripts/benchmark_mmm.py --config benchmarks/spend_var_mapping.json
python scripts/process_queue_simple.py --loop

# Compare ROAS patterns
python -c "
import pandas as pd
import json
df = pd.read_csv('results.csv')
for _, row in df.iterrows():
    print(f\"\\n{row['benchmark_variant']}:\")
    roas = json.loads(row['channel_roas'])
    for channel, value in roas.items():
        print(f\"  {channel}: {value:.2f}\")
"
```

## Best Practices

### 1. Start Small

- Test with 2-3 variants first
- Use lower iterations (1000) for initial exploration
- Set reasonable `max_combinations` limits

### 2. Incremental Testing

- Test one dimension at a time
- Use results to inform next tests
- Build up knowledge systematically

### 3. Monitor Resources

- Each variant = full training job (15-30 min)
- 10 variants × 20 min = 3+ hours compute
- Plan accordingly for costs

### 4. Interpret Carefully

- Look for consistent patterns, not single "best" results
- Consider multiple metrics (fit + stability + business)
- Validate on multiple datasets if possible

### 5. Document Findings

- Save configurations and results
- Note which patterns generalize
- Build institutional knowledge

## Troubleshooting

### Authentication Issues

If you get permission errors:

```bash
# Ensure credentials are set
export GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json

# Verify they work
gcloud auth application-default print-access-token
```

### Queue Not Processing

Check queue status:

```bash
# View queue directly
gsutil cat gs://mmm-app-output/robyn-queues/default-dev/queue.json | jq .

# Check for paused status
python -c "
import json
from google.cloud import storage
client = storage.Client()
bucket = client.bucket('mmm-app-output')
blob = bucket.blob('robyn-queues/default-dev/queue.json')
queue = json.loads(blob.download_as_text())
print(f\"Status: {queue.get('status', 'active')}\")
print(f\"Entries: {len(queue.get('entries', []))}\")
"
```

### Jobs Failing

Check job logs:

```bash
# Get recent executions
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=5

# Get logs for specific execution
gcloud run jobs executions describe EXECUTION_ID \
  --job=mmm-app-dev-training \
  --region=europe-west1
```

## Next Steps

- Review example benchmarks in `benchmarks/` directory
- Check detailed documentation in `benchmarks/README.md`
- See workflow examples in `benchmarks/WORKFLOW_EXAMPLE.md`
- Explore architecture in `ARCHITECTURE.md`

## Summary

This guide covers:
- ✅ Authentication setup (what actually works)
- ✅ Running benchmarks to test configurations
- ✅ Processing the job queue
- ✅ Analyzing and comparing results
- ✅ Common use cases with examples

You can now systematically test different MMM configurations to find what works best for your data!
