# Script Troubleshooting Guide

**Last Updated:** 2026-02-03  
**Purpose:** Help diagnose and fix issues with cost optimization scripts

---

## Quick Diagnosis

### Is the cleanup script failing?

**Symptom:** "✗ Failed to delete" errors with "manifest is referenced by parent manifests"

**Quick Check:**
```bash
# Run in dry-run mode to see what would be deleted
./scripts/cleanup_artifact_registry.sh

# Look for lines without tags - these should be skipped now
```

**Expected:** All lines should show "(tags: ...)" and no digest-only entries should be attempted.

**If you see digest-only entries being attempted:**
- Pull the latest version of the script
- The script should skip entries with empty tags

---

### Is the training cost script returning $0?

**Symptom:** Shows executions but reports "Could not calculate duration" and $0 cost

**Quick Check:**
```bash
# Check what timestamps look like
gcloud run jobs executions list \
  --job=mmm-app-training \
  --region=europe-west1 \
  --limit=1 \
  --format="value(status.startTime,status.completionTime)"
```

**Expected:** Timestamps in ISO 8601 format (possibly with milliseconds)

**If timestamps have milliseconds (`.123456Z`):**
- The script should handle this now
- Pull the latest version if it doesn't

---

## Detailed Troubleshooting

### Cleanup Script Issues

#### Issue: "manifest is referenced by parent manifests"

**Diagnosis:**
```bash
# List images to see what types exist
gcloud artifacts docker images list \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app \
  --include-tags \
  --format="table(package,version,tags)"
```

**Look for:**
- Entries WITH tags: These can be deleted
- Entries WITHOUT tags (empty): These are digest-only and should be skipped

**Solution:**
The script now filters these automatically. If you still see this error:

1. Check script version:
   ```bash
   grep "Skip if no tags" scripts/cleanup_artifact_registry.sh
   ```
   Should find the line: `# Skip if no tags (digest-only entries)`

2. If not found, pull latest version from the PR

#### Issue: Script deletes some but not all images

**This is expected!** The script:
- Deletes tagged images (with git SHA, `latest`, etc.)
- Skips digest-only entries (child layers)
- Counts only tagged images

**Verify it's working:**
```bash
# Before cleanup
gcloud artifacts repositories describe mmm-repo \
  --location=europe-west1 \
  --format="value(sizeBytes)"

# Run cleanup
DRY_RUN=false KEEP_LAST_N=10 ./scripts/cleanup_artifact_registry.sh

# After cleanup
gcloud artifacts repositories describe mmm-repo \
  --location=europe-west1 \
  --format="value(sizeBytes)"
```

**Expected:** Size should decrease significantly even if some digest entries remain.

#### Issue: "Delete request issued" but times out

**Cause:** Very large manifest lists can take time to delete

**Solution:**
1. Wait for the operation to complete (can take 1-2 minutes per image)
2. If it times out, check if it actually succeeded:
   ```bash
   gcloud artifacts docker images list \
     europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app \
     --include-tags | grep "TAG_THAT_FAILED"
   ```

3. If the tag is gone, deletion succeeded despite timeout

---

### Training Cost Script Issues

#### Issue: "Could not calculate duration (incomplete execution data)"

**Diagnosis:**
```bash
# Get execution details to see actual timestamps
gcloud run jobs executions list \
  --job=mmm-app-training \
  --region=europe-west1 \
  --limit=3 \
  --format="json" | jq -r '.[].status | {start: .startTime, end: .completionTime}'
```

**Common timestamp formats:**
1. `2026-02-03T12:00:00Z` - Standard (works)
2. `2026-02-03T12:00:00.123456Z` - With milliseconds (now handled)
3. `2026-02-03T12:00:00+00:00` - Alternative timezone (now handled)
4. Missing timestamps - Job might not have completed

**Solution:**
If timestamps exist but script still fails:

1. Check script version:
   ```bash
   grep "START_TIME_CLEAN" scripts/get_training_costs.sh
   ```
   Should find timestamp cleaning logic

2. Test date parsing manually:
   ```bash
   # Example timestamp
   TS="2026-02-03T12:00:00.123456Z"
   
   # Clean it
   TS_CLEAN=$(echo "$TS" | sed 's/\.[0-9]*Z$/Z/' | sed 's/+00:00$/Z/')
   echo "$TS_CLEAN"  # Should be: 2026-02-03T12:00:00Z
   
   # Parse it (macOS)
   date -j -f "%Y-%m-%dT%H:%M:%SZ" "$TS_CLEAN" +%s
   ```

#### Issue: Script finds 0 executions

**Diagnosis:**
```bash
# Check if jobs have executed recently
gcloud run jobs executions list \
  --job=mmm-app-training \
  --region=europe-west1 \
  --limit=5
```

**Possible causes:**
1. No jobs have run in last 30 days
   - Solution: Adjust `DAYS_BACK` parameter
   ```bash
   DAYS_BACK=60 ./scripts/get_training_costs.sh
   ```

2. Wrong job name
   - Check actual job name:
   ```bash
   gcloud run jobs list --region=europe-west1
   ```

3. Wrong region
   - Verify region in script matches your deployment

#### Issue: Script shows jobs but $0 cost

**This means timestamps aren't being parsed.**

**Debug steps:**

1. Enable debug output (add to script temporarily):
   ```bash
   # After line 96, add:
   echo "DEBUG: START_TIME=$START_TIME"
   echo "DEBUG: COMPLETION_TIME=$COMPLETION_TIME"
   echo "DEBUG: START_TIME_CLEAN=$START_TIME_CLEAN"
   echo "DEBUG: COMPLETION_TIME_CLEAN=$COMPLETION_TIME_CLEAN"
   echo "DEBUG: START_SEC=$START_SEC"
   echo "DEBUG: END_SEC=$END_SEC"
   ```

2. Run script and check output:
   ```bash
   ./scripts/get_training_costs.sh | grep DEBUG
   ```

3. Look for:
   - Empty timestamps? Job might not have completed
   - Timestamps present but _CLEAN variables empty? sed command failed
   - Timestamps cleaned but _SEC=0? date parsing failed

---

## Common Error Messages

### "unrecognized arguments: --job=mmm-app-training"

**Cause:** Using wrong gcloud command syntax

**Correct syntax:**
```bash
# executions list doesn't take --job
gcloud run jobs executions list \
  --job=mmm-app-training \    # ✓ Correct
  --region=europe-west1

# executions describe doesn't take --job
gcloud run jobs executions describe EXECUTION_NAME \
  --region=europe-west1        # ✓ Correct (no --job)
```

### "date: illegal option -- d"

**Cause:** Using GNU date syntax on macOS

**Solution:** The script should auto-detect OS now. If you still see this:

1. Check detection logic:
   ```bash
   grep "date -v-1d" scripts/get_training_costs.sh
   ```

2. Test manually:
   ```bash
   if date -v-1d > /dev/null 2>&1; then
     echo "BSD date (macOS)"
   else
     echo "GNU date (Linux)"
   fi
   ```

---

## Manual Cleanup Commands

If scripts fail, you can manually clean up:

### Delete specific tagged images

```bash
# Delete a specific tag
gcloud artifacts docker images delete \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app:TAG_NAME \
  --delete-tags --quiet

# Delete by digest (if no parent references)
gcloud artifacts docker images delete \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app@sha256:DIGEST \
  --delete-tags --quiet
```

### List images by age

```bash
# List oldest images
gcloud artifacts docker images list \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app \
  --include-tags \
  --sort-by=CREATE_TIME \
  --format="table(package,tags,CREATE_TIME)" \
  --limit=20
```

### Check repository size

```bash
# Current size
gcloud artifacts repositories describe mmm-repo \
  --location=europe-west1 \
  --format="value(sizeBytes)" | \
  awk '{print $1/1024/1024/1024 " GB"}'
```

---

## Getting Help

If none of these solutions work:

1. **Check script versions:**
   ```bash
   git log --oneline scripts/cleanup_artifact_registry.sh | head -5
   git log --oneline scripts/get_training_costs.sh | head -5
   ```

2. **Verify you have latest fixes:**
   - Cleanup script should have: "Skip if no tags (digest-only entries)"
   - Cost script should have: "START_TIME_CLEAN" and sed commands

3. **Collect debug information:**
   ```bash
   # Script versions
   head -20 scripts/cleanup_artifact_registry.sh
   head -20 scripts/get_training_costs.sh
   
   # Environment info
   date --version 2>&1 || date  # Check date command
   gcloud version
   
   # Sample data
   gcloud artifacts docker images list \
     europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app \
     --include-tags --limit=5 --format="json"
   ```

4. **Review documentation:**
   - `SCRIPT_FIXES_SUMMARY.md` - All fixes explained
   - `scripts/COST_AUTOMATION_README.md` - Script documentation
   - `IMPLEMENTATION_GUIDE_AUTOMATION.md` - Deployment guide

---

## Quick Reference

### Cleanup Script
```bash
# Safe preview (default)
./scripts/cleanup_artifact_registry.sh

# Actually delete (keeps last 10)
DRY_RUN=false KEEP_LAST_N=10 ./scripts/cleanup_artifact_registry.sh

# Keep more versions
DRY_RUN=false KEEP_LAST_N=20 ./scripts/cleanup_artifact_registry.sh
```

### Training Cost Script
```bash
# Default (30 days)
./scripts/get_training_costs.sh

# Different period
DAYS_BACK=7 ./scripts/get_training_costs.sh
DAYS_BACK=90 ./scripts/get_training_costs.sh

# Different project/region
PROJECT_ID=my-project REGION=us-central1 ./scripts/get_training_costs.sh
```

---

**Document Version:** 1.0  
**Last Updated:** 2026-02-03  
**Status:** Current with all known fixes
