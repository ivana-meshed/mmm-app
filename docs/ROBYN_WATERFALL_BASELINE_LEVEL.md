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

We use **`baseline_level = 0`** to ensure that:
- **ALL variables appear as individual bars** in the waterfall chart
- **Intercept** shown individually
- **Trend** shown individually
- **Prophet variables** (seasonality, holiday, weekday) shown individually
- **Context variables** shown individually
- **Organic variables** shown individually (CRITICAL)
- Each driver's contribution is fully visible and measurable
- Maximum visibility into all model components

**Note**: This provides the most detailed view. If the chart becomes too cluttered, you can increase baseline_level to aggregate less important components (e.g., baseline_level = 2 to group intercept + trend).

### Implementation

The parameter is set in `r/run_all.R` at the `robyn_onepagers()` function calls:

```r
robyn_onepagers(
    InputCollect,
    OutputCollect,
    select_model = m,
    plot_folder = dir_path,
    export = TRUE,
    baseline_level = 0  # Shows ALL variables individually
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

With `baseline_level = 0`, the waterfall chart will display:
- ✅ **Individual intercept** component
- ✅ **Individual trend** component
- ✅ **Individual Prophet decomposition components** (season, holiday, weekday)
- ✅ **Individual context variable contributions**
- ✅ **Individual organic variable contributions** (FIXED - was missing before)
- ✅ **Individual paid media contributions**

This provides COMPLETE visibility into ALL modeled components and drivers, with maximum interpretability.

**Important Note**: These changes only take effect for NEW training runs. Existing onepager images that were generated before this fix will still show the old visualization. You need to run a new training job to see the updated waterfall chart with individual organic_vars displayed.

## Troubleshooting

If organic_vars are still not showing after running a new training job:
1. Verify organic_vars are correctly specified in `robyn_inputs()` in the training config
2. Check that organic_vars have non-zero variance in your data
3. Ensure organic_vars are not being filtered out during preprocessing
4. Review the R logs to confirm the variables are being included in the model
5. Check that the onepager image you're viewing is from the NEW training run (not a cached old image)
