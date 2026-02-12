# MMM Benchmarking System

Systematically evaluate different Robyn/MMM configurations to identify optimal settings and understand how different assumptions affect model performance.

## Purpose

The benchmarking system helps answer questions like:
- **Spend→Var Mapping**: Is spend→spend always better than spend→proxy? Does it vary by channel type?
- **Adstock Choice**: Do geometric, Weibull CDF, or Weibull PDF work better for different channels?
- **Train/Val/Test Splits**: What split ratios provide the best generalization?
- **Time Aggregation**: Is daily or weekly aggregation better for your data?
- **Seasonality Window**: Should the training window extend beyond paid media history?

## Overview

The benchmarking workflow:
1. **Define Test**: Create a JSON config specifying what to test
2. **Generate Variants**: Script generates configuration variants
3. **Execute**: Submit variants to Cloud Run training queue
4. **Collect Results**: Gather metrics from all variants
5. **Analyze**: Compare performance across configurations

## Quick Start

### 1. List Available Benchmarks

```bash
python scripts/benchmark_mmm.py --list-configs
```

### 2. Run a Benchmark Test

```bash
# Dry run (generate variants but don't submit)
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --dry-run

# Execute benchmark
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json
```

### 3. Collect Results

```bash
python scripts/benchmark_mmm.py \
  --collect-results benchmark_id_20240101_120000 \
  --export-format csv
```

## Benchmark Configuration

Benchmark configs are JSON files in the `benchmarks/` directory.

### Configuration Schema

```json
{
  "name": "test_name",
  "description": "What this benchmark tests",
  "base_config": {
    "country": "de",
    "goal": "UPLOAD_VALUE",
    "version": "20251211_115528"
  },
  "iterations": 2000,
  "trials": 5,
  "max_combinations": 20,
  "variants": {
    "test_type": [
      {
        "name": "variant_name",
        "description": "What this variant tests",
        ...variant-specific params...
      }
    ]
  }
}
```

### Fields

- **name**: Benchmark identifier (used in output paths)
- **description**: Human-readable description
- **base_config**: Reference to base `selected_columns.json`
  - `country`: Country code
  - `goal`: Goal/KPI name
  - `version`: Timestamp of prepared training data
- **iterations**: Robyn iterations per variant (default: 2000)
- **trials**: Robyn trials per variant (default: 5)
- **max_combinations**: Limit on total variants to generate
- **variants**: Dictionary of test types and their specifications

## Test Types

### 1. Spend→Var Mapping Tests

Test different ways of mapping spend columns to media variables.

**Config Key**: `spend_var_mapping`

**Variant Types**:
- `spend_to_spend`: All channels map spend → spend
- `spend_to_proxy`: All channels map spend → proxy (sessions/clicks/impressions)
- `mixed_by_funnel`: Upper funnel → proxy, lower funnel → spend

**Example**:
```json
{
  "variants": {
    "spend_var_mapping": [
      {
        "name": "all_spend_to_spend",
        "type": "spend_to_spend"
      },
      {
        "name": "mixed_by_funnel",
        "type": "mixed_by_funnel",
        "upper_funnel_channels": ["SPEND_FACEBOOK"],
        "lower_funnel_channels": ["SPEND_GOOGLE"],
        "proxy_mapping": {
          "SPEND_FACEBOOK": "FB_IMPRESSIONS"
        }
      }
    ]
  }
}
```

**Evaluation Metrics**:
- R² (train/val/test)
- NRMSE (train/val/test)
- decomp.rssd (decomposition stability)
- ROAS by channel
- Coefficient stability

### 2. Adstock Tests

Compare different adstock transformation types.

**Config Key**: `adstock`

**Types**:
- `geometric`: Exponential decay (Robyn default)
- `weibull_cdf`: Weibull cumulative distribution
- `weibull_pdf`: Weibull probability density

**Example**:
```json
{
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

**Evaluation Metrics**:
- Model fit (R², NRMSE)
- Decomposition quality (decomp.rssd)
- Channel-specific patterns
- Lag/carryover effects

### 3. Train/Val/Test Split Tests

Test different data split ratios.

**Config Key**: `train_splits`

**Parameters**:
- `train_size`: Array of two values [train_end, val_end]
  - Example: `[0.7, 0.9]` = 70% train, 20% val, 10% test

**Example**:
```json
{
  "variants": {
    "train_splits": [
      {
        "name": "70_90",
        "train_size": [0.7, 0.9]
      },
      {
        "name": "75_90",
        "train_size": [0.75, 0.9]
      }
    ]
  }
}
```

**Evaluation Focus**:
- Validation vs test performance gap
- Overfitting indicators
- Decomposition stability across splits

### 4. Time Aggregation Tests

Compare daily vs weekly (vs monthly) aggregation.

**Config Key**: `time_aggregation`

**Frequencies**:
- `none`: No resampling (daily if data is daily)
- `W`: Weekly aggregation
- `M`: Monthly aggregation

**Example**:
```json
{
  "variants": {
    "time_aggregation": [
      {
        "name": "daily",
        "frequency": "none"
      },
      {
        "name": "weekly",
        "frequency": "W"
      }
    ]
  }
}
```

**Evaluation Focus**:
- Fit quality vs granularity tradeoff
- Allocator stability
- Budget optimization feasibility

### 5. Seasonality Window Tests

Test impact of extending training window beyond paid media history.

**Config Key**: `seasonality_window`

**Example**:
```json
{
  "variants": {
    "seasonality_window": [
      {
        "name": "aligned_to_media",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31"
      },
      {
        "name": "extended_seasonality",
        "start_date": "2023-01-01",
        "end_date": "2024-12-31"
      }
    ]
  }
}
```

**Evaluation Focus**:
- Does longer window improve seasonality/trend fit?
- Does trend "gobble up" media signal?
- Media contribution stability

## Example Benchmarks

### Spend→Var Mapping Test
`benchmarks/spend_var_mapping.json`

Tests whether spend→spend or spend→proxy is better overall and by funnel position.

### Adstock Comparison
`benchmarks/adstock_comparison.json`

Compares geometric, Weibull CDF, and Weibull PDF adstock types.

### Train/Val/Test Splits
`benchmarks/train_val_test_splits.json`

Tests multiple split ratios to find optimal balance.

### Time Aggregation
`benchmarks/time_aggregation.json`

Compares daily vs weekly aggregation.

### Comprehensive Benchmark
`benchmarks/comprehensive_benchmark.json`

⚠️ **Use with caution**: Tests multiple dimensions simultaneously, generating many combinations.

## Output Metrics

The benchmarking system collects these metrics for each variant:

### Model Fit
- **R²** (train/val/test): Coefficient of determination
- **NRMSE** (train/val/test): Normalized root mean squared error
- **MAPE**: Mean absolute percentage error

### Decomposition Quality
- **decomp.rssd**: Decomposition sum of squared differences (stability metric)

### Business Metrics
- **ROAS by channel**: Return on ad spend per media channel
- **Channel contributions**: Percentage contribution to outcome
- **Trend/seasonality share**: How much variance is explained by trend vs media

### Execution Metadata
- Training time
- Job status (success/failure)
- Pareto front model count
- Hyperparameter ranges used

## Results Analysis

Results are exported as CSV/Parquet tables with columns:
- `benchmark_id`: Unique benchmark identifier
- `variant_name`: Variant identifier
- `test_type`: Type of test (adstock, train_split, etc.)
- `config_params`: JSON of configuration used
- `rsq_train`, `rsq_val`, `rsq_test`: R² metrics
- `nrmse_train`, `nrmse_val`, `nrmse_test`: NRMSE metrics
- `decomp_rssd`: Decomposition stability
- `channel_roas`: ROAS per channel (JSON)
- `training_time_mins`: Execution time
- `status`: Job status

### Analysis Workflow

1. **Load Results**:
   ```python
   import pandas as pd
   df = pd.read_csv('gs://mmm-app-output/benchmarks/test_id/results.csv')
   ```

2. **Compare Variants**:
   ```python
   # Compare by test type
   df.groupby('test_type')[['rsq_val', 'decomp_rssd']].mean()
   
   # Find best performer
   best = df.loc[df['rsq_val'].idxmax()]
   ```

3. **Visualize**:
   ```python
   import plotly.express as px
   fig = px.scatter(df, x='nrmse_val', y='decomp_rssd', 
                    color='test_type', hover_data=['variant_name'])
   ```

## Best Practices

### 1. Start Small
- Begin with single-dimension tests (e.g., only adstock)
- Use lower iterations/trials for initial exploration (e.g., 1000/3)
- Set reasonable `max_combinations` limits

### 2. Incremental Approach
- Test one aspect at a time
- Use results from previous benchmarks to inform next tests
- Build up a library of learnings over time

### 3. Resource Management
- Each variant is a full training job
- 20 variants × 15 min/job = 5 hours of compute
- Consider cost implications for large benchmark runs

### 4. Result Interpretation
- Look for consistent patterns, not single "best" results
- Consider multiple metrics (fit + stability + business metrics)
- Validate findings on multiple datasets/countries

### 5. Documentation
- Document learnings in benchmark descriptions
- Keep notes on which patterns generalize vs. dataset-specific
- Build institutional knowledge over time

## Limitations

**Current Limitations** (as of initial implementation):
- Job submission to queue not yet fully implemented
- Results collection requires manual integration
- No automatic retry/failure handling
- No multi-dimensional variant generation (combinations)

**Planned Improvements**:
- Integrate with existing Cloud Run job queue
- Automatic result aggregation from GCS
- Statistical significance testing
- Visualization dashboard
- Multi-dimensional test support

## Integration with Existing System

The benchmark system builds on existing infrastructure:
- Uses same `selected_columns.json` format
- Submits to existing Cloud Run training queue
- Results stored in standard GCS locations
- Compatible with existing job monitoring

## Support

For questions or issues:
1. Check example benchmark configs in `benchmarks/` directory
2. Review this README
3. Contact the data science team

## References

- [Robyn Documentation](https://github.com/facebookexperimental/Robyn)
- Internal: `ARCHITECTURE.md` for system architecture
- Internal: `DEVELOPMENT.md` for local testing
