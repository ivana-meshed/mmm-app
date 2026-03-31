# JIRA Response — MMM Benchmarking System

> **Format:** This document uses Jira wiki markup. Copy everything from `h2.` onward into a Jira comment or description field.

---

h2. ✅ Implemented: MMM Benchmarking System (PR #182)

The benchmarking system requested in this ticket is now live on the
{{copilot/build-benchmarking-script-mmm-configs}} branch.
Below is a point-by-point response in the same order as the ticket.

----

h3. Problem — addressed

We now have a fully reproducible, queue-based benchmarking pipeline.
Every variant is identified by an explicit config label (adstock × split × time-agg × spend-var × window),
and results are written to a structured CSV so comparisons are objective and repeatable.

----

h3. Idea — what was built

A single command runs the full sweep end-to-end:

{code:bash}
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/<country>/<goal>/<version>/selected_columns.json \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  [--full-run | --extended-run | --production-run] [--all-adstock] [--sequential] [--top-n N]
{code}

The pipeline:
# Parses {{selected_columns.json}} from GCS to read data metadata
# Loads the fleet marketplace benchmark config (90 cartesian variants, geometric default)
# Submits all variants to the Cloud Run Jobs queue
# Processes the queue until all training jobs complete
# Writes an analyzable results table (CSV) with model config + all metrics

Output table columns per variant:

|| Category || Metric ||
| Model fit | {{rsq_train}}, {{rsq_val}}, {{rsq_test}} (R²) |
| Model fit | {{nrmse_train}}, {{nrmse_val}}, {{nrmse_test}} (NRMSE) |
| Decomposition | {{decomp_rssd}} — decomposition quality (lower = better) |
| Media efficiency | ROAS per channel — from {{model_summary.json}} |
| Driver waterfall | Trend/intercept vs media contributions |
| Config | {{adstock}}, {{train_size}}, {{resample_freq}}, {{mapping_strategy}}, {{seasonality_window}} |

Documentation: [USAGE_GUIDE.md] · [ANALYSIS_GUIDE.md] · [README.md]

----

h3. Tests Supported

*(1) Paid media: spend → media var mapping*

Implemented as the {{spend_var_mapping}} dimension — 5 variants in the fleet marketplace config:

|| Variant || {{paid_media_vars}} || Best for ||
| {{spend_to_spend}} | = paid_media_spends (cost columns) | Direct cost attribution |
| {{spend_to_impressions}} | impression columns (all channels) | Ad delivery volume signal |
| {{spend_to_clicks}} | click columns (all channels) | Engagement-weighted signal |
| {{mixed_by_funnel_impressions}} | upper-funnel → impressions; search/pmax/bing → spend | Funnel-aware hybrid |
| {{mixed_by_funnel_clicks}} | upper-funnel → clicks; search/pmax/bing → spend | Funnel-aware hybrid |

Upper funnel: Meta, Google Display, Google YouTube.
Lower funnel: Google Search Brand/Non-brand, Google PMax, Bing Search.

Evaluation: R², NRMSE, {{decomp_rssd}}, allocator stability. See Workflow 1 in [ANALYSIS_GUIDE.md].

----

*(2) Training vs production: train / val / test splits*

Implemented as the {{train_splits}} dimension — 3 variants:

|| Split || {{train_size}} || Train % || Val % || Test % ||
| {{70_90}} | [0.70, 0.90] | 70 | 20 | 10 |
| {{75_90}} | [0.75, 0.90] | 75 | 15 | 10 |
| {{65_80}} | [0.65, 0.80] | 65 | 15 | 20 |

Primary signal: val/test R² gap and {{decomp_rssd}} stability across splits.
See Workflow 2 in [ANALYSIS_GUIDE.md].

----

*(3) Adstock choice*

Implemented as the {{adstock}} dimension — 3 variants (add {{--all-adstock}} to enable all three):

|| Variant || Hyperparameter preset ||
| {{geometric}} | Meshed recommend |
| {{weibull_cdf}} | Meta default |
| {{weibull_pdf}} | Meshed recommend |

Per-channel, per-adstock-type ranges are defined in
{{benchmarks/generic_hyperparameter_ranges_v2.json}} across three presets
({{conservative}} / {{balanced}} / {{exploratory}}).
The fleet marketplace config uses {{balanced}} by default.
See Hyperparameter Presets in [USAGE_GUIDE.md].

----

*(4) Time aggregation*

Implemented as the {{time_aggregation}} dimension — 2 variants:

|| Variant || {{resample_freq}} ||
| {{daily}} | none (no resampling) |
| {{weekly}} | W (ISO weekly) |

See Workflow 3 in [ANALYSIS_GUIDE.md] for daily-vs-weekly interpretation guidance.

----

*(5) Training start/end date + seasonality window*

Implemented as the {{seasonality_window}} dimension. *Default:* only the {{full}} variant (all available history). Add {{--all-windows}} to include {{2y}} and {{3y}} as well.

|| Variant || Lookback || Notes ||
| {{full}} _(default)_ | all available history | no date override — always included |
| {{2y}} | last 104 weeks | add with {{--all-windows}} |
| {{3y}} | last 156 weeks | add with {{--all-windows}} |

Window offsets are resolved to absolute {{start_date}} / {{end_date}} at submission time.
Evaluation: fit metrics + media contributions stability + {{decomp_rssd}} across windows.
See Workflow 5 in [ANALYSIS_GUIDE.md].

----

h3. Combination Strategies

Two modes are available:

|| Mode || Flag || Variants (geometric, full window default) || Formula ||
| Cartesian (default) | _(none)_ | 30 | 1×3×2×5×1 |
| Sequential | {{--sequential}} | 11 | 1+3+2+5 |

With {{--all-windows}} (all 3 window lengths):

|| Mode || Variants ||
| Cartesian | 90 (1×3×2×5×3) |
| Sequential | 14 (1+3+2+5+3) |

With {{--all-adstock}} (all 3 adstock types, full window):

|| Mode || Variants ||
| Cartesian | 90 (3×3×2×5×1) |
| Sequential | 13 (3+3+2+5) |

With {{--all-adstock --all-windows}}:

|| Mode || Variants ||
| Cartesian | 270 (3×3×2×5×3) |
| Sequential | 16 (3+3+2+5+3) |

Sequential order (from most to least fundamental):
# {{adstock}} — core model transform
# {{train_splits}} — evaluation methodology
# {{time_aggregation}} — data granularity
# {{spend_var_mapping}} — media signal strategy
# {{seasonality_window}} — training period (only when {{--all-windows}} is given)

Recommended: run sequential first (~11 variants) to screen dimensions, then cartesian on the top settings.

----

h3. Cost & Time Estimates

Based on the fleet marketplace config with default {{full}} window.

|| Config || Mode || Variants || Time || Cost (USD) ||
| Default geometric, cartesian | Test | 30 | ~0.5 h | ~$5 |
| Default geometric, cartesian | Standard | 30 | ~2 h | ~$25 |
| All adstock, cartesian | Standard | 90 | ~6 h | ~$75 |
| All windows, cartesian | Standard | 90 | ~6 h | ~$75 |
| All adstock + windows, cartesian | Standard | 270 | ~18 h | ~$225 |
| Default geometric, cartesian | Extended | 30 | ~4-5 h | ~$70 |
| All adstock, cartesian | Extended | 90 | ~12-15 h | ~$210 |
| All adstock + windows, cartesian | Extended | 270 | ~40 h | ~$630 |
| Default geometric, cartesian | Production | 30 | ~10-12 h | ~$165 |
| All adstock + windows, cartesian | Production | 270 | ~90-100 h | ~$1,485 |
| Sequential geometric | Standard | 11 | ~45 min | ~$9 |
| Sequential, all windows | Standard | 14 | ~1 h | ~$12 |
| Top-10 | Extended | 10 | ~1.5 h | ~$23 |
| Top-10 | Production | 10 | ~3-4 h | ~$55 |

Full cost table with all flag combinations: [USAGE_GUIDE.md — Cost Estimates].

----

h3. Output — analyzable table + Streamlit page

*(1) Results table*
* GCS: {{gs://mmm-app-output/benchmarks/<benchmark_id>/results_<timestamp>.csv}}
* Local: {{./benchmark_analysis/results_*.csv}} + 6 PNG plots (ranking, adstock comparison, split heatmap, spend-var bar, time-agg, window sweep)

*(2) Streamlit page*
A _View Benchmark Results_ page is available in the app.
Analysis workflows (adstock comparison, split evaluation, spend→var, time aggregation,
comprehensive ranking) are documented in [ANALYSIS_GUIDE.md].

----

h3. Quick Start

{code:bash}
# Sequential exploration — fastest way to screen all dimensions (11 variants, ~$9, ~45 min)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/<country>/<goal>/<version>/selected_columns.json \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --full-run --sequential

# Full cartesian sweep — geometric adstock, full window (30 variants, ~$25, ~2 h)
python scripts/run_full_benchmark.py \
  --path <path> \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --full-run

# All adstock types, full window (90 variants, ~$75, ~6 h)
python scripts/run_full_benchmark.py \
  --path <path> \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --full-run --all-adstock

# All adstock + all windows (270 variants, ~$225, ~18 h)
python scripts/run_full_benchmark.py \
  --path <path> \
  --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
  --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
  --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
  --full-run --all-adstock --all-windows
{code}

See [USAGE_GUIDE.md] for the complete command reference and appendix with every combination enumerated.
