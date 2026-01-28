# Testing Checklist

This document provides a comprehensive testing checklist for the GCS path structure changes.

## Pre-Deployment Testing

### 1. Migration Script Testing

**Prerequisites:**
- [ ] GCP authentication configured (`gcloud auth application-default login`)
- [ ] Access to `mmm-app-output` GCS bucket

**Tests:**
```bash
# 1. Dry run to see what would be migrated
python scripts/migrate_training_data_structure.py

# Expected: Shows list of files to migrate, no actual changes
# Verify: Check output shows correct old→new path mappings

# 2. Check for files with missing goals
# Expected: Script identifies and skips files without selected_goal

# 3. Run actual migration (after verifying dry run)
python scripts/migrate_training_data_structure.py --no-dry-run

# Expected: Files copied to new structure
# Verify: Check GCS console to confirm new paths exist
```

**Verification:**
- [ ] Migration summary shows correct counts
- [ ] No errors during migration
- [ ] New files exist in GCS at correct paths
- [ ] Old files still exist (for rollback)

---

## Post-Deployment Testing

### 2. Prepare Training Data Page - Save Functionality

**Test Case 1: Export New Training Configuration**

Steps:
1. Navigate to Prepare Training Data page
2. Complete all 4 steps:
   - Select data version
   - Ensure good data quality
   - Prepare paid media
   - Reduce noise (VIF analysis)
3. Ensure a goal is selected (e.g., "revenue")
4. Click "Export Configuration" button

**Expected Results:**
- [ ] Success message appears
- [ ] File saved to GCS at path: `training_data/{country}/{goal}/{timestamp}/selected_columns.json`
- [ ] Session state updated with:
  - `just_exported_training_timestamp`
  - `just_exported_training_country`
  - `just_exported_training_goal`

**Verification in GCS:**
```bash
# Check file exists at new path
gsutil ls gs://mmm-app-output/training_data/de/revenue/*/selected_columns.json

# Download and verify content
gsutil cat gs://mmm-app-output/training_data/de/revenue/20240127_*/selected_columns.json
```

**Test Case 2: Export with Different Goals**

Steps:
1. Export configuration with goal "revenue"
2. Change goal to "conversions"
3. Export configuration again

**Expected Results:**
- [ ] Two separate directories created under country:
  - `training_data/de/revenue/{timestamp}/`
  - `training_data/de/conversions/{timestamp}/`
- [ ] Each has its own selected_columns.json

---

### 3. Run Experiment Page - Load Functionality

**Test Case 3: Auto-Selection After Export**

Steps:
1. After exporting from Prepare Training Data (Test Case 1)
2. Navigate to Run Experiment page
3. Scroll to "Training Data Configuration" section

**Expected Results:**
- [ ] Country shows exported country (e.g., "DE")
- [ ] Goal dropdown auto-selects exported goal (e.g., "revenue")
- [ ] Timestamp dropdown auto-selects exported timestamp
- [ ] Success message shows: "✅ Loaded training data config: DE - revenue - 20240127_..."
- [ ] Config preview shows correct data

**Test Case 4: Manual Goal Selection**

Steps:
1. Navigate to Run Experiment page
2. In Training Data Configuration section:
   - Goal dropdown should show available goals
3. Select different goal from dropdown

**Expected Results:**
- [ ] Goal dropdown lists all available goals for the country
- [ ] Timestamp dropdown updates to show timestamps for selected goal
- [ ] Can select timestamp after selecting goal
- [ ] Configuration loads correctly

**Test Case 5: Multiple Goals Per Country**

Prerequisites:
- Export configurations with 2+ different goals for same country

Steps:
1. Navigate to Run Experiment page
2. Check Goal dropdown

**Expected Results:**
- [ ] Goal dropdown shows all goals that have data for the country
- [ ] Selecting different goals shows different timestamps
- [ ] Can load configurations for different goals independently

**Test Case 6: No Data Available**

Steps:
1. Navigate to Run Experiment page
2. Select country with no training data

**Expected Results:**
- [ ] Goal dropdown shows "No goals available" or is empty
- [ ] Timestamp shows "Select goal first"
- [ ] No errors displayed

---

### 4. UI Visual Verification

**Test Case 7: Three-Column Layout**

Steps:
1. Navigate to Run Experiment page
2. Locate Training Data Configuration section

**Expected Results:**
- [ ] Three columns displayed side-by-side:
  - Column 1: Country (readonly text input)
  - Column 2: Goal (dropdown)
  - Column 3: Timestamp (dropdown)
- [ ] Columns are visually aligned
- [ ] Labels are clear ("Country", "Goal", "Timestamp")
- [ ] Layout is responsive (doesn't break on different screen sizes)

**Take Screenshot:**
- [ ] Screenshot of Training Data Configuration section
- [ ] Show all three columns with data selected

---

### 5. Integration Testing

**Test Case 8: Full Workflow**

Steps:
1. Navigate to Prepare Training Data
2. Select country "DE"
3. Complete all steps
4. Select goal "revenue"
5. Export configuration
6. Navigate to Run Experiment
7. Verify auto-selection
8. Configure experiment settings
9. Run experiment

**Expected Results:**
- [ ] Training data config auto-selected correctly
- [ ] Experiment uses correct goal variable
- [ ] Job runs successfully with selected configuration

**Test Case 9: Multiple Exports, Same Country/Goal**

Steps:
1. Export configuration for DE/revenue
2. Make changes to variable selection
3. Export again for DE/revenue (different timestamp)
4. Navigate to Run Experiment

**Expected Results:**
- [ ] Timestamp dropdown shows both timestamps
- [ ] Most recent timestamp is auto-selected
- [ ] Both configurations can be loaded independently
- [ ] Configurations show different variable selections

---

### 5a. Result Pages Goal Filtering Tests

**Test Case 9a: View Results Page - Goal Filter**

Steps:
1. Navigate to View Results page
2. Select an experiment/revision
3. Select country(ies)
4. Observe goal multiselect filter

**Expected Results:**
- [ ] Goal filter appears between Country and Timestamp filters
- [ ] Goal filter shows all unique goals for selected revision/countries
- [ ] Selecting goals filters the displayed results
- [ ] Session state persists goal selections across page navigation
- [ ] Warning shown if goal information unavailable for some runs

**Test Case 9b: View Best Results Page - Goal Filter**

Steps:
1. Navigate to View Best Results page
2. Select an experiment/revision
3. Select countries
4. Observe goal multiselect filter
5. Select specific goal(s)

**Expected Results:**
- [ ] Goal filter appears between Country and Timestamp filters
- [ ] Countries automatically filter to show only those with selected goals
- [ ] Best results compare models with same goal variable
- [ ] Session state persists goal selections

**Test Case 9c: Review Model Stability Page - Goal Filter**

Steps:
1. Navigate to Review Model Stability page
2. Select an experiment/revision
3. Select countries
4. Observe goal multiselect filter
5. Select specific goal(s)
6. View stability analysis

**Expected Results:**
- [ ] Goal filter appears between Country and Timestamp filters
- [ ] Stability analysis uses runs with selected goals
- [ ] Session state persists goal selections
- [ ] Metrics and charts reflect goal-filtered data

**Test Case 9d: Goal Filter Performance**

Steps:
1. Navigate to any result page (View Results, View Best Results, or Review Model Stability)
2. Select revision with 10+ runs across multiple goals
3. Measure time to populate goal filter

**Expected Results:**
- [ ] Goal filter loads in < 3 seconds
- [ ] Goal information cached in session state
- [ ] Subsequent page loads use cached data (faster)
- [ ] No noticeable UI lag

---

### 6. Backward Compatibility Testing (After Migration)

**Test Case 10: Old Format Files (If Not Deleted)**

Steps:
1. Check if old format files still exist in GCS
2. Navigate to Run Experiment page

**Expected Results:**
- [ ] Old format files are NOT shown in dropdowns
- [ ] Only new format files appear
- [ ] No errors from old files being present

**Test Case 11: Migration Verification**

Steps:
1. Compare old and new file contents

**Expected Results:**
- [ ] Content of migrated files matches original
- [ ] Goal from JSON now matches goal in path
- [ ] No data loss during migration

---

### 7. Error Handling

**Test Case 12: Missing Goal in Export**

Steps:
1. Try to export configuration without selecting a goal

**Expected Results:**
- [ ] Export button is disabled OR
- [ ] Warning message appears OR
- [ ] Default goal is used

**Test Case 13: GCS Permission Errors**

Steps:
1. If possible, test with limited GCS permissions

**Expected Results:**
- [ ] Graceful error messages
- [ ] No application crash
- [ ] User informed of permission issue

---

## Regression Testing

### 8. Other Pages Not Affected

**Test Case 14: Other Pages Still Work**

Pages to check:
- [ ] Connect Data - Still works
- [ ] Map Data - Still works
- [ ] Validate Mapping - Still works
- [ ] View Results - Still works (with new goal filter)
- [ ] View Best Results - Still works (with new goal filter)
- [ ] Review Model Stability - Still works (with new goal filter)

**Expected Results:**
- [ ] No errors on any page
- [ ] New goal filtering functionality works correctly
- [ ] Existing functionality unchanged

---

## Performance Testing

### 9. Performance Checks

**Test Case 15: Loading Performance**

Steps:
1. With 10+ training configurations available
2. Navigate to Run Experiment page
3. Measure time to load goal dropdown

**Expected Results:**
- [ ] Goal dropdown loads in < 2 seconds
- [ ] Timestamp dropdown loads in < 2 seconds
- [ ] No noticeable performance degradation

---

## Security Testing

### 10. Security Checks

**Test Case 16: Path Traversal Prevention**

Steps:
1. Check if goal parameter is properly sanitized

**Expected Results:**
- [ ] Goal parameter doesn't allow path traversal (../)
- [ ] Special characters handled properly
- [ ] No injection vulnerabilities

---

## Checklist Summary

Mark each section as complete:

- [ ] 1. Migration Script Testing
- [ ] 2. Prepare Training Data - Save
- [ ] 3. Run Experiment - Load
- [ ] 4. UI Visual Verification
- [ ] 5. Integration Testing
- [ ] 5a. Result Pages Goal Filtering Tests
- [ ] 6. Backward Compatibility
- [ ] 7. Error Handling
- [ ] 8. Regression Testing
- [ ] 9. Performance Testing
- [ ] 10. Security Testing

---

## Sign-Off

Once all tests pass:

- [ ] All tests completed successfully
- [ ] Screenshots captured
- [ ] No critical issues found
- [ ] Ready for production deployment

**Tested by:** _________________  
**Date:** _________________  
**Environment:** [ ] Dev [ ] Staging [ ] Production

**Notes:**
_________________________________________________________________
_________________________________________________________________
_________________________________________________________________
