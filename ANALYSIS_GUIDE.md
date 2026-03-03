# Analysis Guide - Benchmark Results

## Overview

This guide explains how to collect, analyze, and interpret benchmark results to make informed decisions about MMM configurations.

## Collecting Results

### Step 1: Identify Benchmark ID

```bash
# List completed benchmarks
python scripts/benchmark_mmm.py --list-results

# Or use the ID from submission output
# Example: adstock_comparison_20260212_151148
```

### Step 2: Collect and Export

```bash
# Export to CSV (recommended for analysis)
python scripts/benchmark_mmm.py \
  --collect-results adstock_comparison_20260212_151148 \
  --export-format csv

# Export to Parquet (for larger datasets)
python scripts/benchmark_mmm.py \
  --collect-results adstock_comparison_20260212_151148 \
  --export-format parquet

# Output: results.csv or results.parquet
```

### Step 3: Verify Collection

```bash
# Check output file exists
ls -lh results.csv

# Quick preview
head -10 results.csv

# Or with column names
python3 -c "import pandas as pd; df = pd.read_csv('results.csv'); print(df.columns.tolist())"
```

## Result Structure

### Key Metrics

**Model Fit Metrics:**
- `rsq_train` - R² on training set (0-1, higher better)
- `rsq_val` - R² on validation set (most important)
- `rsq_test` - R² on test set (production prediction)
- `nrmse_train` - Normalized RMSE on training
- `nrmse_val` - Normalized RMSE on validation (lower better)
- `nrmse_test` - Normalized RMSE on test

**Decomposition Metrics:**
- `decomp_rssd` - Decomposition quality (lower better)
- `mape` - Mean Absolute Percentage Error

**Model Configuration:**
- `benchmark_test` - Test type (adstock, train_splits, etc.)
- `benchmark_variant` - Variant name (geometric, 70_90, etc.)
- `adstock` - Adstock type used
- `train_size` - Train/val split used
- `iterations` - Number of iterations
- `trials` - Number of trials

## Analysis Workflows

### Workflow 1: Compare Adstock Types

**Question:** Which adstock transformation works best for our data?

```python
import pandas as pd
import matplotlib.pyplot as plt

# Load results
df = pd.read_csv('results.csv')

# Filter adstock benchmark
adstock_df = df[df['benchmark_test'] == 'adstock']

# Compare by variant
comparison = adstock_df.groupby('benchmark_variant')[
    ['rsq_val', 'nrmse_val', 'decomp_rssd']
].mean()

print("\nAdstock Comparison:")
print(comparison.sort_values('rsq_val', ascending=False))

# Visualize
fig, axes = plt.subplots(1, 3, figsize=(15, 5))

comparison['rsq_val'].plot(kind='bar', ax=axes[0], title='R² Validation')
comparison['nrmse_val'].plot(kind='bar', ax=axes[1], title='NRMSE Validation')
comparison['decomp_rssd'].plot(kind='bar', ax=axes[2], title='Decomp RSSD')

plt.tight_layout()
plt.savefig('adstock_comparison.png')
print("Saved: adstock_comparison.png")
```

**Interpretation:**
- Highest `rsq_val`: Best fit
- Lowest `nrmse_val`: Best prediction error
- Lowest `decomp_rssd`: Most stable decomposition
- **Recommendation:** Choose variant that balances all three

### Workflow 2: Evaluate Train/Val/Test Splits

**Question:** Which split ratio generalizes best?

```python
import pandas as pd

df = pd.read_csv('results.csv')
splits_df = df[df['benchmark_test'] == 'train_splits']

# Compare validation vs test performance
comparison = splits_df.groupby('benchmark_variant')[
    ['rsq_val', 'rsq_test', 'nrmse_val', 'nrmse_test']
].mean()

# Calculate val-test gap
comparison['rsq_gap'] = comparison['rsq_val'] - comparison['rsq_test']
comparison['nrmse_gap'] = comparison['nrmse_test'] - comparison['nrmse_val']

print("\nTrain/Val/Test Split Comparison:")
print(comparison.sort_values('rsq_gap'))

print("\nBest split (smallest gap):")
best_split = comparison['rsq_gap'].abs().idxmin()
print(f"  {best_split}")
print(comparison.loc[best_split])
```

**Interpretation:**
- Small `rsq_gap`: Model generalizes well
- Large `rsq_gap`: Overfitting, choose smaller train size
- Similar `rsq_val` and `rsq_test`: Robust model
- **Recommendation:** Choose split with smallest gap

### Workflow 3: Compare Time Aggregation

**Question:** Daily vs Weekly - which is better?

```python
import pandas as pd

df = pd.read_csv('results.csv')
time_df = df[df['benchmark_test'] == 'time_aggregation']

comparison = time_df.groupby('benchmark_variant')[
    ['rsq_val', 'nrmse_val', 'decomp_rssd']
].mean()

print("\nTime Aggregation Comparison:")
print(comparison)

# Check stability
print("\nStability (lower std = more stable):")
stability = time_df.groupby('benchmark_variant')[
    ['rsq_val', 'nrmse_val']
].std()
print(stability)
```

**Interpretation:**
- **Daily:** Often better decomposition, may overfit
- **Weekly:** More stable, may miss short-term patterns
- Lower std: More stable across iterations
- **Recommendation:** 
  - Use daily if decomp_rssd significantly better
  - Use weekly if stability is priority

### Workflow 4: Analyze Spend→Variable Mapping

**Question:** Should we use spend or proxy (sessions) as media variable?

```python
import pandas as pd

df = pd.read_csv('results.csv')
mapping_df = df[df['benchmark_test'] == 'spend_var_mapping']

comparison = mapping_df.groupby('benchmark_variant')[
    ['rsq_val', 'nrmse_val', 'decomp_rssd']
].mean()

print("\nSpend→Variable Mapping Comparison:")
print(comparison.sort_values('rsq_val', ascending=False))

# Calculate relative performance
baseline = comparison.loc['spend_to_spend']
for variant in comparison.index:
    print(f"\n{variant} vs spend_to_spend:")
    print(f"  R² change: {(comparison.loc[variant, 'rsq_val'] - baseline['rsq_val']):.4f}")
    print(f"  NRMSE change: {(comparison.loc[variant, 'nrmse_val'] - baseline['nrmse_val']):.4f}")
```

**Interpretation:**
- `spend_to_spend`: Direct cost impact
- `spend_to_proxy`: May capture ad delivery better
- `mixed_by_funnel`: Optimizes by channel type
- **Recommendation:** Choose based on business logic and fit

### Workflow 5: Comprehensive Analysis

**Question:** What's the overall best configuration?

```python
import pandas as pd

df = pd.read_csv('results.csv')

# Rank by validation performance
df['rsq_rank'] = df['rsq_val'].rank(ascending=False)
df['nrmse_rank'] = df['nrmse_val'].rank(ascending=True)
df['decomp_rank'] = df['decomp_rssd'].rank(ascending=True)

# Combined score (lower is better)
df['combined_rank'] = df['rsq_rank'] + df['nrmse_rank'] + df['decomp_rank']

# Top configurations
top_10 = df.nsmallest(10, 'combined_rank')[
    ['benchmark_test', 'benchmark_variant', 'rsq_val', 
     'nrmse_val', 'decomp_rssd', 'combined_rank']
]

print("\nTop 10 Configurations:")
print(top_10)

# Best per test type
print("\nBest per test type:")
for test_type in df['benchmark_test'].unique():
    best = df[df['benchmark_test'] == test_type].nsmallest(1, 'combined_rank')
    print(f"\n{test_type}: {best['benchmark_variant'].values[0]}")
    print(f"  R²: {best['rsq_val'].values[0]:.4f}")
    print(f"  NRMSE: {best['nrmse_val'].values[0]:.4f}")
```

## Quality Checks

### Check 1: Reasonable Metrics

```python
import pandas as pd

df = pd.read_csv('results.csv')

# Check ranges
print("Metric Ranges:")
print(f"R² validation: {df['rsq_val'].min():.3f} to {df['rsq_val'].max():.3f}")
print(f"NRMSE validation: {df['nrmse_val'].min():.3f} to {df['nrmse_val'].max():.3f}")

# Flag suspicious results
suspicious = df[
    (df['rsq_val'] < 0) | (df['rsq_val'] > 1) |
    (df['nrmse_val'] < 0) | (df['nrmse_val'] > 1)
]

if len(suspicious) > 0:
    print(f"\n⚠️ WARNING: {len(suspicious)} suspicious results found")
    print(suspicious[['benchmark_variant', 'rsq_val', 'nrmse_val']])
else:
    print("\n✅ All metrics in reasonable ranges")
```

### Check 2: Consistency Across Trials

```python
import pandas as pd

df = pd.read_csv('results.csv')

# If multiple trials exist, check consistency
if 'trial' in df.columns:
    consistency = df.groupby('benchmark_variant')['rsq_val'].agg(['mean', 'std'])
    consistency['cv'] = consistency['std'] / consistency['mean']  # Coefficient of variation
    
    print("\nConsistency Check (Coefficient of Variation):")
    print(consistency.sort_values('cv'))
    
    if consistency['cv'].max() > 0.1:
        print("\n⚠️ High variability detected - consider more trials")
    else:
        print("\n✅ Results are consistent across trials")
```

### Check 3: All Variants Completed

```python
import pandas as pd

df = pd.read_csv('results.csv')

# Expected counts per benchmark type
expected = {
    'adstock': 3,
    'train_splits': 5,
    'time_aggregation': 2,
    'spend_var_mapping': 3
}

actual = df.groupby('benchmark_test')['benchmark_variant'].nunique()

print("\nCompletion Check:")
for test_type, expected_count in expected.items():
    actual_count = actual.get(test_type, 0)
    status = "✅" if actual_count == expected_count else "⚠️"
    print(f"{status} {test_type}: {actual_count}/{expected_count} variants")
```

## Decision Framework

### Step 1: Identify Goals

**Model Fit Priority:**
- Focus on `rsq_val` and `nrmse_val`
- Choose configuration with highest R² and lowest NRMSE

**Stability Priority:**
- Focus on `decomp_rssd` and consistency
- Choose configuration with most stable decomposition

**Generalization Priority:**
- Focus on val vs test gap
- Choose split ratio that generalizes best

### Step 2: Apply Business Constraints

**Data Availability:**
- Daily aggregation requires granular data
- Weekly more forgiving of data gaps

**Budget Optimization:**
- Spend→spend better for budget optimization
- Spend→proxy better for delivery optimization

**Seasonality:**
- Longer seasonality windows if strong patterns
- Shorter windows if rapid market changes

### Step 3: Make Recommendation

**Template:**
```
Based on benchmark results, recommended configuration:

1. Adstock: [best_adstock]
   - R² validation: [value]
   - Rationale: [why this one]

2. Train/Val/Test Split: [best_split]
   - Val-test gap: [value]
   - Rationale: [why this one]

3. Time Aggregation: [daily/weekly]
   - Decomp RSSD: [value]
   - Rationale: [why this one]

4. Spend→Variable Mapping: [best_mapping]
   - NRMSE validation: [value]
   - Rationale: [why this one]

Expected Performance:
- R² validation: [estimated]
- NRMSE validation: [estimated]
- Decomp RSSD: [estimated]
```

## Visualization Examples

### Multi-Metric Comparison

```python
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

df = pd.read_csv('results.csv')

# Heatmap of performance
pivot = df.pivot_table(
    values=['rsq_val', 'nrmse_val', 'decomp_rssd'],
    index='benchmark_test',
    columns='benchmark_variant',
    aggfunc='mean'
)

fig, axes = plt.subplots(1, 3, figsize=(18, 5))

for idx, metric in enumerate(['rsq_val', 'nrmse_val', 'decomp_rssd']):
    sns.heatmap(pivot[metric], annot=True, fmt='.3f', ax=axes[idx], cmap='RdYlGn_r')
    axes[idx].set_title(f'{metric.upper()} by Test & Variant')

plt.tight_layout()
plt.savefig('benchmark_heatmap.png')
```

### Performance Distribution

```python
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv('results.csv')

fig, axes = plt.subplots(2, 2, figsize=(12, 10))

df.boxplot(column='rsq_val', by='benchmark_test', ax=axes[0,0])
axes[0,0].set_title('R² Validation by Test Type')

df.boxplot(column='nrmse_val', by='benchmark_test', ax=axes[0,1])
axes[0,1].set_title('NRMSE Validation by Test Type')

df.boxplot(column='decomp_rssd', by='benchmark_test', ax=axes[1,0])
axes[1,0].set_title('Decomp RSSD by Test Type')

# Val vs Test comparison
axes[1,1].scatter(df['rsq_val'], df['rsq_test'], alpha=0.5)
axes[1,1].plot([0, 1], [0, 1], 'r--')  # Perfect correlation line
axes[1,1].set_xlabel('R² Validation')
axes[1,1].set_ylabel('R² Test')
axes[1,1].set_title('Validation vs Test Performance')

plt.tight_layout()
plt.savefig('benchmark_distribution.png')
```

## Exporting Findings

### Create Summary Report

```python
import pandas as pd

df = pd.read_csv('results.csv')

# Best per test type
summary = []
for test_type in df['benchmark_test'].unique():
    best = df[df['benchmark_test'] == test_type].nsmallest(1, 'nrmse_val').iloc[0]
    summary.append({
        'Test Type': test_type,
        'Best Variant': best['benchmark_variant'],
        'R² Val': f"{best['rsq_val']:.4f}",
        'NRMSE Val': f"{best['nrmse_val']:.4f}",
        'Decomp RSSD': f"{best['decomp_rssd']:.4f}"
    })

summary_df = pd.DataFrame(summary)
summary_df.to_csv('benchmark_summary.csv', index=False)
print("\nSaved: benchmark_summary.csv")
print(summary_df)
```

## Next Steps

1. **Document Findings** - Create summary with recommendations
2. **Validate Recommendations** - Run single model with best config
3. **Deploy to Production** - Apply configuration to production models
4. **Monitor Performance** - Track if benchmark predictions hold
5. **Iterate** - Re-run benchmarks if data/business changes
