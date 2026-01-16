# Multiple Data Source Connection Feature

## Overview

This implementation adds support for two additional data source connection methods to the MMM Trainer application:
1. **BigQuery** - Connect to Google BigQuery databases
2. **CSV Upload** - Upload local CSV files directly

The existing Snowflake connection remains the default option and is fully backward compatible.

## Changes Made

### 1. New Files Created

#### `app/utils/bigquery_connector.py`
Core BigQuery connector utilities providing:
- Service account authentication via Secret Manager
- BigQuery client creation
- Query execution with pandas integration
- Table preview functionality

#### `tests/test_bigquery_connector.py`
Unit tests for BigQuery connector with 6 test cases:
- Loading credentials from Secret Manager
- Creating client with JSON credentials
- Creating client with dict credentials
- Error handling for missing credentials
- Query execution
- Query execution without fetch

#### `docs/BIGQUERY_TESTING.md`
Comprehensive testing instructions including:
- Service account setup steps
- Testing procedures for both web UI and public datasets
- Troubleshooting guide
- Security best practices

#### `docs/UI_CHANGES.md`
Visual documentation of UI changes including:
- Data source selector layout
- Form fields for each data source
- Success/error message formats
- User flow examples

### 2. Modified Files

#### `app/nav/Connect_Data.py`
Major refactoring to support multiple data sources:
- Added data source type radio selector (Snowflake/BigQuery/CSV Upload)
- Implemented BigQuery connection form with credential saving
- Implemented CSV file upload interface
- Updated navigation logic to work with all data sources
- Maintained backward compatibility with existing Snowflake functionality

#### `requirements.txt`
Added new dependencies:
- `google-cloud-bigquery` - BigQuery Python client
- `db-dtypes` - Data type compatibility for BigQuery/pandas

## Features

### Data Source Selector
- Horizontal radio buttons for easy switching between sources
- Default: Snowflake (maintains backward compatibility)
- Session state tracking of selected source type

### Snowflake Connection
- **Unchanged** - All existing functionality preserved
- Private key authentication with PEM file upload or text paste
- Credential persistence via Secret Manager
- Connection status display with reconnect/disconnect options
- Table preview functionality

### BigQuery Connection
- Service account JSON authentication
- Multiple input methods:
  - Paste JSON directly
  - Upload JSON key file
- Credential persistence via Secret Manager (optional)
- Table preview with fully qualified table IDs
- Connection status display
- Clear saved credentials option

### CSV Upload
- Direct file upload via Streamlit file uploader
- Automatic data preview (first 20 rows)
- Data summary with column names and types
- Shape information (rows × columns)
- Upload new file option to replace data
- Data stored in session state for use in downstream pages

## Session State Keys

New session state keys added:
- `data_source_type`: String - Selected data source ("Snowflake", "BigQuery", "CSV Upload")
- `bq_connected`: Boolean - BigQuery connection status
- `bq_client`: BigQuery Client - Active client instance
- `bq_project_id`: String - GCP project ID
- `_bq_credentials_json`: String - Credentials (not exposed to UI)
- `_checked_persisted_bq_creds`: Boolean - Flag for credential check
- `csv_connected`: Boolean - CSV upload status
- `csv_data`: DataFrame - Uploaded CSV data
- `csv_filename`: String - Original filename
- `data_connected`: Boolean - Unified connection status across all sources

## Testing Instructions

### BigQuery Connection Testing

See `docs/BIGQUERY_TESTING.md` for detailed instructions. Quick start:

1. Create a service account in GCP Console
2. Grant BigQuery Data Viewer and Job User roles
3. Download JSON key file
4. In the app:
   - Select "BigQuery" data source
   - Enter project ID
   - Upload or paste JSON credentials
   - (Optional) Check "Save credentials for future sessions"
   - Click Connect

To test with public data:
```
Project ID: your-project-id
Table ID: bigquery-public-data.samples.shakespeare
```

### CSV Upload Testing

1. Select "CSV Upload" data source
2. Click file uploader
3. Select a CSV file with MMM data
4. Verify preview shows correctly
5. Check data summary in expandable section
6. Click "Next → Map Your Data" to proceed

### Example CSV Format

Your CSV should include:
- Date column (e.g., `date`, `DATE`, `Date`)
- Dependent variable (e.g., `revenue`, `conversions`)
- Media spend columns (e.g., `tv_spend`, `facebook_spend`)
- Media activity columns (e.g., `tv_impressions`, `facebook_clicks`)
- Optional: context variables (e.g., `temperature`, `day_of_week`)
- Optional: organic variables (e.g., `organic_traffic`)

## Future Work

### Required for Full Integration

1. **Update Map_Data.py**
   - Add support for CSV data source (read from session state)
   - Add support for BigQuery queries (use `bq_client` from session state)
   - Modify data fetching logic to handle all three sources

2. **Update other pages**
   - Review_Data.py - Support all data sources
   - Prepare_Training_Data.py - Ensure compatibility
   - Any other pages that assume Snowflake connection

3. **Data validation**
   - Add validation for CSV uploads (required columns, data types)
   - Add validation for BigQuery queries
   - Ensure consistent data format across sources

4. **Error handling**
   - More robust error messages
   - Connection retry logic
   - Better handling of credential issues

### Nice-to-Have Enhancements

1. **BigQuery query builder**
   - UI for browsing datasets and tables
   - Query editor with syntax highlighting
   - Query cost estimation

2. **CSV upload improvements**
   - Support for other formats (Excel, Parquet)
   - Data type inference and correction
   - Column mapping wizard

3. **Connection management**
   - Save multiple connections (not just one per type)
   - Connection profiles/presets
   - Connection testing before saving

4. **Documentation**
   - Add sample CSV files to repo
   - Video tutorials for each connection type
   - FAQ section

## Code Quality

All code follows project standards:
- Formatted with Black (line length 80)
- Imports sorted with isort
- Linting with flake8 and pylint
- Type hints where appropriate
- Comprehensive docstrings

## Testing

Unit tests created and passing:
```bash
pytest tests/test_bigquery_connector.py -v
# 6 passed in 1.40s
```

## Backward Compatibility

✅ All existing functionality preserved:
- Default data source is still Snowflake
- Snowflake connection flow unchanged
- Session state keys for Snowflake unchanged
- Map_Data page will continue to work for Snowflake connections

## Security Considerations

1. **Credentials Storage**
   - BigQuery credentials stored in Google Secret Manager (encrypted at rest)
   - Snowflake private keys stored in Google Secret Manager
   - CSV data stored only in session state (not persisted)
   - Service accounts follow principle of least privilege

2. **Best Practices**
   - Never commit credentials to repository
   - Use saved credentials feature to avoid repeated uploads
   - Rotate service account keys regularly
   - Audit access via GCP Console

## Screenshots

See `docs/UI_CHANGES.md` for detailed UI descriptions and user flows.

## Support

For issues or questions:
1. Check `docs/BIGQUERY_TESTING.md` for troubleshooting
2. Review error messages in the UI
3. Check Cloud Logging for detailed error information
4. Verify IAM permissions for service accounts

## Contributors

- Implementation: GitHub Copilot Agent
- Code Review: (Pending)
