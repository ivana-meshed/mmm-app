# MMM App Mapping and Experiment Improvements - Implementation Summary

## Overview
This document summarizes the implementation of improvements to the MMM App's data mapping and experiment configuration workflows, as specified in the problem statement.

## Changes Implemented

### 1. Map Your Data (1_Map_Data.py) - Step 2 Improvements

#### 1.1 Goal Variables Management
**Before:** Goals were configured in horizontal layout with separate primary/secondary columns
**After:** 
- **Vertical Layout**: Primary and secondary goal variables now stack vertically for better space utilization
- **Main Dependent Variable**: Added a "Main" checkbox column in the goals editor to select the main dependent variable directly in Step 2
- **Autocomplete Selection**: The "Variable" field is now a SelectboxColumn instead of free text, providing autocomplete from available source data columns
- **Type Validation**: Added validation to prevent saving goals without specifying a type (revenue/conversion)
- **Aggregation Rules**: Goals automatically get aggregation strategies:
  - Revenue goals: `sum` (enforced automatically)
  - Conversion goals: `mean` (enforced automatically)
  - These are applied when goals are saved and cannot be overridden in the metadata

#### 1.2 Mapping Table Improvements
- **Sorting**: Mapping DataFrame is now automatically sorted by column name (alphabetically)
- **Aggregation Options**: Removed "auto" and "Null" from the aggregation dropdown. Valid options are now: `sum`, `mean`, `max`, `min`, `mode`
- **Filtered Display**: Date field and goal variables are excluded from the mapping_df display to reduce clutter

#### 1.3 Custom Column Naming
**Before:** TOTAL columns had no suffix (e.g., `GA_TOTAL_COST`)
**After:** 
- All TOTAL columns now have `_CUSTOM` suffix (e.g., `GA_TOTAL_COST_CUSTOM`, `ORGANIC_TOTAL_CUSTOM`)
- TOTAL columns are only created when a channel has multiple subchannels
- **Example**: If channel `GA` has subchannels `SUPPLY`, `DEMAND`, and `OTHER`, TOTAL columns like `GA_TOTAL_COST_CUSTOM` will be created
- **Counter-example**: If channel `TV` has only one subchannel or no subchannels, no `TV_TOTAL_COST_CUSTOM` is created

#### 1.4 Metadata Enhancements
**New Fields in mapping.json:**
- `goals`: Now includes `agg_strategy` and `main` boolean fields
- `paid_media_mapping`: Maps each `paid_media_spends` variable to its corresponding `paid_media_vars` based on channel and subchannel
- `data_types`: Date field automatically gets `data_type: "date"`

**Filtering Rules:**
- Variables with empty Category AND empty Channel are excluded from mapping.json
- Variables with at least one non-empty field (category OR channel) are included

#### 1.5 Universal vs Country-Specific Saves
**New Feature:**
- Added checkbox: "Save only for {COUNTRY}"
- Default (unchecked): Saves as universal mapping for all countries
- When checked: Saves for specific country only
- Metadata country field is set to "universal" or country code accordingly

### 2. Run Experiment (4_Run_Experiment.py) - Step 4 Improvements

#### 2.1 Metadata Source Selection
**Before:** Metadata was loaded implicitly from the same source as data
**After:**
- Added "Metadata source" selector above "Data source" selector
- Options include:
  - `Universal - Latest` (default)
  - `Universal - {timestamp}`
  - `{COUNTRY} - Latest`
  - `{COUNTRY} - {timestamp}`
- Default selection: Latest universal mapping
- Users can choose country-specific mappings when needed

#### 2.2 Revision Field Improvements
**Before:** Revision field was pre-filled with latest revision value
**After:**
- Field is now empty by default
- Placeholder text shows: "Latest revision for country: {latest_revision}"
- Validation: User must enter a revision before starting training or saving configuration
- Error displayed if training is attempted without revision

#### 2.3 Training Configuration Save/Load
**New Feature Section:** "Save/Load Training Configuration"

**Save Configuration:**
- Name the configuration (e.g., `baseline_config_v1`)
- Choose single or multi-country save
- For multi-country, select which countries to apply to
- Configuration includes all training parameters:
  - Iterations, trials, train_size
  - Date range
  - Variable selections
  - Goal variable and type
  - Adstock and hyperparameters
  - Resampling options
- Saved to GCS: `training-configs/saved/{country}/{config_name}.json`

**Note on Main Goal Selection:**
- Only one goal should be marked as "main"
- If multiple goals are marked as main, a warning is displayed and the first one is used
- If no goal is marked as main, the first primary goal is used as fallback

**Load Configuration:**
- Lists available configurations for current country
- Select and load configuration
- Configuration parameters can be applied to new data sources

## Metadata JSON Structure

### Example metadata.json
```json
{
  "project_id": "mmm-project",
  "bucket": "mmm-bucket",
  "country": "universal",
  "saved_at": "2025-01-28T12:00:00+00:00",
  "data": {
    "origin": "gcs_latest",
    "timestamp": "latest",
    "date_field": "date",
    "row_count": 1000
  },
  "goals": [
    {
      "var": "REVENUE",
      "group": "primary",
      "type": "revenue",
      "agg_strategy": "sum",
      "main": true
    },
    {
      "var": "CONVERSIONS",
      "group": "secondary",
      "type": "conversion",
      "agg_strategy": "mean",
      "main": false
    }
  ],
  "dep_variable_type": {
    "REVENUE": "revenue",
    "CONVERSIONS": "conversion"
  },
  "autotag_rules": {
    "paid_media_spends": ["_cost", "_spend"],
    "paid_media_vars": ["_impressions", "_clicks"]
  },
  "custom_channels": ["spotify", "podcast"],
  "mapping": {
    "paid_media_spends": ["GA_SUPPLY_COST", "GA_DEMAND_COST"],
    "paid_media_vars": ["GA_SUPPLY_SESSIONS", "GA_DEMAND_SESSIONS"]
  },
  "channels": {
    "GA_SUPPLY_COST": "ga",
    "GA_SUPPLY_SESSIONS": "ga"
  },
  "data_types": {
    "date": "date",
    "GA_SUPPLY_COST": "numeric",
    "GA_SUPPLY_SESSIONS": "numeric"
  },
  "agg_strategies": {
    "GA_SUPPLY_COST": "sum",
    "GA_SUPPLY_SESSIONS": "sum"
  },
  "paid_media_mapping": {
    "GA_SUPPLY_COST": ["GA_SUPPLY_SESSIONS", "GA_SUPPLY_CLICKS"],
    "GA_DEMAND_COST": ["GA_DEMAND_SESSIONS", "GA_DEMAND_CLICKS"]
  },
  "dep_var": "REVENUE"
}
```

## Example Training Configuration JSON
```json
{
  "name": "baseline_config_v1",
  "created_at": "2025-01-28T12:00:00",
  "countries": ["fr", "de", "it"],
  "config": {
    "iterations": 2000,
    "trials": 5,
    "train_size": "0.7,0.9",
    "revision": "r100",
    "start_date": "2024-01-01",
    "end_date": "2024-12-31",
    "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST",
    "paid_media_vars": "GA_SUPPLY_SESSIONS, GA_DEMAND_SESSIONS",
    "context_vars": "IS_WEEKEND",
    "factor_vars": "TV_IS_ON",
    "organic_vars": "ORGANIC_TRAFFIC",
    "dep_var": "REVENUE",
    "dep_var_type": "revenue",
    "date_var": "date",
    "adstock": "geometric",
    "hyperparameter_preset": "Meshed recommend",
    "resample_freq": "none",
    "resample_agg": "sum"
  }
}
```

## CSV Transformation Example

### Before (lines 1-32 of reference CSV):
```csv
var,category,channel,data_type,agg_strategy,custom_tags
GA_SUPPLY_COST,paid_media_spends,ga,numeric,sum,
GA_DEMAND_COST,paid_media_spends,ga,numeric,sum,
GA_OTHER_COST,paid_media_spends,ga,numeric,sum,small
...
NL_DAILY_SESSIONS,organic_vars,organic,numeric,sum,engineering
```

### After (lines 35-81 of reference CSV):
```csv
var,category,channel,data_type,agg_strategy,custom_tags
GA_SUPPLY_COST,paid_media_spends,ga,numeric,sum,
GA_DEMAND_COST,paid_media_spends,ga,numeric,sum,
GA_OTHER_COST,paid_media_spends,ga,numeric,sum,small
GA_TOTAL_COST_CUSTOM,paid_media_spends,ga,numeric,sum
GA_SMALL_COST_CUSTOM,paid_media_spends,ga,numeric,sum,small
...
ORGANIC_NL_DAILY_SESSIONS,organic_vars,organic,numeric,sum,engineering
ORGANIC_ENGINEERING_SESSIONS_CUSTOM,organic_vars,organic,numeric,sum,engineering
ORGANIC_TOTAL_CUSTOM,organic_vars,organic,numeric,sum
```

## Testing

### Test Coverage
Created comprehensive test suite in `tests/test_mapping_metadata.py`:

1. **Metadata Structure Tests** (5 tests)
   - Validates all required fields present
   - Tests goals structure with new fields
   - Validates aggregation rules for goals
   - Tests paid_media_mapping structure
   - Tests universal vs country-specific saves

2. **Data Type Tests** (3 tests)
   - Validates date field has data_type "date"
   - Tests custom column naming (_CUSTOM suffix)
   - Tests aggregation options exclude "auto" and None

3. **Filtering Tests** (1 test)
   - Validates filtering of empty category AND channel

4. **Training Configuration Tests** (2 tests)
   - Tests saved configuration structure
   - Tests multi-country support
   - Validates revision requirement

**Test Scenarios Covered:**
- Metadata JSON structure validation
- Goals aggregation rule enforcement
- Paid media spend-to-var mapping
- Custom column naming conventions
- Empty field filtering
- Universal vs country-specific saves
- Training configuration save/load
- Revision requirement validation

### Test Results
- **New Tests**: 11 tests - All passing ✓
- **Existing Tests**: 8 tests - All passing ✓
- **Total**: 19 tests - 100% pass rate

## Benefits

1. **Improved User Experience**
   - Clearer goal variable configuration with visual hierarchy
   - Autocomplete prevents typos in variable names
   - Validation prevents incomplete configurations

2. **Better Data Organization**
   - Sorted mapping table for easier navigation
   - Filtered display reduces clutter
   - Clear naming conventions with _CUSTOM suffix

3. **Enhanced Flexibility**
   - Universal mappings work across all countries
   - Country-specific overrides when needed
   - Reusable training configurations

4. **Improved Metadata Quality**
   - Explicit paid_media relationships tracked
   - Proper data type annotations
   - Complete aggregation specifications

5. **Efficiency Gains**
   - Save and reuse training configurations
   - Apply configurations to multiple countries
   - Faster experiment setup with saved configs

## Migration Notes

**Version:** January 2025 (v2.0)
**Changes Introduced:** Complete rework of mapping and experiment configuration

### For Existing Metadata
- Old metadata files will continue to work
- New "main" field in goals defaults to false if missing
- New "agg_strategy" field in goals will use defaults if missing
- paid_media_mapping is optional and will be empty if not present
- Metadata created before this version can be loaded but may not have all new fields

### For Users
- First time users will see the universal mapping option by default
- Existing country-specific mappings remain accessible
- Training configurations are a new feature - no migration needed
- Old workflows continue to work without changes

## Files Modified

1. **app/pages/1_Map_Data.py**
   - Updated goals form layout and validation
   - Enhanced mapping table with sorting
   - Improved metadata save logic
   - Added universal/country-specific toggle

2. **app/pages/4_Run_Experiment.py**
   - Added metadata source selector
   - Updated revision field with placeholder
   - Added training configuration save/load
   - Enhanced validation

3. **tests/test_mapping_metadata.py** (NEW)
   - Comprehensive test suite for new features
   - Validates metadata structure
   - Tests configuration management

## Future Enhancements

Potential improvements for future iterations:
1. UI for editing saved training configurations
2. Configuration versioning and history
3. Configuration templates library
4. Bulk configuration operations
5. Configuration comparison tool
6. Automatic configuration recommendations based on data

## Conclusion

All requirements from the problem statement have been successfully implemented and tested. The changes improve the user experience, data quality, and operational efficiency of the MMM App while maintaining backward compatibility with existing workflows.
