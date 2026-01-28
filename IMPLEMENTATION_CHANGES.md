# GCS Path Structure Migration - Implementation Summary

## Changes Made

This document summarizes the changes made to restructure the training data storage from `data+timestamp` to `data+goal+timestamp` filtering.

### 1. Path Structure Update

**Old structure:**
```
training_data/{country}/{timestamp}/selected_columns.json
```

**New structure:**
```
training_data/{country}/{goal}/{timestamp}/selected_columns.json
```

### 2. Files Modified

#### A. `app/nav/Prepare_Training_Data.py`

**Changes:**
- Updated `TRAINING_DATA_PATH_TEMPLATE` constant to include `{goal}` placeholder (line 103)
- Modified export logic to extract goal from session state (line 2591)
- Updated GCS path formatting to include goal parameter (line 2616)
- Added `just_exported_training_goal` to session state for auto-selection in Run Experiment page (line 2624)

**Impact:**
- New training data exports will be saved in the new path structure
- Goal is now part of the file path, not just stored in the JSON

#### B. `app/nav/Run_Experiment.py`

**Changes:**
- Updated `_list_training_data_versions()` function:
  - Added optional `goal` parameter
  - Changed path parsing to handle 5-part paths (country/goal/timestamp)
  - Updated docstring and comments

- Updated `_load_training_data_json()` function:
  - Added required `goal` parameter
  - Updated blob path construction

- Updated `_list_all_training_data_configs()` function:
  - Added `goal` field to returned dictionaries
  - Changed path parsing to handle 5-part paths
  - Updated display names to include goal: `"{COUNTRY} - {goal} - {timestamp}"`

- Added new `_list_available_goals()` function:
  - Lists all available goals for a given country
  - Extracts goals from GCS path structure

- **"Select Data" expander updated**:
  - **All 4 filters in ONE ROW** using `st.columns(4)`:
    - Column 1: Primary Country (selectbox)
    - Column 2: Goal (selectbox) - NEW! Added goal filtering
    - Column 3: Mapped Data version (selectbox)
    - Column 4: Metadata version (selectbox)
  - Goal dropdown lists available goals from training data for selected country
  - Auto-selects from `just_exported_training_goal` session state

- **"Training Data Configuration" section preserved**:
  - Kept as separate section below Select Data
  - Maintains 3-column layout: Country (readonly) | Goal | Timestamp
  - Allows loading saved configurations from Prepare Training Data
  - Cascading behavior: goal selection loads available timestamps

**Impact:**
- **Goal filtering added to data selection** - Users can filter by goal when selecting data/metadata versions
- **Clear workflow separation** - Data selection vs. Training configuration remain distinct
- Compact single-row layout for all data selection filters
- Backward compatible with new path structure only (requires migration)

#### C. `app/nav/View_Results.py`

**Changes:**
- Added `extract_goal_from_config()` function:
  - Reads goal (dep_var) from `training-configs/{timestamp}/job_config.json`
  - Returns None if config doesn't exist or goal not found

- Added `get_goals_for_runs()` function:
  - Extracts goals for a set of runs
  - Returns mapping of (rev, country, stamp) tuples to goal strings
  - Caches unique timestamps to minimize GCS calls

- **Restructured filter UI to single-row layout**:
  - **All 4 filters in ONE ROW** using st.columns(4)
  - Column 1: Experiment Name (selectbox)
  - Column 2: Country (multiselect)
  - Column 3: Goal (dep_var) (multiselect)
  - Column 4: Timestamp (selectbox, optional)
  - Session state caching (`goals_cache_{rev}_{countries}`) for performance
  - Persistent selection via `view_results_goals_value` session state key
  - Filters displayed runs based on selected goals

**Impact:**
- **Compact single-row filter layout** for better space utilization
- Users can filter model results by goal variable
- Goal information extracted from model configuration files
- Session state persistence maintains selections across page navigation
- Performance optimized through caching

#### D. `app/nav/View_Best_Results.py`

**Changes:**
- Added `extract_goal_from_config()` function (same as View_Results.py)
- Added `get_goals_for_runs()` function (same as View_Results.py)
- **Restructured filter UI to single-row layout**:
  - Revision filter remains separate (primary filter)
  - **Countries and Goal in ONE ROW** using st.columns(2)
  - Column 1: Countries (multiselect)
  - Column 2: Goal (dep_var) (multiselect)
  - Session state caching (`goals_cache_best_{rev}_{countries}`)
  - Persistent selection via `view_best_results_goals_value` session state key
  - Filters countries to show only those with runs matching selected goals

**Impact:**
- **Compact two-column layout** for related filters
- Consistent goal filtering across all result pages
- Users can compare best results filtered by specific goals
- Countries are automatically filtered to those with selected goals

#### E. `app/nav/Review_Model_Stability.py`

**Changes:**
- Added `extract_goal_from_config()` function (same as View_Results.py)
- Added `get_goals_for_runs()` function (same as View_Results.py)
- **Restructured filter UI to single-row layout**:
  - **All 4 filters in ONE ROW** using st.columns(4)
  - Column 1: Experiment Name (selectbox)
  - Column 2: Country (multiselect)
  - Column 3: Goal (dep_var) (multiselect)
  - Column 4: Timestamp (selectbox, optional)
  - Session state caching (`goals_cache_stability_{rev}_{countries}`)
  - Persistent selection via `model_stability_goals_value` session state key
  - Filters runs for stability analysis based on selected goals

**Impact:**
- **Compact single-row filter layout** matching View_Results page
- Goal filtering integrated into stability analysis workflow
- Users can analyze model stability for specific goal variables
- Consistent filtering experience across all analysis pages

### 3. New Files Added

#### A. `scripts/migrate_training_data_structure.py`

A comprehensive migration script that:
- Lists all files in old format (4-part paths)
- Reads each JSON file to extract `selected_goal` field
- Copies files to new path structure (5-part paths)
- Optionally deletes old files after successful migration
- Includes dry-run mode for safe testing
- Provides detailed logging and summary statistics

**Usage:**
```bash
# Dry run (safe, no changes)
python scripts/migrate_training_data_structure.py

# Actual migration
python scripts/migrate_training_data_structure.py --no-dry-run

# Migration + delete old files
python scripts/migrate_training_data_structure.py --no-dry-run --delete-old
```

#### B. `scripts/MIGRATION_README.md`

Comprehensive documentation for the migration process including:
- Overview of the migration
- Step-by-step instructions
- Prerequisites and authentication
- Troubleshooting guide
- Example output

### 4. Session State Changes

**Training Data Export/Import:**
- `just_exported_training_goal`: Stores the goal from the last export to enable auto-selection in Run Experiment page
- `just_exported_training_timestamp`: Still used, now works with goal
- `just_exported_training_country`: Still used, now works with goal

**Result Pages Goal Filtering:**
- `view_results_goals_value`: Stores selected goals in View Results page
- `view_best_results_goals_value`: Stores selected goals in View Best Results page
- `model_stability_goals_value`: Stores selected goals in Review Model Stability page

**Goal Cache Keys (for performance):**
- `goals_cache_{rev}_{countries}`: Caches goal mapping in View Results page
- `goals_cache_best_{rev}_{countries}`: Caches goal mapping in View Best Results page
- `goals_cache_stability_{rev}_{countries}`: Caches goal mapping in Review Model Stability page

### 5. UI Changes

#### Run Experiment Page

**Before:**
```
Country: [DE ▼]
Mapped Data version: [20240115_120000 ▼]
Metadata version: [Universal - 20240115_120000 ▼]
---
Training Data Configuration
Country (readonly): DE
Goal: [revenue ▼]
Timestamp: [20240115_120000 ▼]
```

**After:**
```
Select Data (4-column row)
┌──────────┬──────────┬──────────────┬──────────────┐
│ Country  │   Goal   │  Data Version│   Metadata   │
│  [DE ▼]  │[revenue ▼]│ [20240115 ▼] │ [Universal ▼]│
└──────────┴──────────┴──────────────┴──────────────┘

---
Training Data Configuration (from Prepare Training Data)
┌─────────────┬──────────┬──────────────┐
│  Country    │   Goal   │  Timestamp   │
│     DE      │[revenue ▼]│ [20240115 ▼] │
│ (readonly)  │          │              │
└─────────────┴──────────┴──────────────┘
```

**Key improvements:**
- Added Goal filter to Select Data section
- Two separate sections for different workflows
- All Select Data filters in one compact row

#### View Results Page

**Before:**
```
Experiment: [gmv001 ▼]
Country: [DE, FR ☑]
Goal (dep_var): [revenue ☑]
Timestamp: [20240115_120000 ▼]
```

**After (All in ONE ROW):**
```
┌──────────────┬────────────┬───────────────┬──────────────┐
│  Experiment  │  Country   │     Goal      │  Timestamp   │
│  [gmv001 ▼]  │ [DE, FR ☑] │ [revenue ☑]   │ [20240115 ▼] │
└──────────────┴────────────┴───────────────┴──────────────┘
```

#### Review Model Stability Page

**Before:**
```
Experiment: [gmv001 ▼]
Country: [DE ☑]
Goal (dep_var): [revenue ☑]
Timestamp: [20240115_120000 ▼]
```

**After (All in ONE ROW):**
```
┌──────────────┬──────────┬──────────────┬──────────────┐
│  Experiment  │ Country  │     Goal     │  Timestamp   │
│  [gmv001 ▼]  │ [DE ☑]   │ [revenue ☑]  │ [20240115 ▼] │
└──────────────┴──────────┴──────────────┴──────────────┘
```

#### View Best Results Page

**Before:**
```
Revision: [gmv001 ▼]
Countries: [DE, FR ☑]
Goal (dep_var): [revenue ☑]
```

**After:**
```
Revision: [gmv001 ▼]

┌─────────────┬──────────────┐
│  Countries  │     Goal     │
│ [DE, FR ☑]  │ [revenue ☑]  │
└─────────────┴──────────────┘
```

**Key Improvements:**
- Goal filtering added to Run Experiment's Select Data section
- All Select Data filters in compact single-row layout
- Training Data Configuration preserved as separate section
- Better space utilization across all result pages
- Clearer visual hierarchy
- Consistent UI patterns across all pages

### 6. Backward Compatibility

**IMPORTANT:** The code changes are NOT backward compatible with the old path structure.

- Old format files will NOT be found by the new code
- Migration script MUST be run to convert existing data
- After migration, both old and new format files can coexist temporarily
- Old files can be deleted after verifying migration success

### 7. Migration Requirements

Before deploying these changes to production:

1. ✅ **Run migration script in dry-run mode** to see what will be migrated
2. ✅ **Verify all files have a `selected_goal` field** in their JSON
3. ✅ **Run actual migration** with `--no-dry-run` flag
4. ✅ **Verify migration success** by checking GCS console
5. ✅ **Test the application** with migrated data
6. ⚠️ **Optionally delete old files** after confirming everything works

### 8. Testing Checklist

- [ ] Test saving training data from Prepare Training Data page
- [ ] Verify file is saved to correct path: `training_data/{country}/{goal}/{timestamp}/`
- [ ] Test loading training data in Run Experiment page
- [ ] Verify goal dropdown shows available goals
- [ ] Verify timestamp dropdown shows timestamps for selected goal
- [ ] Verify auto-selection works after exporting from Prepare Training Data
- [ ] Test with multiple goals for the same country
- [ ] Test with multiple countries
- [ ] Verify preview shows correct config data

### 9. Deployment Steps

1. **Dev Environment:**
   - Deploy code changes
   - Run migration script on dev GCS bucket
   - Test thoroughly

2. **Production Environment:**
   - Run migration script in dry-run mode
   - Review output carefully
   - Run actual migration
   - Deploy code changes
   - Monitor for any issues

### 10. Rollback Plan

If issues occur after deployment:

1. **Immediate:** Revert code changes to previous version
2. **Data:** Old format files remain in GCS (if not deleted)
3. **Recovery:** Re-deploy old code version that reads old path structure

Note: DO NOT delete old files until you're confident the migration was successful and the new code is working properly.

## Summary

This implementation successfully restructures the training data storage to include goal in the path hierarchy, enabling filtering by `country + goal + timestamp` as requested. The UI has been updated to display filters side-by-side in a clean 3-column layout. A comprehensive migration script with documentation ensures safe migration of existing data.
