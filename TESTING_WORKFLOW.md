# How to Test the Cost Optimization Workflow

Quick reference for manually triggering the `cost-optimization.yml` workflow before merging.

## Quick Start (Recommended)

### Via GitHub UI

1. **Go to Actions:** https://github.com/ivana-meshed/mmm-app/actions
2. **Select workflow:** "Cost Optimization - Artifact Registry Cleanup"
3. **Click:** "Run workflow" button
4. **Select branch:** `copilot/implement-cost-reduction-measures`
5. **Set dry_run:** `true` (IMPORTANT for first test!)
6. **Click:** Green "Run workflow" button

### Via GitHub CLI

```bash
# First time: Install and authenticate
brew install gh  # or: sudo apt install gh
gh auth login

# Test with dry run (SAFE - shows what would be deleted)
gh workflow run cost-optimization.yml \
  --ref copilot/implement-cost-reduction-measures \
  -f dry_run=true \
  -f keep_last_n=10

# Check status
gh run list --workflow=cost-optimization.yml --limit 5

# View logs
gh run view --log
```

## Input Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `keep_last_n` | 10 | Number of recent tags to keep per image |
| `dry_run` | false | When true, shows what would be deleted without actually deleting |

## Testing Steps

### 1. Dry Run Test (Always Start Here) ✅

```bash
gh workflow run cost-optimization.yml \
  --ref copilot/implement-cost-reduction-measures \
  -f dry_run=true
```

**What it does:**
- Lists all images that would be deleted
- Shows size that would be freed
- Estimates monthly cost savings
- **Does NOT actually delete anything**

### 2. Review Output

Check the workflow logs for:
- Which images would be deleted
- Which are being kept (most recent N)
- Protected tags (latest, stable) are skipped
- Expected cost savings

### 3. Actual Cleanup (Optional)

Only run if dry run results look correct:

```bash
gh workflow run cost-optimization.yml \
  --ref copilot/implement-cost-reduction-measures \
  -f dry_run=false \
  -f keep_last_n=10
```

### 4. Verify Results

```bash
# List all remaining images
gcloud artifacts docker images list \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo \
  --include-tags

# Check specific image
gcloud artifacts docker images list \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app-web \
  --include-tags
```

## Expected Output

### Dry Run Example

```
==========================================
Artifact Registry Cleanup
==========================================
Project: datawarehouse-422511
Repository: mmm-repo
Region: europe-west1
Keep last: 10 tags
Dry run: true

Processing: europe-west1-docker.pkg.dev/.../mmm-app-web
  Total tags: 25
  Tags to delete: 15
    [DRY RUN] Would delete: sha256:abc123... (size: 1.2GB)
    [DRY RUN] Would delete: sha256:def456... (size: 1.1GB)
    Skipping sha256:xyz789... (has protected tag: latest)
    ...

Processing: europe-west1-docker.pkg.dev/.../mmm-app-training
  Total tags: 18
  Tags to delete: 8
    [DRY RUN] Would delete: sha256:123abc... (size: 2.1GB)
    ...

==========================================
Summary
==========================================
DRY RUN MODE - No images were actually deleted
Total images that would be deleted: 23
Total size that would be freed: 28.5 GB
Estimated monthly savings: $2.85
```

### Actual Cleanup Example

```
Processing: mmm-app-web
  Total tags: 25
  Tags to delete: 15
    Deleting: sha256:abc123...
      ✓ Deleted
    Deleting: sha256:def456...
      ✓ Deleted
    ...

Summary:
Total images deleted: 23
Total size freed: 28.5 GB
Estimated monthly savings: $2.85
```

## Troubleshooting

### Workflow Fails to Start
- Check you're on the correct branch
- Ensure you have write permissions to the repository

### Authentication Error
```
Error: Failed to authenticate to Google Cloud
```

**Solution:** Verify Workload Identity Federation is configured correctly. This is handled automatically in the workflow, but ensure the service account has the required permissions.

### No Images Found
```
No packages found in repository
```

**Possible causes:**
- Repository name or region is incorrect
- No images have been pushed to the registry yet

### Images Not Being Deleted
```
Skipping sha256:xyz... (has protected tag: latest)
```

**This is expected behavior.** Images with protected tags (`latest`, `stable`) are never deleted.

## Why Test Before Merging?

✅ **Verify logic:** Ensure the cleanup logic works as expected  
✅ **Check permissions:** Confirm authentication and GCP permissions  
✅ **Safe testing:** Dry run mode shows impact without deleting  
✅ **Cost validation:** See actual cost savings before production  
✅ **No surprises:** Test on PR branch, not in production  

## Additional Information

For complete documentation, see:
- **Full guide:** [COST_OPTIMIZATION.md](COST_OPTIMIZATION.md) - Section 8
- **Workflow file:** [.github/workflows/cost-optimization.yml](.github/workflows/cost-optimization.yml)

## Quick Commands Reference

```bash
# Test with dry run (safe)
gh workflow run cost-optimization.yml --ref YOUR_BRANCH -f dry_run=true

# Check last 5 runs
gh run list --workflow=cost-optimization.yml --limit 5

# View latest run logs
gh run view --log

# View specific run
gh run view RUN_ID --log

# Re-run failed job
gh run rerun RUN_ID

# List all images in registry
gcloud artifacts docker images list \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo \
  --include-tags
```

---

**Status:** Ready for testing! Start with `dry_run=true` to see what would happen before actually deleting anything.
