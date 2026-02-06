# Troubleshooting Cost Tracking Script

This guide helps diagnose and fix issues with `scripts/get_actual_costs.sh`.

## Recent Fix: jq Parsing Error

### Problem
Script failed with:
```
jq: error (at
```

### Solution
The script now includes:
1. **DEBUG mode** for troubleshooting
2. **Robust error handling** with fallback values
3. **Clear error messages** showing what went wrong

---

## Quick Diagnosis

### Step 1: Run with DEBUG mode
```bash
DEBUG=1 ./scripts/get_actual_costs.sh
```

This shows the complete raw BigQuery output, which helps identify:
- Field name mismatches
- Unexpected data structures
- Missing or null fields

### Step 2: Check the output

**If you see BigQuery data but parsing fails:**
```
✓ Successfully retrieved billing data from BigQuery
Warning: Failed to parse billing data
Error output: jq: error (at <stdin>:1): Cannot iterate over null
First 500 chars of raw data:
[{"service":{"description":"Cloud Run"},"sku":{"description":"CPU"}...
```

This means the BigQuery structure is different than expected.

**Common causes:**
1. Nested field structure (e.g., `service.description` vs `service`)
2. Different field names
3. Null values in numeric fields

---

## Understanding BigQuery Output Format

### Expected Structure

The script expects this JSON array structure:
```json
[
  {
    "service": "Cloud Run",
    "sku": "CPU allocation",
    "total_cost": "15.23",
    "usage_amount": "12345",
    "usage_unit": "seconds"
  },
  {
    "service": "Cloud Storage",
    "sku": "Standard Storage",
    "total_cost": "2.10",
    "usage_amount": "105",
    "usage_unit": "gigabyte month"
  }
]
```

### Actual BigQuery Structure

BigQuery may return nested objects:
```json
[
  {
    "service": {
      "description": "Cloud Run"
    },
    "sku": {
      "description": "CPU allocation"  
    },
    "total_cost": "15.23",
    "usage_amount": "12345",
    "usage_unit": "seconds"
  }
]
```

---

## Fixing Field Name Mismatches

If DEBUG mode shows nested structures, update the SQL query in the script:

### Current Query (lines 100-120):
```sql
SELECT
  service.description as service,
  sku.description as sku,
  SUM(cost) as total_cost,
  SUM(usage.amount) as usage_amount,
  usage.unit as usage_unit
FROM `PROJECT_ID.DATASET_ID.TABLE_NAME`
WHERE ...
GROUP BY service, sku, usage_unit
```

The `as service` and `as sku` aliases should flatten the nested structure.

### If Still Nested

You may need to update the jq expression (lines 164-167) to access nested fields:

```bash
# If fields are nested, use dot notation:
jq -r '
    .[] | 
    "\(.service.description // "Unknown") - \(.sku.description // "Unknown"): 
     $\(.total_cost | tonumber // 0 | . * 100 | round / 100) 
     (\(.usage_amount // 0) \(.usage_unit // "units"))"
'
```

---

## Common Issues and Solutions

### Issue 1: Empty Result
```
Warning: BigQuery billing export not configured or no data available
```

**Solutions:**
1. Verify billing export is enabled in GCP Console
2. Wait 24 hours for data to populate
3. Check date range matches available data
4. Verify project ID is correct

### Issue 2: Permission Denied
```
Error: Access Denied: BigQuery BigQuery: Permission denied for this resource.
```

**Solutions:**
1. Run: `gcloud auth application-default login`
2. Ensure account has BigQuery Data Viewer role
3. Check billing account access permissions

### Issue 3: Table Not Found
```
Error: Not found: Table PROJECT_ID:DATASET_ID.TABLE_NAME
```

**Solutions:**
1. Verify dataset name: `mmm_billing` (not `billing_export`)
2. Check billing account number in table name
3. List available tables:
   ```bash
   bq ls mmm_billing
   ```

### Issue 4: Numeric Field Errors
```
jq: error: number (123.45) and string ("abc") cannot be added
```

**Solution:**
The script now handles this with `tonumber // 0` fallbacks. If still occurring, check for non-numeric data in cost fields.

---

## Manual Testing

### Test 1: Check BigQuery Access
```bash
bq query --format=json --use_legacy_sql=false \
  "SELECT COUNT(*) as count FROM \`datawarehouse-422511.mmm_billing.gcp_billing_export_resource_v1_01B2F0_BCBFB7_2051C5\` LIMIT 1"
```

Should return a count > 0.

### Test 2: Check Field Names
```bash
bq query --format=json --use_legacy_sql=false \
  "SELECT service.description, sku.description, cost, usage.amount, usage.unit 
   FROM \`datawarehouse-422511.mmm_billing.gcp_billing_export_resource_v1_01B2F0_BCBFB7_2051C5\` 
   LIMIT 1"
```

Verify field names match script expectations.

### Test 3: Check Date Range
```bash
bq query --format=json --use_legacy_sql=false \
  "SELECT MIN(DATE(_PARTITIONTIME)) as min_date, MAX(DATE(_PARTITIONTIME)) as max_date 
   FROM \`datawarehouse-422511.mmm_billing.gcp_billing_export_resource_v1_01B2F0_BCBFB7_2051C5\`"
```

Ensure data exists for the requested date range.

---

## Environment Variables

### Override Configuration
```bash
# Use different dataset
BILLING_DATASET=custom_billing ./scripts/get_actual_costs.sh

# Use different billing account
BILLING_ACCOUNT_NUM=ABCDEF_123456_789012 ./scripts/get_actual_costs.sh

# Change date range
DAYS_BACK=7 ./scripts/get_actual_costs.sh

# Enable debug output
DEBUG=1 ./scripts/get_actual_costs.sh

# Combine multiple
DEBUG=1 DAYS_BACK=14 ./scripts/get_actual_costs.sh
```

---

## Getting Help

### Information to Provide

When reporting issues, include:

1. **Debug output:**
   ```bash
   DEBUG=1 ./scripts/get_actual_costs.sh > debug_output.txt 2>&1
   ```

2. **Sample BigQuery data:**
   ```bash
   bq query --format=json --use_legacy_sql=false \
     "SELECT * FROM \`datawarehouse-422511.mmm_billing.gcp_billing_export_resource_v1_01B2F0_BCBFB7_2051C5\` LIMIT 1" \
     > sample_data.json
   ```

3. **BigQuery schema:**
   ```bash
   bq show --schema --format=prettyjson \
     mmm_billing.gcp_billing_export_resource_v1_01B2F0_BCBFB7_2051C5 \
     > schema.json
   ```

---

## Recent Changes

### 2026-02-06: Fixed jq parsing error
- Added DEBUG mode
- Improved error handling with safe field access
- Added fallback values for all fields
- Show sample data on parse failures
- Continue to usage statistics even if parsing fails

### 2026-02-06: Updated for actual billing export
- Changed dataset from `billing_export` to `mmm_billing`
- Updated table name to match actual structure
- Added configuration environment variables

---

## Status

✅ Script handles jq parsing errors gracefully
✅ DEBUG mode available for troubleshooting
✅ Continues to usage statistics on parse failures
✅ Clear error messages guide troubleshooting

The script is now resilient to unexpected BigQuery output formats.
