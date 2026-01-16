# Quick Start Guide: Multiple Data Sources

## What's New?

You can now connect your data in **3 different ways**:

### 1. Snowflake (Default) ❄️
- Same as before - no changes!
- Use private key authentication
- Preview tables before mapping

### 2. BigQuery (NEW) 🔍
- Connect to Google BigQuery
- Use service account JSON credentials
- Save credentials for future sessions
- Preview tables with fully qualified IDs

### 3. CSV Upload (NEW) 📁
- Upload CSV files directly
- Instant preview and validation
- No database connection needed
- Great for testing and small datasets

## How to Use

### Select Your Data Source
1. Open the **Connect Data** page
2. At the top, you'll see three options:
   ```
   ○ Snowflake    ○ BigQuery    ○ CSV Upload
   ```
3. Click the one you want to use

### Option A: BigQuery

**Prerequisites:**
- Google Cloud project with BigQuery enabled
- Service account with BigQuery permissions
- Service account JSON key file

**Steps:**
1. Click **BigQuery**
2. Enter your **Project ID**
3. Either:
   - Paste the JSON key content, OR
   - Upload the JSON key file
4. (Optional) Check "Save credentials for future sessions"
5. Enter a **Table ID** to preview (format: `project.dataset.table`)
6. Click **Connect**

**Testing with Public Data:**
```
Project ID: your-project-id
Table ID: bigquery-public-data.samples.shakespeare
```

This will let you test the connection without using your own data!

### Option B: CSV Upload

**Requirements:**
- CSV file with your MMM data
- Should include: date column, dependent variable, media columns

**Steps:**
1. Click **CSV Upload**
2. Click **Choose a CSV file**
3. Select your CSV file
4. Preview will show automatically
5. Click **Next → Map Your Data** to continue

**What happens to the file?**
- Stored in browser memory (session state)
- Not uploaded to cloud storage
- Lost when you close the browser
- Great for testing!

## Testing BigQuery

### Quick Test (No Setup Required)

Use Google's public datasets to test:

1. Create or use any GCP project
2. Create a service account and download JSON key
3. Use this table ID: `bigquery-public-data.samples.shakespeare`
4. You'll see Shakespeare text data in the preview!

### Full Testing Instructions

See `docs/BIGQUERY_TESTING.md` for:
- How to create a service account
- Required IAM permissions
- Troubleshooting tips
- Security best practices

## What Works Now?

✅ **Connect Data page**: All three sources fully functional
✅ **Credential saving**: Snowflake and BigQuery support Secret Manager
✅ **Data preview**: Works for all sources
✅ **Navigation**: Can proceed to Map Data after connecting

## What's Next?

🚧 **Map Data page**: Currently only works with Snowflake
- Will be updated to support BigQuery and CSV
- Track progress in the PR

🚧 **Other pages**: Will be updated for multi-source support

## Common Issues

### BigQuery Connection Failed

**Error:** "Permission denied"
- **Fix:** Ensure service account has these roles:
  - BigQuery Data Viewer
  - BigQuery Job User

**Error:** "Table not found"
- **Fix:** Check table ID format: `project.dataset.table`

### CSV Upload Issues

**Error:** "Error reading CSV file"
- **Fix:** Ensure file is valid CSV format
- Check that file isn't too large (< 100MB recommended)
- Verify no special characters in column names

## Tips & Tricks

### Switching Between Sources
- You can switch data sources anytime
- Previous connection stays active until you disconnect
- Only one source can be "active" at a time for navigation

### Saving Credentials
- **Recommended**: Always check "Save credentials for future sessions"
- Credentials stored in Google Secret Manager (encrypted)
- Next time you visit, they'll load automatically
- Can clear them anytime with the "Clear Saved" button

### CSV Best Practices
- Use clean column names (no special characters)
- Include date column in standard format (YYYY-MM-DD)
- Ensure no missing values in critical columns
- Keep file size reasonable (< 100MB)

## Files to Reference

- **`docs/BIGQUERY_TESTING.md`**: Detailed BigQuery setup and testing
- **`docs/UI_CHANGES.md`**: Visual guide to UI changes
- **`docs/MULTIPLE_DATA_SOURCES.md`**: Technical implementation details

## Need Help?

1. Check the error message in the UI
2. Review relevant documentation above
3. Check GCP Console for IAM permissions
4. Verify service account has correct roles

## Example Workflows

### Workflow 1: Quick Test with CSV
```
1. Click "CSV Upload"
2. Upload your test data file
3. Review the preview
4. Click "Next → Map Your Data"
5. (Map Data page coming soon)
```

### Workflow 2: Production with BigQuery
```
1. Create service account (one-time)
2. Download JSON key (one-time)
3. Click "BigQuery"
4. Upload key and check "Save credentials"
5. Connect
6. Next time: automatic credential loading!
7. Just enter project ID and connect
```

### Workflow 3: Keep Using Snowflake
```
1. Click "Snowflake" (or don't click anything - it's default!)
2. Everything works exactly as before
3. No changes needed to your workflow
```

## Summary

✨ **Three ways to connect**
🔒 **Secure credential storage**
📊 **Instant data preview**
🔄 **Easy source switching**
⚡ **Backward compatible**

Enjoy the new flexibility! 🚀
