# Single Job Run - Feature Documentation

## Overview
This document describes the changes made to the Experiment page (Run_Experiment.py) to support single job runs with GCS-based data selection and enhanced configuration options.

## Changes Summary

### 1. Data Selection Section
**Before:**
- Table name input (DB.SCHEMA.TABLE)
- Custom SQL query text area
- "Test connection & preview 5 rows" button

**After:**
- Country dropdown (fr, de, it, es, nl, uk)
- Data source dropdown (Latest + timestamped versions from GCS)
- "Load selected data" button with automatic 5-row preview
- Data loaded from GCS datasets/<country>/<version>/raw.parquet
- Metadata loaded from GCS metadata/<country>/<version>/mapping.json

### 2. Robyn Configuration Section
**Before:**
- Separate country field
- Separate iterations and trials inputs
- Single "Date tag" field
- Text input for dep_var
- No hyperparameter configuration

**After:**
- Country auto-filled from Data Selection (read-only display)
- Training preset dropdown:
  - **Test run**: 200 iterations, 3 trials
  - **Production**: 2000 iterations, 5 trials
  - **Custom**: 5000 iterations, 10 trials (editable)
- Revision tag auto-prefilled with latest from GCS
- **Training start date** and **Training end date** (replaces Date tag)
  - Used as start_data_date and end_data_date in R script
- **Goal variable** selectbox populated from metadata.json goals (primary group)
- **Goals type** selectbox (revenue/conversion) mapped to dep_var_type
- **Hyperparameter preset** selector (Facebook recommend/Meshed recommend/Custom)
  - Different ranges for geometric vs Weibull adstocks

### 3. Variable Mapping Section
**Before:**
- All fields with hardcoded defaults

**After:**
- Auto-populated from metadata.json mapping
- Still user-editable
- Falls back to defaults if metadata unavailable

### 4. Backend Configuration Changes

#### app_shared.py
```python
# New config fields:
"start_date": "2024-01-01",  # Training window start
"end_date": "2024-12-31",    # Training window end
"dep_var": "REVENUE",         # Goal variable name
"dep_var_type": "revenue",    # revenue or conversion
"hyperparameter_preset": "Meshed recommend",  # Preset choice
```

#### R/run_all.R
```r
# New config parameters used:
start_data_date <- as.Date(cfg$start_date %||% "2024-01-01")
end_data_date <- as.Date(cfg$end_date %||% Sys.Date())
dep_var_from_cfg <- cfg$dep_var %||% "UPLOAD_VALUE"
dep_var_type_from_cfg <- cfg$dep_var_type %||% "revenue"
hyperparameter_preset <- cfg$hyperparameter_preset %||% "Meshed recommend"

# Hyperparameter presets implementation:
get_hyperparameter_ranges(preset, adstock_type, var_name)
  - "Facebook recommend": Standard Robyn ranges
  - "Meshed recommend": Custom ranges optimized for use case
  - "Custom": User-defined (defaults to Meshed ranges)
```

### 5. Hyperparameter Presets

#### Geometric Adstock

**Facebook recommend:**
- Standard variables: alphas [0.5, 3], gammas [0.3, 1], thetas [0, 0.3]
- TV: alphas [0.5, 3], gammas [0.3, 1], thetas [0.3, 0.8]

**Meshed recommend:**
- ORGANIC_TRAFFIC: alphas [0.5, 2.0], gammas [0.3, 0.7], thetas [0.9, 0.99]
- TV_COST: alphas [0.8, 2.2], gammas [0.6, 0.99], thetas [0.7, 0.95]
- PARTNERSHIP_COSTS: alphas [0.65, 2.25], gammas [0.45, 0.875], thetas [0.3, 0.625]
- Other: alphas [1.0, 3.0], gammas [0.6, 0.9], thetas [0.1, 0.4]

#### Weibull Adstock (weibull_cdf, weibull_pdf)

**Facebook recommend:**
- alphas [0.5, 3], shapes [0.0001, 2], scales [0, 0.1]

**Meshed recommend:**
- alphas [0.5, 3], shapes [0.5, 2.5], scales [0.001, 0.15]

## File Structure

### GCS Data Layout
```
gs://<bucket>/
├── datasets/
│   └── <country>/
│       ├── latest/
│       │   └── raw.parquet
│       └── <timestamp>/
│           └── raw.parquet
├── metadata/
│   └── <country>/
│       ├── latest/
│       │   └── mapping.json
│       └── <timestamp>/
│           └── mapping.json
└── robyn/
    └── <revision>/
        └── <country>/
            └── <timestamp>/
                └── [outputs]
```

### Metadata.json Structure
```json
{
  "goals": [
    {
      "var": "REVENUE",
      "group": "primary",
      "type": "revenue"
    },
    {
      "var": "CONVERSIONS",
      "group": "primary",
      "type": "conversion"
    }
  ],
  "mapping": [
    {
      "var": "GA_COST",
      "category": "paid_media_spends",
      "channel": "ga"
    },
    {
      "var": "GA_IMPRESSIONS",
      "category": "paid_media_vars",
      "channel": "ga"
    }
  ]
}
```

## Testing

### Unit Tests
Located in `/tests/test_single_job_config.py`:
- Config structure validation
- Training preset values
- Hyperparameter preset options
- Date range logic
- dep_var_type values

Run tests:
```bash
python3 -m unittest tests.test_single_job_config
```

### CI/CD Integration
Tests run automatically in `.github/workflows/ci-dev.yml`:
```yaml
- name: Run unit tests
  run: |
    python3 -m unittest tests.test_single_job_config -v
```

## Migration Notes

### For Existing Users
1. **Data must be mapped first**: Use page 1 (Map Your Data) to save data to GCS
2. **Metadata required**: Goals and variable mappings come from metadata.json
3. **No more Snowflake connection needed for single runs**: Data loaded directly from GCS
4. **Date range instead of single date**: Specify training window with start/end dates

### Breaking Changes
- Single job runs no longer support direct Snowflake table/SQL input
- Must use GCS-stored data from Map Your Data page
- Date tag replaced with start_date/end_date
- dep_var_type is now required in config

## Future Enhancements
1. Allow custom hyperparameter ranges in UI for "Custom" preset
2. Support multiple goal variables (multi-objective)
3. Add validation for variable names against loaded data columns
4. Persist last-used configuration per country
5. Add quick-select templates for common configurations

## Troubleshooting

### "Please load data first" error
- Click "Load selected data" button before starting training
- Ensure data exists in GCS for selected country

### No data versions available
- Run page 1 (Map Your Data) to save data first
- Check GCS bucket has datasets/<country>/ folder

### Goal variable not appearing
- Ensure metadata.json exists in GCS
- Check goals array has items with group="primary"
- Metadata must match data version selected

### Hyperparameters not applied
- Verify hyperparameter_preset in job config
- Check R logs for hyperparameter construction
- Ensure preset name matches exactly (case-sensitive)
