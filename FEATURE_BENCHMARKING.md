# MMM Benchmarking Feature

## Overview

The MMM benchmarking system enables systematic evaluation of different Robyn/MMM configurations to identify optimal settings and understand how different assumptions affect model performance.

**Problem Solved**: Right now it's hard to tell which Robyn configuration is better for a given goal, and we can't systematically evaluate whether assumptions (adstock choice, spend→var mapping, etc.) hold across datasets. This makes onboarding and tuning subjective and non-reproducible.

**Solution**: Automated benchmarking script that generates and queues MMM variants, then collects and compares results across key metrics.

## Quick Start

```bash
# 1. Create benchmark configuration
cat > benchmarks/my_test.json << EOF
{
  "name": "adstock_test",
  "description": "Compare adstock types",
  "base_config": {
    "country": "de",
    "goal": "UPLOAD_VALUE", 
    "version": "20251211_115528"
  },
  "variants": {
    "adstock": [
      {"name": "geometric", "type": "geometric"},
      {"name": "weibull_cdf", "type": "weibull_cdf"}
    ]
  }
}
EOF

# 2. Submit benchmark
python scripts/benchmark_mmm.py --config benchmarks/my_test.json

# 3. Monitor in Streamlit app
# Navigate to: Run Experiment → Queue Monitor

# 4. Collect results (after jobs complete)
python scripts/benchmark_mmm.py \
  --collect-results adstock_test_20240210_180000 \
  --export-format csv
```

## Supported Test Types

1. **Spend→Var Mapping** - Test spend→spend vs spend→proxy vs mixed strategies
2. **Adstock Comparison** - Compare geometric, Weibull CDF, Weibull PDF
3. **Train/Val/Test Splits** - Test different split ratios for generalization
4. **Time Aggregation** - Daily vs weekly vs monthly granularity
5. **Seasonality Windows** - Extended vs aligned training windows

## Key Features

- ✅ **Automated Variant Generation** - Generates test configurations from specs
- ✅ **Queue Integration** - Uses existing Cloud Run job queue (no new infrastructure)
- ✅ **Results Collection** - Automatically collects metrics from completed jobs
- ✅ **CSV Export** - Generates comparison tables for analysis
- ✅ **Flexible Configuration** - JSON-based, extensible system
- ✅ **Comprehensive Docs** - Complete usage guides and examples

## Files

**Core Script:**
- `scripts/benchmark_mmm.py` - Main benchmarking tool

**Example Configurations:**
- `benchmarks/spend_var_mapping.json` - Spend→var mapping tests
- `benchmarks/adstock_comparison.json` - Adstock type comparison
- `benchmarks/train_val_test_splits.json` - Split ratio tests
- `benchmarks/time_aggregation.json` - Time granularity tests
- `benchmarks/comprehensive_benchmark.json` - Multi-dimensional tests

**Documentation:**
- `benchmarks/README.md` - Complete technical reference
- `benchmarks/WORKFLOW_EXAMPLE.md` - Step-by-step workflow guide

**Tests:**
- `tests/test_benchmark_mmm.py` - Unit tests

## Use Cases

### Internal: Model Preconfiguration
- Test configurations on customer data before deployment
- Identify optimal settings for specific industries/markets
- Build library of proven configurations

### Internal: Pattern Learning
- Learn generalizable MMM patterns over time
- Understand which settings work for different scenarios
- Build institutional knowledge

### Specific Tests

**1. Paid Media Spend→Var Mapping**
- Is spend→spend always better than spend→proxy?
- Does it differ by media type (upper vs lower funnel)?
- Test configs: all-spend, all-proxy, mixed by funnel

**2. Training vs Production Performance**
- Does benchmark performance predict production performance?
- Test multiple train/val/test split ratios
- Focus on validation metrics + decomp.rssd stability

**3. Adstock Choice**
- Do we see consistent patterns per channel?
- Test geometric, Weibull CDF, Weibull PDF
- Goal: identify reusable defaults for onboarding

**4. Time Aggregation**
- Daily vs weekly vs monthly?
- Daily: better decomp, worse fit?
- Weekly: more stable allocator?

**5. Seasonality Window**
- Should we extend window beyond paid media history?
- Or does it cause trend to "gobble up" media signal?
- Test aligned vs extended windows

## Output Metrics

Each benchmark variant collects:

**Model Fit:**
- R² (train/val/test)
- NRMSE (train/val/test)
- MAPE

**Decomposition:**
- decomp.rssd (stability metric)

**Business Metrics:**
- ROAS by channel
- Channel contributions
- Trend/seasonality share

**Metadata:**
- Training time
- Pareto model count
- Hyperparameters used

## Analysis Workflow

```python
import pandas as pd
import plotly.express as px

# Load results
df = pd.read_csv('gs://mmm-app-output/benchmarks/test_id/results.csv')

# Compare variants
summary = df.groupby('benchmark_test')[['rsq_val', 'decomp_rssd']].mean()
print(summary)

# Visualize tradeoffs
fig = px.scatter(df, x='nrmse_val', y='decomp_rssd', 
                 color='benchmark_test', 
                 hover_data=['benchmark_variant'])
fig.show()

# Find best model
best = df.loc[df['rsq_val'].idxmax()]
print(f"Best: {best['benchmark_variant']} (R²={best['rsq_val']:.3f})")
```

## Best Practices

1. **Start Small** - Test 2-3 variants before scaling
2. **Use Dry Run** - Always test with --dry-run first
3. **Monitor Queue** - Watch progress in Streamlit app
4. **Document Findings** - Save learnings for future reference
5. **Iterate** - Use results to refine next benchmarks

## Resource Planning

Typical costs:
- 10 variants × 15 min/job = 2.5 hours compute
- ~$5-10 per benchmark
- Plan accordingly for large-scale tests

## Limitations

- Results matching is heuristic-based (uses country/adstock/config matching)
- Requires all jobs to complete before collection
- No real-time progress tracking for result collection
- Parquet export requires pyarrow

## Future Enhancements

- Add benchmark_id to job config for exact result matching
- Implement streaming results collection (collect as jobs complete)
- Add statistical significance testing
- Build visualization dashboard
- Support multi-dimensional combinations (e.g., adstock × aggregation)

## Support

For questions or issues:
- Review documentation in `benchmarks/README.md`
- Check workflow example in `benchmarks/WORKFLOW_EXAMPLE.md`
- Examine example configs in `benchmarks/*.json`
- Run unit tests: `python -m unittest tests.test_benchmark_mmm`

## Related Files

- Architecture: `ARCHITECTURE.md`
- Development: `DEVELOPMENT.md`
- Queue System: `app/app_shared.py` (load_queue_from_gcs, save_queue_to_gcs)
- Job Configuration: `app/nav/Run_Experiment.py`
- Results Format: `r/extract_model_summary.R`
