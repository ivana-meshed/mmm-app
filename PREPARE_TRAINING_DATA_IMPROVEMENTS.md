# Prepare Training Data Page Improvements

This document describes the improvements made to the Prepare Training Data page (`app/nav/Prepare_Training_Data.py`) to address the reported issues.

## Issues Fixed

### 1. Column Order and Sorting (✅ Fixed)

**Issue**: Table columns should show Spearman's ρ first, then R², then NMAE, sorted by Spearman's ρ descending.

**Solution**: 
- Reordered all metric tables to display columns in the order: `Spearman's ρ`, `R²`, `NMAE`
- Added automatic sorting by `Spearman's ρ` in descending order (highest first) for:
  - Section 3.2: Paid Media Spends metrics table
  - Section 3.3: Media Response Variables metrics tables
  - Section 4: VIF tables for all variable categories

**Code Changes**:
```python
# Reorder dictionary keys
metrics_data.append({
    "Select": is_selected,
    "Paid Media Spend": spend_col,
    "Spearman's ρ": _safe_float(spearman_rho),  # First
    "R²": _safe_float(r2),                       # Second
    "NMAE": _safe_float(nmae),                   # Third
})

# Add sorting after DataFrame creation
metrics_df = metrics_df.sort_values(
    by="Spearman's ρ", ascending=False, na_position="last"
).reset_index(drop=True)
```

### 2. Automatic Table Refresh - Holistic Approach (✅ Fixed)

**Issue**: Deselecting/interacting with a table does not automatically refresh the table, only at the next action.

**Solution**: 
- Implemented **holistic approach** across all interactive sections for consistent behavior
- **Section 2 (Data Quality Tables)**: Changed from fragment-only reruns to full page reruns
  - Ensures aggregate selection state (`selected_columns_for_training` and `column_categories`) updates immediately
  - Removed `@st.fragment` decorator from `_render_cat_table` function
- **Section 3.2 (Paid Media Spends)**: Added change detection and triggers `st.rerun()` 
  - Ensures dependent sections (3.3 and 4) update immediately
- **Section 4 (VIF Tables)**: Removed fragment scope to enable instant dependent refreshes
  - Removed `@st.fragment` decorator from `_render_vif_table` function
  - Changed from `st.rerun(scope="fragment")` to `st.rerun()`
  - Model Quality Indicators now update automatically without manual refresh button
  - Fixed `StreamlitAPIException` caused by fragment scope calls outside fragment context

**Code Changes**:
```python
# Section 3.2 - Paid Media Spends
selection_changed = False
for _, row in edited_metrics.iterrows():
    spend_col = row["Paid Media Spend"]
    is_selected = bool(row["Select"])
    old_val = st.session_state["paid_spend_selections"].get(spend_col)
    if old_val != is_selected:
        selection_changed = True
    st.session_state["paid_spend_selections"][spend_col] = is_selected

if selection_changed:
    st.rerun()

# Section 2 & 4 - Data Quality and VIF Tables
# Changed from: st.rerun(scope="fragment")
# To: st.rerun()
# Removed @st.fragment decorators for consistent full page reruns
```

**Commits**:
- Initial fix (Step 3.2): commit 4dbfefc
- Step 2 data quality fix: commit 665a760
- Step 4 VIF fragment bug fix: commit c100283
- Removed manual refresh button: commit 3d84f13

### 3. Persistent Sort Order (✅ Fixed)

**Issue**: After table update, sort order should be maintained as Spearman's ρ descending.

**Solution**: 
- Sorting is applied after DataFrame creation, before display
- Sort order persists across reruns as it's applied on every render
- Uses `na_position="last"` to handle NaN values gracefully

### 4. Remove Country from Sidebar (✅ Fixed)

**Issue**: Country selector in sidebar should be removed.

**Solution**: 
- Removed the `st.sidebar.multiselect()` for country selection
- Country filtering still applies in the background (all countries included by default)
- Country selection remains available in Step 1 where it's contextually appropriate

**Code Changes**:
```python
# Removed sidebar multiselect
# Country filter - apply but don't show in sidebar
sel_countries = []
if "COUNTRY" in df.columns:
    country_list = sorted(df["COUNTRY"].dropna().astype(str).unique().tolist())
    # Use all countries by default (no filtering)
    sel_countries = country_list
```

### 5. Goal-Specific Selection Persistence (✅ Fixed)

**Issue**: Prep training data page is linked to a dataset/metadata version, but selections should be different per goal.

**Solution**: 
- Added `goal_specific_selections` dictionary in session state
- Automatically saves current selections when goal changes
- Restores previous selections for a goal when user switches back

**Code Changes**:
```python
# Track selections per goal
st.session_state.setdefault("goal_specific_selections", {})

# Save/restore on goal change
prev_goal = st.session_state.get("selected_goal")
if prev_goal and prev_goal != selected_goal:
    # Save current selections for the previous goal
    st.session_state["goal_specific_selections"][prev_goal] = {
        "paid_spend_selections": st.session_state.get("paid_spend_selections", {}).copy(),
        "selected_paid_spends": st.session_state.get("selected_paid_spends", []).copy(),
        "vif_selections": st.session_state.get("vif_selections", {}).copy(),
    }
    
    # Restore selections for the new goal if they exist
    if selected_goal in st.session_state["goal_specific_selections"]:
        saved = st.session_state["goal_specific_selections"][selected_goal]
        st.session_state["paid_spend_selections"] = saved.get("paid_spend_selections", {}).copy()
        st.session_state["selected_paid_spends"] = saved.get("selected_paid_spends", []).copy()
        st.session_state["vif_selections"] = saved.get("vif_selections", {}).copy()
```

### 6. Timestamp Persistence (✅ Fixed)

**Issue**: Prep training data does not save under a certain timestamp when moved to next page.

**Solution**: 
- Stores timestamp back to session state after generation to ensure consistency
- Adds timestamp to export data dictionary for Run Models page to use
- Uses shared timestamp from Map Data when available

**Code Changes**:
```python
# Store the timestamp back to session state for consistency
if not st.session_state.get("shared_save_timestamp"):
    st.session_state["shared_save_timestamp"] = timestamp

# Add timestamp to export data for Run Models page to use
export_data["timestamp"] = timestamp
```

### 7. Text Update (✅ Fixed)

**Issue**: Info box after "export to training page" should say "move to run model" instead of "move to experiment".

**Solution**: 
- Updated success message text from "Navigate to **Experiment** page" to "Navigate to **Run Models** page"
- Matches the actual page name in the navigation

**Code Changes**:
```python
st.success(
    # ... other parts ...
    "👉 Navigate to **Run Models** page to see prefilled values."
)
```

## Testing Recommendations

### Manual Testing Steps

1. **Column Order and Sorting**:
   - Navigate to Prepare Training Data page
   - Complete Step 1 and Step 2
   - In Step 3.2, verify columns appear as: Select, Paid Media Spend, Spearman's ρ, R², NMAE
   - Verify rows are sorted by Spearman's ρ descending (highest first)
   - Repeat verification for Step 3.3 and Step 4 tables

2. **Automatic Refresh - Holistic Approach**:
   - **Step 2**: Toggle checkboxes in data quality tables
     - Verify aggregate counts at bottom update immediately
     - Verify `selected_columns_for_training` updates in session state
   - **Step 3.2**: Deselect a paid media spend checkbox
     - Verify the table refreshes immediately (no manual refresh needed)
     - Navigate to Step 3.3 and verify dropdown options reflect the change
     - Navigate to Step 4 and verify VIF tables reflect the change
   - **Step 4**: Toggle checkboxes in VIF tables
     - Verify Model Quality Indicators update automatically (Driver Ratio, Collinearity)
     - Verify no manual "Refresh Quality Indicators" button is present
     - Verify changes trigger immediate full page rerun without errors

3. **Persistent Sort Order**:
   - Make changes to selections in any table
   - Verify sort order remains Spearman's ρ descending after changes

4. **Country Sidebar Removal**:
   - Verify no "Country" selector appears in the sidebar
   - Verify all countries' data is included in calculations

5. **Goal-Specific Selections**:
   - Select Goal A in Step 3.1
   - Make specific paid media spend selections
   - Switch to Goal B
   - Make different paid media spend selections
   - Switch back to Goal A
   - Verify Goal A's original selections are restored

6. **Timestamp Persistence**:
   - Complete all steps and click "Export to Training Page"
   - Note the timestamp in the success message
   - Navigate to Run Models page
   - Verify the same timestamp is used or available

7. **Text Update**:
   - Complete export and verify success message says "Navigate to **Run Models** page"

8. **No Fragment Scope Errors**:
   - Interact with all tables in Steps 2, 3.2, and 4
   - Verify no `StreamlitAPIException` errors about fragment scope
   - Verify all sections rerun without console errors

## Impact Analysis

### User Experience Improvements
- ✅ More intuitive column order prioritizing the most important metric (Spearman's ρ)
- ✅ Immediate feedback when making selections (automatic refresh across all sections)
- ✅ **Holistic approach**: Consistent instant dependent refresh behavior in Steps 2, 3.2, and 4
- ✅ Consistent view and sorting across all tables
- ✅ Cleaner sidebar with less clutter
- ✅ Goal-specific workflows with automatic save/restore of selections
- ✅ More reliable data transfer to Run Models page
- ✅ Removed manual refresh buttons - all sections update automatically

### Backward Compatibility
- ✅ All changes are additive or purely UI improvements
- ✅ No breaking changes to data structures or GCS paths
- ✅ Session state changes are backward compatible (uses `.get()` with defaults)
- ✅ Fragment-to-full-page rerun changes are transparent to users

### Performance Considerations
- Minimal performance impact: sorting and change detection are O(n) operations on small datasets
- Goal-specific selection persistence uses shallow copies for efficiency
- Full page reruns instead of fragment reruns: slightly more re-rendering but provides better consistency and user experience

## Related Files

- **Main File**: `app/nav/Prepare_Training_Data.py`
- **Related Pages**: `app/nav/Run_Experiment.py` (receives prefilled data)
- **Test File**: `tests/test_prepare_training_data.py`

## Technical Details

### Fragment to Full Page Rerun Migration

**Problem**: Originally, data quality tables (Step 2) and VIF tables (Step 4) used `@st.fragment` decorators with `st.rerun(scope="fragment")` for partial page reruns. This caused:
- Aggregate selection state not updating immediately in Step 2
- Model Quality Indicators requiring manual refresh button in Step 4
- `StreamlitAPIException` errors when fragment decorators were removed

**Solution**: Migrated all interactive tables to use full page reruns (`st.rerun()`) without fragment decorators:
- **Consistency**: All sections now behave the same way
- **Simplicity**: No need to manage fragment scope boundaries
- **Reliability**: Dependent sections always update automatically

**Affected Functions**:
- `_render_cat_table` (Step 2) - removed `@st.fragment`
- `_render_vif_table` (Step 4) - removed `@st.fragment`

### Dependent Section Updates

| Source Section | Selection Changes | Dependent Sections Updated |
|----------------|-------------------|----------------------------|
| Step 2 (Data Quality) | Checkbox toggles | Aggregate counts, `selected_columns_for_training` |
| Step 3.2 (Paid Spends) | Checkbox toggles | Section 3.3 dropdown options, Step 4 VIF tables |
| Step 4 (VIF Tables) | Checkbox toggles | Model Quality Indicators (Driver Ratio, Collinearity) |

## Future Enhancements (Out of Scope)

1. Add visual indicator (e.g., 🔄 icon) when a rerun is triggered by selection change
2. Add confirmation dialog before switching goals to prevent accidental loss of work
3. Add export history to view previous timestamp exports
4. Add ability to compare goal-specific configurations side-by-side
5. Consider optimizing full page reruns with targeted state updates if performance becomes an issue
