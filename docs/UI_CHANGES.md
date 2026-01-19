# UI Changes Documentation

## Connect Data Page - New Features

### 1. Data Source Type Selector

At the top of the Connect Data page, users will now see a horizontal radio button selector with three options:

```
Select Data Source Type:
○ Snowflake    ○ BigQuery    ○ CSV Upload
```

**Default Selection**: Snowflake (maintains backward compatibility)

### 2. Snowflake Connection (When "Snowflake" is selected)

The existing Snowflake connection form remains unchanged:
- User, Account, Warehouse fields
- Schema, Role, Database fields
- Private Key upload (PEM format) with text area or file uploader
- "Save this key for future sessions" checkbox
- Preview table name field
- Connect button

**Status Display** (when connected):
- Green checkmark with "Connected" status
- Shows Warehouse, Database, and Schema
- Reconnect, Disconnect, and Clear Saved Key buttons

### 3. BigQuery Connection (When "BigQuery" is selected)

**New Form Fields**:
```
Project ID: [text input field]

Service Account Credentials:
[Large text area for JSON]
Placeholder: {
  "type": "service_account",
  "project_id": "your-project",
  ...
}

…or upload a JSON key file instead
[File uploader button]

☐ Save credentials for future sessions (Recommended)

Table ID for Preview (optional): [text input]
Example: project.dataset.table

[🔌 Connect button]
```

**Status Display** (when connected):
- Green checkmark with "Connected to BigQuery" status
- Shows Project ID
- Disconnect and Clear Saved Credentials buttons

**Success Message**: "✅ Connected to BigQuery project `project-id` successfully."

**Preview**: If a table ID is provided, shows first 20 rows of the table

### 4. CSV Upload (When "CSV Upload" is selected)

**Upload Interface**:
```
Upload a CSV file containing your marketing mix data. The file should include:
• Date column
• Dependent variable (e.g., revenue, conversions)
• Media spend columns
• Media impression/activity columns
• Context variables (optional)
• Organic variables (optional)

[📁 Choose a CSV file button]
```

**Success Display** (after upload):
```
✅ File uploaded successfully! Shape: X rows × Y columns

Data Preview:
[Table showing first 20 rows]

📊 Data Summary (expandable):
  Column Names:           Data Types:
  - column1               [Type dataframe]
  - column2
  - ...
```

**Status Display** (when file loaded):
- Green checkmark with "CSV Data Loaded" status
- Shows filename and shape
- "Upload New File" button

### 5. Output Location

Same as before - expandable section for GCS bucket configuration

### 6. Navigation

Updated to support all data sources:

**Connected State**:
```
[Next → Map Your Data button]  (Active when ANY source is connected)
```

**Disconnected State**:
```
ℹ️ Connect to a data source above (Snowflake, BigQuery, or upload CSV) to enable Next.
```

## Session State Changes

New session state keys added:
- `data_source_type`: String ("Snowflake", "BigQuery", or "CSV Upload")
- `bq_connected`: Boolean
- `bq_client`: BigQuery client instance
- `bq_project_id`: String
- `_bq_credentials_json`: String (credentials, not exposed to UI)
- `_checked_persisted_bq_creds`: Boolean
- `csv_connected`: Boolean
- `csv_data`: DataFrame
- `csv_filename`: String
- `data_connected`: Boolean (unified connection status)

## User Flow Examples

### Flow 1: Switching Between Sources

1. User opens Connect Data page → Default shows Snowflake
2. User clicks "BigQuery" radio button → Page updates to show BigQuery form
3. User clicks "CSV Upload" → Page updates to show CSV uploader
4. User clicks back to "Snowflake" → Returns to Snowflake form

### Flow 2: BigQuery Connection with Saved Credentials

1. User selects "BigQuery"
2. User uploads JSON key file
3. User checks "Save credentials for future sessions"
4. User clicks Connect
5. Success message + credentials saved to Secret Manager
6. On next visit: "✅ Found previously saved credentials" message appears
7. User can connect without re-uploading

### Flow 3: CSV Upload

1. User selects "CSV Upload"
2. User clicks file uploader and selects CSV file
3. File is read and preview shows immediately
4. Data summary shows column names and types
5. "Next → Map Your Data" button becomes active
6. User can click "Upload New File" to replace data

## Visual Design

**Colors**:
- Success messages: Green (✅)
- Info messages: Blue (ℹ️)
- Warning messages: Orange (⚠️)
- Error messages: Red (❌)

**Icons**:
- 🔌 Connect
- ⏏️ Disconnect  
- 🗑️ Clear/Delete
- 🔄 Reconnect/Reload
- 💾 Save
- 📁 Upload
- 📊 Summary
- ➡️ Next

**Layout**:
- Clean, uncluttered design
- Form fields in columns for better space utilization
- Expandable sections for advanced settings
- Clear visual separation between sections with dividers
- Consistent button placement and styling
