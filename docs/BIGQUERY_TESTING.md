# BigQuery Connection Testing Instructions

This document provides instructions for testing the BigQuery connection feature in the MMM Trainer application.

## Prerequisites

1. **Google Cloud Project**: You need access to a Google Cloud project with BigQuery enabled
2. **Service Account**: Create a service account with appropriate BigQuery permissions
3. **Service Account Key**: Download the JSON key file for the service account

## Setting up a Service Account

### Step 1: Create a Service Account

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Navigate to **IAM & Admin** → **Service Accounts**
3. Click **Create Service Account**
4. Provide a name (e.g., `mmm-app-bigquery-reader`)
5. Click **Create and Continue**

### Step 2: Grant Required Roles

Grant the following roles to the service account:

- **BigQuery Data Viewer** (`roles/bigquery.dataViewer`) - For reading table data
- **BigQuery Job User** (`roles/bigquery.jobUser`) - For running queries
- **BigQuery Read Session User** (`roles/bigquery.readSessionUser`) - Optional, for better performance

Click **Continue** and then **Done**.

### Step 3: Create and Download Key

1. Click on the newly created service account
2. Go to the **Keys** tab
3. Click **Add Key** → **Create new key**
4. Choose **JSON** format
5. Click **Create** - the JSON key file will download automatically
6. **Important**: Keep this file secure and never commit it to version control

## Testing the Connection

### Option 1: Using the Web UI

1. Start the Streamlit application:
   ```bash
   streamlit run app/streamlit_app.py
   ```

2. Navigate to the **Connect Data** page

3. Select **BigQuery** from the data source type radio buttons

4. Enter your **Project ID** (the GCP project ID where your BigQuery data resides)

5. Provide credentials in one of two ways:
   - **Paste JSON**: Copy the contents of your service account JSON key file and paste it into the "Service Account JSON" text area
   - **Upload File**: Click the file uploader and select your JSON key file

6. (Optional) Check the **"💾 Save credentials for future sessions"** checkbox to store credentials securely in Google Secret Manager

7. (Optional) Enter a **Table ID** for preview in the format: `project_id.dataset_id.table_name`
   - Example: `my-project.marketing_data.conversions`

8. Click **🔌 Connect**

### Expected Results

#### Successful Connection

- You should see: "✅ Connected to BigQuery project `your-project-id` successfully."
- If you provided a table ID for preview, you'll see the first 20 rows of the table
- A green status box will appear showing your connection status
- The **Next → Map Your Data** button will become active

#### Failed Connection

Common errors and solutions:

| Error | Cause | Solution |
|-------|-------|----------|
| "Could not load BigQuery credentials" | Invalid JSON format | Verify JSON is valid and complete |
| "Permission denied" | Service account lacks permissions | Grant required BigQuery roles |
| "Dataset/Table not found" | Incorrect table ID | Check table ID format: `project.dataset.table` |
| "Project ID mismatch" | Project ID doesn't match credentials | Ensure project ID matches the one in JSON key |

### Option 2: Testing with Sample Data

If you don't have your own BigQuery data, you can test with Google's public datasets:

1. Use any GCP project ID (even one without billing enabled works for public datasets)
2. Use a service account from any project
3. For the preview table, use a public dataset, for example:
   - `bigquery-public-data.austin_bikeshare.bikeshare_trips`
   - `bigquery-public-data.chicago_taxi_trips.taxi_trips`
   - `bigquery-public-data.samples.shakespeare`

Example:
```
Project ID: your-project-id
Table ID: bigquery-public-data.samples.shakespeare
```

## Saved Credentials Feature

### How It Works

When you check "Save credentials for future sessions":
1. The JSON credentials are stored in Google Secret Manager
2. Secret ID: `bq-credentials-persistent` (configurable via `BQ_PERSISTENT_CREDS_SECRET` env var)
3. On subsequent visits, the app will automatically load the saved credentials
4. You can connect without re-uploading credentials

### Clearing Saved Credentials

To remove saved credentials:
1. Connect to BigQuery (with saved credentials or new ones)
2. Click the **🗑️ Clear Saved Credentials** button
3. The secret will be deleted from Secret Manager

## Troubleshooting

### Issue: Cannot connect even with correct credentials

**Solution**: 
- Check that the BigQuery API is enabled in your GCP project
- Run: `gcloud services enable bigquery.googleapis.com`

### Issue: "No module named 'google.cloud.bigquery'"

**Solution**: 
- Ensure dependencies are installed: `pip install -r requirements.txt`
- The app requires `google-cloud-bigquery` and `db-dtypes` packages

### Issue: Credentials saved but not loading

**Solution**:
- Check Secret Manager permissions for the web service account
- Verify the secret exists: `gcloud secrets describe bq-credentials-persistent`
- The web service account needs `roles/secretmanager.secretAccessor` role

### Issue: Table preview fails but connection succeeds

**Solution**:
- Verify the table ID format: `project.dataset.table` (with dots, not colons)
- Ensure the service account has `bigquery.tables.get` and `bigquery.tables.getData` permissions
- Check that the table exists and is not a view requiring additional permissions

## Security Best Practices

1. **Never commit service account keys**: Add `*.json` to `.gitignore`
2. **Use least privilege**: Only grant necessary BigQuery roles
3. **Rotate keys regularly**: Create new keys periodically and revoke old ones
4. **Use saved credentials feature**: Avoid repeatedly uploading keys
5. **Audit access**: Review service account usage in Cloud Console

## Next Steps

After successful connection:
1. Click **Next → Map Your Data** to proceed to data mapping
2. The Map Data page will need to be updated to support BigQuery queries (see implementation notes)

## Implementation Notes

### For Developers

The BigQuery connector uses:
- `app/utils/bigquery_connector.py` - Core BigQuery utilities
- Session state keys:
  - `bq_connected` - Boolean indicating active connection
  - `bq_client` - BigQuery client instance
  - `bq_project_id` - Project ID string
  - `_bq_credentials_json` - Credentials JSON (not exposed to UI)

### Future Enhancements

- Add support for querying BigQuery in Map_Data page
- Implement BigQuery query caching similar to Snowflake
- Add dataset/table browser UI
- Support for BigQuery views and materialized views
- Query cost estimation before execution
