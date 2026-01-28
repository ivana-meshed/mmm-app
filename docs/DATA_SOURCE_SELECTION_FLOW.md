# Data Source Selection Flow - Before vs After

## Before (Old Implementation)

```
┌─────────────────────────────────────────────────────────────┐
│  1. Select Dataset                                          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  📊 Choose the data you want to analyze             │  │
│  │                                                       │  │
│  │  [Country Selector]                                  │  │
│  │    ↓                                                 │  │
│  │  1.1 Select previously loaded data:                 │  │
│  │    [Dropdown: Latest, 20250107, 20241231...]        │  │
│  │                                                       │  │
│  │  1.2 Alternatively: connect and load new dataset    │  │
│  │    [Dropdown: Snowflake, BigQuery, CSV Upload]      │  │
│  │                                                       │  │
│  │  ❌ PROBLEM: Both dropdowns always visible          │  │
│  │  ❌ PROBLEM: Unclear which is active                │  │
│  │  ❌ PROBLEM: Change tracking is confusing           │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Problems with Old Implementation

1. **Country selection comes first** - Users must choose countries before data source
2. **Two competing dropdowns** - Both "saved data" and "new dataset" visible simultaneously
3. **Hidden state management** - Complex logic tracks which dropdown changed last
4. **No connection feedback** - Users don't know if Snowflake/BigQuery/CSV is connected
5. **Confusing defaults** - Always defaults to "Latest" even when user wants Snowflake

---

## After (New Implementation)

```
┌─────────────────────────────────────────────────────────────┐
│  1. Select Dataset                                          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  📊 Choose the data you want to analyze             │  │
│  │                                                       │  │
│  │  1.1 Choose Data Source Type                         │  │
│  │    ◉ Load previously saved data from GCS            │  │
│  │    ○ Connect and load new dataset                   │  │
│  │                                                       │  │
│  │  ────────────────────────────────────────────────   │  │
│  │                                                       │  │
│  │  1.2 Select Countries                                │  │
│  │    [Multi-select: de, fr, it, es...]                │  │
│  │                                                       │  │
│  │  ────────────────────────────────────────────────   │  │
│  │                                                       │  │
│  │  1.3 Select Data Source                              │  │
│  │                                                       │  │
│  │  IF "Load from GCS":                                 │  │
│  │    📦 Loading from previously saved datasets         │  │
│  │    [Dropdown: Latest, 20250107, 20241231...]        │  │
│  │                                                       │  │
│  │  IF "Connect and load new":                          │  │
│  │    🔌 Connect to a new data source                   │  │
│  │    **Snowflake**           ✅ Connected              │  │
│  │    **BigQuery**            ⚠️ Not connected          │  │
│  │    **CSV Upload**          ✅ Connected              │  │
│  │    💡 Connect to data sources in Connect Data page  │  │
│  │    [Dropdown: Snowflake, CSV Upload]                │  │
│  │                                                       │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Benefits of New Implementation

✅ **Clear user intent** - Single radio button choice
✅ **Logical flow** - Data source type → Countries → Specific source
✅ **Connection status visible** - Users see what's connected before selecting
✅ **Conditional UI** - Only relevant options shown based on radio selection
✅ **Better error prevention** - Warning if no data sources connected
✅ **Simpler state management** - No complex change tracking needed

---

## UI Flow Comparison

### Old Flow
```
User Action                    System Behavior
──────────────────────────────────────────────────────────────
1. Select countries            Loads country list
2. Change "saved data"         Tracks as "_last_changed = saved"
   dropdown                    Updates "_prev_saved_choice"
3. Change "new dataset"        Tracks as "_last_changed = new"
   dropdown                    Updates "_prev_new_choice"
4. Click Load                  Determines active dropdown by comparing
                              current vs previous values
                              ❌ Confusing logic
```

### New Flow
```
User Action                    System Behavior
──────────────────────────────────────────────────────────────
1. Select radio button         Shows relevant UI section
   "Load from GCS"             - GCS version dropdown
   OR                          OR
   "Connect new"               - Connection status + source dropdown

2. Select countries            Loads country list

3. Select specific source      Clear what will be loaded
   (GCS version OR             (Based on radio selection)
    Snowflake/BQ/CSV)

4. Click Load                  Loads from selected source
                              ✅ Clear and predictable
```

---

## Code Structure Comparison

### Old Code Structure
```python
# Two separate dropdowns always rendered
saved_data_choice = st.selectbox(...)     # Dropdown 1
new_source_choice = st.selectbox(...)     # Dropdown 2

# Complex change tracking
if saved_data_choice != prev_saved:
    source_choice = saved_data_choice
    st.session_state["_last_changed"] = "saved"
elif new_source_choice != prev_new:
    source_choice = new_source_choice
    st.session_state["_last_changed"] = "new"
else:
    # Fallback logic...
```

### New Code Structure
```python
# Single radio button for mode selection
data_source_mode = st.radio(
    "How would you like to load data?",
    options=[
        "Load previously saved data from GCS",
        "Connect and load new dataset"
    ]
)

# Conditional rendering based on mode
if data_source_mode == "Load previously saved data from GCS":
    # Show GCS version selector
    source_choice = st.selectbox("Select GCS version:", ...)
else:
    # Show connection status and new source selector
    # Display: Snowflake ✅/⚠️, BigQuery ✅/⚠️, CSV ✅/⚠️
    source_choice = st.selectbox("Select data source:", ...)
```

---

## User Experience Impact

### Scenario 1: User wants to load from GCS
**Before:**
1. Select countries
2. See two dropdowns
3. Change "saved data" dropdown to "Latest"
4. Hope the system uses "Latest" and not Snowflake
5. Click Load

**After:**
1. Select radio: "Load previously saved data from GCS"
2. See GCS version dropdown clearly labeled
3. Select countries
4. Select version: "Latest"
5. Click Load ✅ Clear what will happen

### Scenario 2: User wants to connect to Snowflake
**Before:**
1. Select countries
2. See two dropdowns
3. Change "new dataset" dropdown to "Snowflake"
4. Don't know if Snowflake is connected
5. Click Load
6. ❌ Error: Not connected (surprise!)

**After:**
1. Select radio: "Connect and load new dataset"
2. See connection status: Snowflake ⚠️ Not connected
3. 💡 See message: "Connect in Connect Data page"
4. Go to Connect Data page and connect
5. Come back, see: Snowflake ✅ Connected
6. Select countries
7. Select Snowflake
8. Click Load ✅ Success!

---

## Summary

The new implementation provides:
- **Clearer user intent** through radio button selection
- **Better information architecture** with logical flow
- **Improved discoverability** of connection status
- **Reduced confusion** with single control for mode
- **Better error prevention** through status visibility
- **Simpler code** without complex state tracking

This fix addresses all the issues mentioned in the problem statement and provides a significantly better user experience.
