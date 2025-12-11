# Robyn Waterfall Chart - Baseline Level Configuration

## Overview

This document explains the `baseline_level` parameter in Robyn's `robyn_onepagers()` function and how it affects the "Response Decomposition Waterfall by Predictor" chart.

## Problem Statement

By default (baseline_level = 0), Robyn's waterfall chart shows all variables individually. However, when baseline_level is increased, more variables are aggregated into a generic "baseline" component, making it difficult to see individual contributions of:
- ORGANIC_CONTENT_SESSIONS_CUSTOM
- ORGANIC_ENG_SESSIONS_CUSTOM
- ORGANIC_TOTAL_CUSTOM

This reduces the interpretability of non-paid marketing drivers in the MMM results.

## Solution

The `baseline_level` parameter in `robyn_onepagers()` controls which variables are aggregated into the baseline component in the waterfall chart.

### Baseline Level Values

The parameter accepts values from 0 to 5. **IMPORTANT**: Higher values aggregate MORE variables into baseline (hiding them), while lower values show more individual bars.

| Level | Description | What's INCLUDED in Baseline (Hidden) |
|-------|-------------|-------------------------------------|
| `0` | No aggregation (default) | Nothing - all variables shown separately |
| `1` | Intercept only | Intercept term |
| `2` | Add trend | Intercept + trend |
| `3` | Add Prophet decomposition | Intercept + trend + Prophet vars (seasonality/holiday) |
| `4` | Add context variables | Intercept + trend + Prophet + context_vars |
| `5` | Add organic variables | Intercept + trend + Prophet + context_vars + **organic_vars** |

### Our Configuration

We use **`baseline_level = 3`** to ensure that:
- **Intercept, trend, and Prophet vars** are aggregated into baseline (cleaner chart)
- **Context variables appear as individual bars** in the waterfall chart
- **Organic variables appear as individual bars** in the waterfall chart (CRITICAL)
- Each organic driver's contribution is visible and measurable
- Non-paid marketing activities can be properly analyzed

**Note**: Using `baseline_level = 5` would HIDE organic_vars by aggregating them into baseline, which is the opposite of what we want!

### Implementation

The parameter is set in `r/run_all.R` at the `robyn_onepagers()` function calls:

```r
robyn_onepagers(
    InputCollect,
    OutputCollect,
    select_model = m,
    plot_folder = dir_path,
    export = TRUE,
    baseline_level = 3  # Shows individual organic_vars and context_vars
)
```

## References

- **Robyn Source Code**: [auxiliary.R - baseline_vars function](https://rdrr.io/cran/Robyn/src/R/auxiliary.R)
- **Robyn Documentation**: [RDocumentation - robyn_outputs](https://www.rdocumentation.org/packages/Robyn/versions/3.12.1/topics/robyn_outputs)
- **GitHub Issues**: 
  - [Issue #754 - Baseline aggregation](https://github.com/facebookexperimental/Robyn/issues/754)
  - [Issue #423 - Accessing onepager plots](https://github.com/facebookexperimental/Robyn/issues/423)
- **Robyn Package**: [facebookexperimental/Robyn](https://github.com/facebookexperimental/Robyn)

## Impact

With `baseline_level = 3`, the waterfall chart will now display:
- ✅ Individual paid media contributions
- ✅ **Individual organic variable contributions** (FIXED - was hidden before)
- ✅ **Individual context variable contributions**
- ✅ Baseline component (intercept + trend + Prophet decomposition)

This provides complete visibility into all modeled marketing drivers (paid, organic, context) and their respective contributions to the target variable.

## Troubleshooting

If organic_vars are still not showing:
1. Verify organic_vars are correctly specified in `robyn_inputs()`
2. Check that organic_vars have non-zero variance in your data
3. Ensure organic_vars are not being filtered out during preprocessing
4. Consider using `baseline_level = 0` for maximum disaggregation (all vars shown separately)
