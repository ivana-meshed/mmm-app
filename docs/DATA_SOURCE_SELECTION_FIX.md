# Data Source Selection UX Improvement

## Problem Statement

The data source selection UI in Map_Data.py had several UX issues:

1. **Selection appeared AFTER country selection** - Users had to select countries before choosing their data source, which was counter-intuitive
2. **Two separate dropdowns with unclear active state** - Both "Select previously loaded data" and "Connect and load new dataset" dropdowns were always visible
3. **Confusing which option was active** - The code tracked which dropdown changed last, but users couldn't easily see which one was being used
4. **Always defaulted to saved datasets** - Even when users wanted to connect to Snowflake/BigQuery/CSV, the system would default to GCS

## Solution

### 1. Restructured UI Flow

The new flow follows a logical progression:

```
1. Choose Data Source Type (Radio Button)
   ├─ Option A: Load previously saved data from GCS
   └─ Option B: Connect and load new dataset

2. Select Countries (Multi-select)

3. Select Specific Data Source
   ├─ If Option A: Show GCS version dropdown
   └─ If Option B: Show connection status and data source selector
```

### 2. Radio Button Selection

Replaced two competing dropdowns with a single radio button choice:
- **"Load previously saved data from GCS"** - For loading existing datasets
- **"Connect and load new dataset"** - For connecting to new data sources

This makes the user's intent clear and prevents confusion.

### 3. Connection Status Display

When "Connect and load new dataset" is selected, the UI now shows:
- **Snowflake** - ✅ Connected / ⚠️ Not connected
- **BigQuery** - ✅ Connected / ⚠️ Not connected
- **CSV Upload** - ✅ Connected / ⚠️ Not connected

With a helpful message: "💡 Connect to data sources in the **Connect Data** page"

### 4. Conditional Dropdown

Based on the radio button selection:
- **GCS mode**: Shows a simple version selector (Latest, timestamps)
- **New source mode**: Shows only connected data sources with a clear warning if none are connected

## Technical Changes

### Modified Files
- `app/nav/Map_Data.py` - Restructured data source selection logic

### Key Code Changes

1. **Radio Button Addition** (Lines 991-996):
```python
data_source_mode = st.radio(
    "How would you like to load data?",
    options=["Load previously saved data from GCS", "Connect and load new dataset"],
    key="data_source_mode",
    help="Choose whether to load already saved data or connect to a new data source"
)
```

2. **Connection Status Checking** (Lines 1103-1119):
```python
sf_connected = st.session_state.get("sf_connected", False)
new_source_options.append(("Snowflake", sf_connected))

bq_connected = (
    st.session_state.get("bq_connected", False)
    and st.session_state.get("bq_client") is not None
)
new_source_options.append(("BigQuery", bq_connected))

csv_connected = (
    st.session_state.get("csv_connected", False)
    and st.session_state.get("csv_data") is not None
)
new_source_options.append(("CSV Upload", csv_connected))
```

3. **Conditional UI Rendering** (Lines 1122-1178):
- Shows different UI elements based on `data_source_mode`
- Displays connection status for each data source
- Filters dropdown to show only connected sources

### Removed Code

Eliminated the complex change-tracking logic:
- Removed `_prev_saved_choice` session state tracking
- Removed `_prev_new_choice` session state tracking
- Removed `_last_changed` flag logic
- Removed competing dropdown selection logic

## Benefits

1. **Clearer User Intent** - Radio button makes the choice explicit
2. **Better Information Architecture** - Data source selection comes first, then country selection
3. **Improved Discoverability** - Connection status is immediately visible
4. **Reduced Confusion** - Only one control for data source type selection
5. **Better Error Prevention** - Users can see if data sources are connected before trying to use them

## Testing Recommendations

1. Test GCS loading with "Latest" and timestamp versions
2. Test Snowflake connection flow:
   - Without connection (should show warning)
   - With connection (should allow selection)
3. Test BigQuery connection flow:
   - Without connection (should show warning)
   - With connection (should allow selection)
4. Test CSV upload flow:
   - Without upload (should show warning)
   - With upload (should allow selection)
5. Test switching between radio button options
6. Test multi-country selection with each data source
7. Verify that data loads correctly for each source type

## Backward Compatibility

The changes maintain backward compatibility:
- Existing session state variables (`source_choice`, `selected_countries`) are preserved
- Data loading logic remains unchanged
- Only the UI selection mechanism was improved
