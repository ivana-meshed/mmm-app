# JIRA Response — MMM Benchmarking System

> **Format:** This document is written in Jira-compatible markdown.
> Copy the content of the relevant section into a Jira comment or description.

---

## Jira Ticket Response

**h2. ✅ Implemented: MMM Benchmarking System (PR \#182)**

The benchmarking system requested in this ticket is now live on the
{{copilot/build-benchmarking-script-mmm-configs}} branch.
Below is a summary of what has been implemented, mapped to each point in the ticket.

---

**h3. What Was Built**

A fully automated, queue-based benchmarking pipeline that:
- Runs a configurable set of MMM configs against a {{selected_columns.json}} dataset
- Writes an analyzable results table (CSV / Parquet) with model config, fit metrics, and decomposition metrics
- Supports cartesian-product _and_ sequential (one-dimension-at-a-time) test strategies
- Is driven by a single command: {{python scripts/run_full_benchmark.py --path <gcs-path>}}

Documentation:
- [USAGE_GUIDE.md] — how to run benchmarks, all CLI flags, cost estimates
- [ANALYSIS_GUIDE.md] — how to analyze and interpret results
- [README.md] — quick-start commands with cost/time estimates

---

**h3. Output Table — Metrics Tracked**

| Category | Metric | Notes |
|----------|--------|-------|
| Model fit | {{rsq_train}}, {{rsq_val}}, {{rsq_test}} | R² on train / val / test splits |
| Model fit | {{nrmse_train}}, {{nrmse_val}}, {{nrmse_test}} | NRMSE (lower = better) |
| Decomposition | {{decomp_rssd}} | Decomposition quality (lower = better) |
| Config | {{adstock}}, {{train_size}}, {{resample_freq}}, {{mapping_strategy}} | Full config captured per variant |
| Seasonality | {{seasonality_window}} | Training window label (full / 2y / 3y) |

ROAS and driver waterfall are captured from {{model_summary.json}} alongside the metrics above.

---

**h3. Tests Supported**

**Paid media: spend → media var mapping**
Implemented via the {{spend_var_mapping}} dimension:
- {{spend_to_spend}} — all channels: spend → spend
- {{spend_to_proxy}} — all channels: spend → sessions / clicks / impressions
- {{mixed_by_funnel}} — upper-funnel → proxy, lower-funnel → spend

The fleet/marketplace benchmark ({{benchmarks/comprehensive_benchmark_fleet_marketplace.json}})
extends this with two additional variants: {{spend_to_impressions}} and
{{spend_to_clicks}}, and separate {{mixed_by_funnel_impressions}} /
{{mixed_by_funnel_clicks}} splits.
See the *Fleet / Mobility Marketplace Dataset* section in [USAGE_GUIDE.md].

Evaluation metrics: R², NRMSE, {{decomp_rssd}}, allocator stability.

---

**Training vs production: train / val / test splits**
Implemented via the {{train_splits}} dimension:
- (0.7, 0.9) — 70% train, 20% val, 10% test
- (0.75, 0.9) — 75% train, 15% val, 10% test
- (0.65, 0.8) — 65% train, 15% val, 20% test

Val/test gap is the primary evaluation signal. See Workflow 2 in [ANALYSIS_GUIDE.md].

---

**Adstock choice**
Implemented via the {{adstock}} dimension (use {{--all-adstock}} to enable all three):
- {{geometric}} — with Meshed recommend preset
- {{weibull_cdf}} — with Meta default preset
- {{weibull_pdf}} — with Meshed recommend preset

Hyperparameter ranges are per-channel and per-adstock-type via
{{benchmarks/generic_hyperparameter_ranges_v2.json}}.
See [USAGE_GUIDE.md] for CLI usage and the presets reference.

---

**Time aggregation**
Implemented via the {{time_aggregation}} dimension:
- {{daily}} ({{resample_freq: none}})
- {{weekly}} ({{resample_freq: W}})

See Workflow 3 in [ANALYSIS_GUIDE.md] for daily vs weekly interpretation guidance.

---

**Training window / seasonality window**
Implemented via the {{seasonality_window}} dimension (use {{--all-windows}} or
{{--windows 2y 3y}} to enable):
- {{full}} — all available history (default for all non-test runs)
- {{2y}} — last 104 weeks
- {{3y}} — last 156 weeks

Window offsets are resolved at runtime from {{end_date}} in {{selected_columns.json}}.
The default for full/extended/production runs is now {{full}} — this ensures results
always carry an explicit window label without changing the training data window.

---

**h3. Combination Strategies**

Two combination modes are available:

|| Mode || Flag || Variants (geometric default) || Use case ||
| Cartesian (default) | _(none)_ | 18 (1×3×2×3) | Exhaustive cross-dimensional sweep |
| Sequential | {{--sequential}} | 9 (1+3+2+3) | Fast initial exploration, one dimension at a time |

Sequential order (from most to least fundamental):
# {{adstock}} — core model transform
# {{train_splits}} — evaluation methodology
# {{time_aggregation}} — data granularity
# {{spend_var_mapping}} — media signal strategy
# {{seasonality_window}} — training period (if windows specified)

Use sequential mode first, then run a cartesian sweep on the top-performing dimension settings.

---

**h3. Cost & Time Estimates**

| Config | Mode | Variants | Time | Cost (USD) |
|--------|------|----------|------|-----------|
| Default (geometric, cartesian) | Standard | 18 | ~1.5-2 h | ~$14 |
| Sequential (geometric) | Standard | 9 | ~40-70 min | ~$7 |
| All adstock, cartesian | Standard | 54 | ~4-6 h | ~$40 |
| Top-10 | Extended | 10 | ~2-3 h | ~$20 |
| Top-10 | Production | 10 | ~5-7 h | ~$50 |

Full cost table: [README.md — Cost & Time Estimates].

*Calculation: 8 vCPU × job_duration_hours × $0.38/vCPU-h + ~10% data/storage overhead*

---

**h3. Output / Streamlit Integration**

Results are written to:
- GCS: {{gs://mmm-app-output/benchmarks/<benchmark_id>/results_<timestamp>.csv}}
- Local: {{./benchmark_analysis/results_*.csv}} + 6 PNG plots

A Streamlit results page (View Benchmark Results) is available in the app.
Analysis workflows (adstock comparison, split evaluation, spend→var, time aggregation,
comprehensive ranking) are documented in [ANALYSIS_GUIDE.md].

---

**h3. Quick Start**

{code:bash}
# Sequential exploration — fastest way to learn (9 variants, ~$7, ~1 hour)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/<country>/<goal>/<version>/selected_columns.json \
  --full-run --sequential

# Full cartesian sweep — geometric adstock (18 variants, ~$14, ~2 hours)
python scripts/run_full_benchmark.py \
  --path <path> --full-run

# All adstock types (54 variants, ~$40, ~4-6 hours)
python scripts/run_full_benchmark.py \
  --path <path> --full-run --all-adstock
{code}

See [USAGE_GUIDE.md] for the complete command reference.
