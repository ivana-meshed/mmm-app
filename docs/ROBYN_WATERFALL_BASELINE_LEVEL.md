# Robyn Waterfall Chart - Baseline Level Configuration

## Overview

This document explains the `baseline_level` parameter in Robyn's `robyn_onepagers()` function and how it affects the "Response Decomposition Waterfall by Predictor" chart.

## Problem Statement

By default, Robyn's waterfall chart aggregates organic variables (organic_vars) into a generic "baseline" component, making it difficult to see the individual contribution of each organic driver like:
- ORGANIC_CONTENT_SESSIONS_CUSTOM
- ORGANIC_ENG_SESSIONS_CUSTOM
- ORGANIC_TOTAL_CUSTOM

This reduces the interpretability of non-paid marketing drivers in the MMM results.

## Solution

The `baseline_level` parameter in `robyn_onepagers()` controls the aggregation level of variables in the waterfall chart.

### Baseline Level Values

The parameter accepts values from 0 to 5, with increasing levels of aggregation:

| Level | Description | What's Aggregated into Baseline |
|-------|-------------|--------------------------------|
| `0` | No aggregation | Nothing - all variables shown separately |
| `1` | Intercept only | Only the intercept term |
| `2` | Add trend | Intercept + trend |
| `3` | Add Prophet decomposition | Intercept + trend + seasonality/holiday |
| `4` | Add context variables | Intercept + trend + Prophet + context_vars |
| `5` | Add organic variables | Intercept + trend + Prophet + context_vars + organic_vars |

### Our Configuration

We use **`baseline_level = 5`** to ensure that:
- **Organic variables appear as individual bars** in the waterfall chart
- Each organic driver's contribution is visible and measurable
- Non-paid marketing activities can be properly analyzed

### Implementation

The parameter is set in `r/run_all.R` at the `robyn_onepagers()` function calls:

```r
robyn_onepagers(
    InputCollect,
    OutputCollect,
    select_model = m,
    plot_folder = dir_path,
    export = TRUE,
    baseline_level = 5  # Shows individual organic_vars
)
```

## References

- **Robyn Documentation**: [RDocumentation - robyn_outputs](https://www.rdocumentation.org/packages/Robyn/versions/3.12.1/topics/robyn_outputs)
- **GitHub Issues**: 
  - [Issue #754 - Baseline aggregation](https://github.com/facebookexperimental/Robyn/issues/754)
  - [Issue #423 - Accessing onepager plots](https://github.com/facebookexperimental/Robyn/issues/423)
- **Robyn Package**: [facebookexperimental/Robyn](https://github.com/facebookexperimental/Robyn)

## Impact

With `baseline_level = 5`, the waterfall chart will now display:
- ✅ Individual paid media contributions (as before)
- ✅ Individual organic variable contributions (NEW)
- ✅ Individual context variable contributions
- ✅ Prophet decomposition components (trend, seasonality, holidays)
- ✅ Baseline (intercept only)

This provides complete visibility into all modeled drivers and their respective contributions to the target variable.
