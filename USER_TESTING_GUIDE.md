# User Testing Guide - Cost Script Fix

## What Was Fixed

The DEBUG output you provided was extremely helpful! It showed that BigQuery's JSON array was being double-nested by `jq -s`, causing only 1 record to process instead of 22.

### The Problem in Your DEBUG Output

```
=== DEBUG: Raw BigQuery Output ===
[{"service":"Cloud Run",...}, {...}, ...]  ← Correct: 22 objects

=== DEBUG: Parsed BILLING_DATA ===
[[{"service":"Cloud Run",...}, {...}, ...]]  ← WRONG: Wrapped again!

Processing 1 records...  ← Only 1 element in outer array
Unknown - Unknown: $0.00  ← Field extraction failed
```

### The Fix

Added a check to see if the input is already a JSON array. If yes, use it directly. If no (NDJSON), convert it with `jq -s`.

---

## How to Test

### Step 1: Run with DEBUG

```bash
DEBUG=1 ./scripts/get_actual_costs.sh
```

### Step 2: Look for These Changes

**✅ Record Count:**
```
Retrieved 22 record(s)  ← Should be 22, not 1
```

**✅ First Record Structure:**
```
=== First Record Structure ===
{
  "service": "Cloud Run",
  "sku": "Services CPU (Instance-based billing) in europe-west1",
  "total_cost": "82.415475",
  ...
}
```
Should show a SINGLE object `{...}`, not an array `[{...}]`

**✅ Processing:**
```
Processing 22 records...  ← Should be 22, not 1
```

**✅ Output:**
```
Cloud Run - Services CPU (Instance-based billing) in europe-west1: $82.42
Cloud Run - Services Memory (Instance-based billing) in europe-west1: $35.35
Artifact Registry - Artifact Registry Storage: $8.64
[... 19 more items ...]
```
Should show actual service names and costs, not "Unknown - Unknown: $0.00"

**✅ Total:**
```
Total actual cost: $139.77
```
Should show the correct total, not $0.00

---

## Expected Complete Output

```
✓ Successfully retrieved billing data from BigQuery

Retrieved 22 record(s)

=== First Record Structure ===
{
  "service": "Cloud Run",
  "sku": "Services CPU (Instance-based billing) in europe-west1",
  "total_cost": "82.415475",
  "usage_amount": "5418784.378191",
  "usage_unit": "seconds"
}
==============================

✓ Array access works, proceeding with parsing...

===================================
ACTUAL COSTS BY SERVICE
===================================

Parsing billing data...
Processing 22 records...

Cloud Run - Services CPU (Instance-based billing) in europe-west1: $82.42 (5418784.378191 seconds)
Cloud Run - Services Memory (Instance-based billing) in europe-west1: $35.35 (2.2453813910254996E16 byte-seconds)
Artifact Registry - Artifact Registry Storage: $8.64 (2.8893809651706816E17 byte-seconds)
Cloud Run - Jobs CPU in europe-west1: $6.44 (421560.674474 seconds)
Cloud Run - Jobs Memory in europe-west1: $2.86 (1.8105893104418848E15 byte-seconds)
Cloud Storage - Standard Storage Europe Multi-region: $1.61 (2.053598493049989E17 byte-seconds)
Artifact Registry - Artifact Registry Network Internet Egress Europe to Europe: $1.52 (1.8268354214E10 bytes)
Cloud Storage - Standard Storage Belgium: $0.53 (8.802512823967846E16 byte-seconds)
Cloud Run - Cloud Run Network Internet Data Transfer Out Europe to Europe: $0.28 (3.419100469E9 bytes)
Cloud Storage - Regional Standard Class A Operations: $0.10 (29179.0 requests)
Cloud Storage - Multi-Region Standard Class A Operations: $0.01 (1153.0 requests)
Cloud Storage - Regional Standard Class B Operations: $0.01 (85859.0 requests)
Cloud Storage - Multi-Region Standard Class B Operations: $0.01 (20200.0 requests)
Cloud Storage - Coldline Storage Belgium: $0.01 (4.8029713669632E15 byte-seconds)
Cloud Storage - Regional Coldline Class A Operations: $0.01 (357.0 requests)
Cloud Storage - Regional Nearline Class A Operations: $0.00 (110.0 requests)
Cloud Storage - Nearline Storage Belgium: $0.00 (5.8469820384256E13 byte-seconds)
Cloud Storage - Network Data Transfer GCP Replication within Europe: $0.00 (9804654.0 bytes)
Cloud Scheduler - Jobs: $0.00 (90.0 requests)
Cloud Run - Cloud Run Network Internet Data Transfer Out Intercontinental (Excl Oceania, Africa and China): $0.00 (5445.0 bytes)
Cloud Run - Cloud Run GOOGLE-API Data Transfer Out: $0.00 (8.6809689E7 bytes)
Cloud Storage - Download Worldwide Destinations (excluding Asia & Australia): $0.00 (9.61269447E9 bytes)

===================================
TOTAL COST
===================================
Total actual cost: $139.77
```

---

## What to Check

- [ ] 22 records are processed (not 1)
- [ ] All service names are shown (not "Unknown")
- [ ] All costs are shown (not $0.00)
- [ ] Total is $139.77 or similar (not $0.00)
- [ ] No syntax errors
- [ ] Script completes in 5-10 seconds

---

## If It Still Doesn't Work

1. Run with DEBUG again and save the output:
   ```bash
   DEBUG=1 ./scripts/get_actual_costs.sh > debug_output.txt 2>&1
   ```

2. Share the file `debug_output.txt`

3. Specifically check:
   - Does "Parsed BILLING_DATA" still show double brackets `[[...]]`?
   - Does "First Record Structure" show an object `{...}` or an array `[...]`?
   - What does "Processing X records..." say?

---

## Clean Test (Without DEBUG)

Once DEBUG test passes, try clean output:

```bash
./scripts/get_actual_costs.sh
```

Should show clean, formatted output with all 22 cost items and correct total.

---

## Summary

This fix addresses the double-nested array issue revealed by your DEBUG output. The script should now correctly process all 22 billing records and display the actual costs totaling $139.77 (or your current month's total).

**Please test and let us know if it works!**
