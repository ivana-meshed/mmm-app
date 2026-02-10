# Debugging the Cost Tracking Script

## Current Issue

The script successfully retrieves data from BigQuery but doesn't display anything:

```
✓ Successfully retrieved billing data from BigQuery

===================================
ACTUAL COSTS BY SERVICE
===================================
```

Nothing appears after this point.

## What We've Done

Added verbose output to help diagnose the issue. The script now shows:

1. **Record count** - How many records were retrieved
2. **First record structure** - The actual data format from BigQuery
3. **Enhanced error messages** - Clear guidance on what went wrong

## Next Steps

### Step 1: Run the Script Again

```bash
./scripts/get_actual_costs.sh
```

### Step 2: Look for "First Record Structure"

The script will now show something like:

```
=== First Record Structure ===
{
  "field1": "value1",
  "field2": "value2",
  ...
}
==============================
```

### Step 3: Share the Output

Please share the complete output, especially:
- The record count line
- The entire "First Record Structure" section
- Any error or warning messages

This will tell us:
- What field names BigQuery is actually returning
- Whether the data structure matches our jq expressions
- Why the parsing might be failing

## Possible Issues We're Looking For

### Issue 1: Field Names Don't Match

**Query says:**
```sql
SELECT
  service.description as service,
  sku.description as sku,
  ...
```

**But BigQuery might return:**
```json
{
  "service.description": "Cloud Run",  // Not aliased correctly
  "sku.description": "CPU"             // Not aliased correctly
}
```

### Issue 2: Nested Structure

**Expected (flat):**
```json
{
  "service": "Cloud Run",
  "sku": "CPU"
}
```

**Actual (nested):**
```json
{
  "service": {
    "description": "Cloud Run"
  },
  "sku": {
    "description": "CPU"
  }
}
```

### Issue 3: Different Field Names

BigQuery might use different names:
```json
{
  "f0_": "Cloud Run",    // Auto-generated names
  "f1_": "CPU"
}
```

### Issue 4: Empty Result Despite Non-Empty Data

The jq check `.[0]` might fail if:
- Data is a single object, not an array
- Data is empty array `[]`
- Data structure is completely different

## Quick Debug Commands

### See Full Raw Output
```bash
DEBUG=1 ./scripts/get_actual_costs.sh
```

### Test BigQuery Query Directly
```bash
bq query --format=json --use_legacy_sql=false "
SELECT
  service.description as service,
  sku.description as sku,
  SUM(cost) as total_cost,
  SUM(usage.amount) as usage_amount,
  usage.unit as usage_unit
FROM \`datawarehouse-422511.mmm_billing.gcp_billing_export_resource_v1_01B2F0_BCBFB7_2051C5\`
WHERE
  DATE(_PARTITIONTIME) >= '2026-01-07'
  AND DATE(_PARTITIONTIME) <= '2026-02-10'
  AND project.id = 'datawarehouse-422511'
GROUP BY service, sku, usage_unit
LIMIT 1
"
```

This will show exactly what BigQuery returns.

### Check Table Schema
```bash
bq show --schema --format=prettyjson \
  datawarehouse-422511:mmm_billing.gcp_billing_export_resource_v1_01B2F0_BCBFB7_2051C5
```

This shows all available fields.

## Once We Know the Structure

After you share the "First Record Structure" output, we can:

1. **Fix the SQL query** if field aliases aren't working
2. **Update jq expressions** to match actual field names
3. **Handle nested structures** if data is nested
4. **Adjust parsing logic** for any other format issues

## Example Fix

If the output shows:
```json
{
  "f0_": "Cloud Run",
  "f1_": "CPU allocation",
  "f2_": "15.23"
}
```

We'd update the jq expression from:
```bash
jq -r '.[] | "\(.service) - \(.sku): $\(.total_cost)"'
```

To:
```bash
jq -r '.[] | "\(.f0_) - \(.f1_): $\(.f2_)"'
```

## Summary

✅ Script now shows what data it's getting
✅ "First Record Structure" reveals actual format
✅ We can quickly fix jq expressions once we see the structure

**Please run the script and share the output!**
