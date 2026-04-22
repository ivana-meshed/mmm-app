# Benchmark Run Commands Reference

This document covers the full command-line interface for the three benchmark
execution scripts, including canonical production and test run invocations and
a complete flag reference for each script.

---

## Quick start

### Sequential production run (4 manifest configs + TV, full iterations)

```bash
python scripts/run_dk_benchmark_all_configs.py \
    --queue-name default \
    --extra-config dk_final_with_tv_config.json \
    --process-queue
```

### Sequential test / smoke run (same 5 configs, 100 iterations, 1 trial)

```bash
python scripts/run_dk_benchmark_all_configs.py \
    --queue-name default-dev \
    --extra-config dk_final_with_tv_config.json \
    --iterations 100 --trials 1 \
    --process-queue
```

---

## `run_dk_benchmark_all_configs.py`

Orchestrates `run_full_benchmark.py` for every config listed in a manifest
file and optionally drains the resulting queue.

### Flag reference

| Flag | Default | Description |
|------|---------|-------------|
| `--manifest FILENAME` | `dk_context_reduced_manifest_clean.json` | Manifest file (relative to `benchmark_analysis/dk_json_configs_clean/`). Use `dk_context_testing_manifest_clean.json` for the original 6-config test set |
| `--queue-name NAME` | `default-dev` | Cloud Tasks queue name. Use `default` for production |
| `--dry-run` | off | Print GCS uploads and commands without executing |
| `--skip-upload` | off | Skip uploading configs to GCS (use when configs are already uploaded) |
| `--process-queue` | off | After submitting all benchmarks, run `process_queue_simple.py --loop` to drain the queue. Without this flag you must process the queue separately |
| `--only NAME` | all | Run a single config by its short name, e.g. `dk_context_reduced_core` |
| `--iterations N` | 1000 | Override Robyn iterations for every variant (e.g. `100` for a smoke test) |
| `--trials N` | 3 | Override Robyn trials for every variant (e.g. `1` for a smoke test) |
| `--benchmark-id ID` | auto-generated | Shared benchmark ID. Pass an existing ID to add more configs to a previous run |
| `--extra-config FILENAME` | none | Additional config file(s) alongside the manifest. May be repeated: `--extra-config dk_final_with_tv_config.json --extra-config other.json` |

### Examples

```bash
# Production run — all 4 manifest configs, upload, submit and process queue
python scripts/run_dk_benchmark_all_configs.py --queue-name default --process-queue

# All 5 configs (4 manifest + TV) — dev/test run (100 iterations, 1 trial)
python scripts/run_dk_benchmark_all_configs.py \
    --queue-name default-dev \
    --extra-config dk_final_with_tv_config.json \
    --iterations 100 --trials 1 --process-queue

# All 5 configs (4 manifest + TV) — full production run
python scripts/run_dk_benchmark_all_configs.py \
    --queue-name default \
    --extra-config dk_final_with_tv_config.json \
    --process-queue

# Run the original 6-config test set
python scripts/run_dk_benchmark_all_configs.py \
    --queue-name default-dev \
    --manifest dk_context_testing_manifest_clean.json

# Dry-run — print commands without executing
python scripts/run_dk_benchmark_all_configs.py --queue-name default --dry-run

# Skip GCS upload (configs already uploaded)
python scripts/run_dk_benchmark_all_configs.py --queue-name default --skip-upload

# Run a single config
python scripts/run_dk_benchmark_all_configs.py \
    --queue-name default --only dk_context_reduced_core
```

---

## `run_full_benchmark.py`

Lower-level script that generates benchmark variant combinations for a single
`selected_columns.json` config, uploads them to GCS, and submits Cloud Tasks.

### Flag reference

#### Input / config

| Flag | Default | Description |
|------|---------|-------------|
| `--path PATH` | **required** | GCS path to `selected_columns.json`, e.g. `gs://mmm-app-output/training_data/dk/N_UPLOADS_WEB/<timestamp>/selected_columns.json` |
| `--config PATH` | none | Path to an existing benchmark config JSON (e.g. `benchmarks/comprehensive_benchmark_fleet_marketplace.json`). When provided, variant dimensions are taken from the file instead of being generated dynamically. Run-mode flags still override iterations/trials |

#### Run mode (mutually exclusive)

| Flag | Iterations | Trials | Approximate duration |
|------|-----------|--------|----------------------|
| *(default / test)* | 10 | 1 | minutes — ~9 combos sequential |
| `--full-run` | 1000 | 3 | standard production run (~40-70 min sequential) |
| `--extended-run` | 2000 | 5 | ~10–15 hours |
| `--production-run` | 5000 | 5 | ~25–35 hours |

#### Iteration / trial overrides

| Flag | Description |
|------|-------------|
| `--iterations N` | Override iterations regardless of run mode |
| `--trials N` | Override trials regardless of run mode |

#### Combination mode (mutually exclusive)

| Flag | Default | Description |
|------|---------|-------------|
| `--sequential` | **on** | Vary each dimension (adstock, splits, time_agg, spend_var) independently. Fewer total jobs |
| `--cartesian` | off | Full cartesian product of all dimension values — many more jobs |

#### Adstock (mutually exclusive)

| Flag | Description |
|------|-------------|
| *(default)* | `geometric` only |
| `--all-adstock` | All 3 types: `geometric`, `weibull_cdf`, `weibull_pdf` (3× variants) |
| `--adstock TYPE [TYPE...]` | Explicit subset, e.g. `--adstock geometric weibull_cdf` |

#### Window length (mutually exclusive)

| Flag | Description |
|------|-------------|
| *(default)* | `full` (all history) |
| `--all-windows` | All 3 lengths: `full`, `2y`, `3y` (3× variants) |
| `--windows WINDOW [WINDOW...]` | Explicit subset, e.g. `--windows 2y 3y` |

#### Hyperparameter ranges / presets

| Flag | Description |
|------|-------------|
| `--hyperparameter-ranges-config PATH` | JSON file with per-channel HP ranges (e.g. `benchmarks/generic_hyperparameter_ranges_v2.json`) |
| `--channel-type-assignments-config PATH` | JSON mapping variable names → channel types (e.g. `benchmarks/channel_type_assignments.json`). Required when `--hyperparameter-ranges-config` is set |
| `--hyperparameter-preset PRESET` | `conservative` / `balanced` / `exploratory` / `fb` / `meshed`. Defaults to `balanced` when a ranges config is set |
| `--fb` | Shorthand for `--hyperparameter-preset fb` (Robyn/Facebook documentation defaults, channel-type-differentiated) |
| `--meshed` | Shorthand for `--hyperparameter-preset meshed` (Meshed recommended ranges) |
| `--compare-presets` | Run `balanced` + `fb` + `meshed` in one benchmark (3× variants). Requires `--hyperparameter-ranges-config` and `--channel-type-assignments-config` |
| `--compare-all-presets` | Run all 5 presets in one benchmark (5× variants). Requires `--hyperparameter-ranges-config` and `--channel-type-assignments-config` |

#### Job identity

| Flag | Description |
|------|-------------|
| `--benchmark-id ID` | Reuse an existing benchmark ID (results land under `benchmarks/{id}/`) |
| `--variant-prefix STR` | Prefix prepended to every variant name. Required when sharing a benchmark ID across multiple configs |

#### Queue / pipeline control

| Flag | Default | Description |
|------|---------|-------------|
| `--queue-name NAME` | `default-dev` | Cloud Tasks queue. Use `default` for production |
| `--top-n N` | all | Limit submitted combinations to the first N |
| `--skip-queue` | off | Only submit the benchmark; do not process the queue |
| `--skip-analysis` | off | Submit + process queue; skip result analysis |

### Examples

```bash
# Sequential test run (default — 9 combos, 10 iterations, 1 trial, ~8-15 min)
python scripts/run_full_benchmark.py \
    --path gs://mmm-app-output/training_data/dk/N_UPLOADS_WEB/<timestamp>/selected_columns.json \
    --sequential

# Sequential full run (9 combos, 1000 iterations, 3 trials, ~40-70 min)
python scripts/run_full_benchmark.py \
    --path <path> --full-run --sequential

# Standard sequential production run (same as above, production queue)
python scripts/run_full_benchmark.py \
    --path <path> --full-run --sequential --queue-name default

# Cartesian run — all dimension combinations
python scripts/run_full_benchmark.py --path <path> --full-run --cartesian

# All adstock types, cartesian (54 combos)
python scripts/run_full_benchmark.py \
    --path <path> --full-run --cartesian --all-adstock --top-n 54

# Window-length sweep (cartesian across all windows)
python scripts/run_full_benchmark.py \
    --path <path> --full-run --cartesian --all-windows --top-n 54

# Extended run with specific adstock types
python scripts/run_full_benchmark.py \
    --path <path> --extended-run --adstock geometric weibull_cdf

# Fleet marketplace config, geometric, standard run
python scripts/run_full_benchmark.py \
    --path <path> \
    --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \
    --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
    --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \
    --full-run

# Preset comparison: balanced + fb + meshed in one run (3× variants)
python scripts/run_full_benchmark.py \
    --path <path> --full-run \
    --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
    --channel-type-assignments-config benchmarks/channel_type_assignments.json \
    --compare-presets

# Compare all five presets (5× variants)
python scripts/run_full_benchmark.py \
    --path <path> --full-run \
    --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \
    --channel-type-assignments-config benchmarks/channel_type_assignments.json \
    --compare-all-presets
```

---

## `process_queue_simple.py`

Standalone queue drainer — polls the Cloud Tasks queue and dispatches Cloud
Run Job executions until the queue is empty.

### Canonical production invocation

```bash
python scripts/process_queue_simple.py \
    --queue-name default \
    --loop \
    --max-concurrent 3 \
    --training-job-name mmm-app-training
```

### Flag reference

| Flag | Default | Description |
|------|---------|-------------|
| `--queue-name NAME` | `default-dev` (or `$DEFAULT_QUEUE_NAME`) | Queue to drain. Use `default` for production |
| `--bucket NAME` | `mmm-app-output` (or `$GCS_BUCKET`) | GCS bucket for training artifacts |
| `--count N` | `1` | Max jobs to process in a single invocation (ignored when `--loop` is set) |
| `--loop` | off | Process until the queue is empty |
| `--project-id ID` | `$PROJECT_ID` env | GCP project ID |
| `--region REGION` | `europe-west1` | Cloud Run region |
| `--training-job-name NAME` | `mmm-app-dev-training` | Cloud Run Job name. For production: `mmm-app-training` |
| `--max-concurrent N` | `3` | Maximum simultaneous Cloud Run Job executions. Keep at ≤ 3 to stay within the 32 GiB memory budget (main R process ~10 GiB + 3 workers ~6 GiB each ≈ 28 GiB) |
| `--cleanup` | off | Remove old completed/failed entries from the queue |
| `--keep-completed N` | `10` | Number of completed entries to retain when `--cleanup` is used |
