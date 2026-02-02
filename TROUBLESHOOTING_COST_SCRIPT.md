# Troubleshooting: Script Not Found Error

## Problem

When running `./scripts/collect_cost_data.sh`, you get:
```
zsh: no such file or directory: ./scripts/collect_cost_data.sh
```

## Root Cause

The `collect_cost_data.sh` script was created on the `copilot/update-cost-estimate-docs` branch. If you're on a different branch or haven't pulled the latest changes, the file won't exist in your local checkout.

## Solution

### Step 1: Check Your Current Branch

```bash
git branch
```

### Step 2: Pull the Latest Changes

If you're already on the `copilot/update-cost-estimate-docs` branch:
```bash
git pull origin copilot/update-cost-estimate-docs
```

If you're on a different branch, switch to the correct branch:
```bash
git fetch origin
git checkout copilot/update-cost-estimate-docs
git pull origin copilot/update-cost-estimate-docs
```

### Step 3: Verify the File Exists

```bash
ls -la scripts/collect_cost_data.sh
```

You should see:
```
-rwxr-xr-x  1 user  staff  8298 Feb  2 12:40 scripts/collect_cost_data.sh
```

### Step 4: Run the Script

```bash
./scripts/collect_cost_data.sh
```

## Alternative: Run Directly from Repository Root

If you're in a different directory, use the full path:

```bash
bash /path/to/mmm-app/scripts/collect_cost_data.sh
```

Or navigate to the repository root first:

```bash
cd /path/to/mmm-app
./scripts/collect_cost_data.sh
```

## Verify You Have the Latest Files

Check if these files exist (all created in the cost analysis update):

```bash
ls -1 docs/COST_ANALYSIS_DEV_PROD.md \
      COST_ANALYSIS_QUICK_START.md \
      DATA_COLLECTION_INSTRUCTIONS.md \
      "Cost estimate - Dev and Prod.csv" \
      scripts/collect_cost_data.sh
```

All five files should exist. If any are missing, you need to pull the latest changes.

## Still Having Issues?

### Check Git Remote

```bash
git remote -v
```

Should show:
```
origin  https://github.com/ivana-meshed/mmm-app.git (fetch)
origin  https://github.com/ivana-meshed/mmm-app.git (push)
```

### Check Available Branches

```bash
git branch -r | grep cost
```

Should show:
```
origin/copilot/update-cost-estimate-docs
```

### Force Pull (Last Resort)

If you have local changes you want to discard:

```bash
git fetch origin
git checkout copilot/update-cost-estimate-docs
git reset --hard origin/copilot/update-cost-estimate-docs
```

⚠️ **Warning:** This will discard any local uncommitted changes on this branch.

## Alternative: Download the Script Directly

If git isn't working, you can download the script directly from GitHub:

```bash
curl -O https://raw.githubusercontent.com/ivana-meshed/mmm-app/copilot/update-cost-estimate-docs/scripts/collect_cost_data.sh
chmod +x collect_cost_data.sh
./collect_cost_data.sh
```

## Running the Script Requires GCP Credentials

Once the file exists, make sure you're authenticated with GCP:

```bash
gcloud auth login
gcloud config set project datawarehouse-422511
./scripts/collect_cost_data.sh
```

## Summary of Files in This Update

All these files were added in the cost analysis update:

1. **docs/COST_ANALYSIS_DEV_PROD.md** - Comprehensive cost analysis (29k words)
2. **Cost estimate - Dev and Prod.csv** - Detailed cost spreadsheet
3. **COST_ANALYSIS_QUICK_START.md** - Quick reference guide
4. **DATA_COLLECTION_INSTRUCTIONS.md** - Data collection guide
5. **scripts/collect_cost_data.sh** - Automated data collection script

If any of these are missing, you need to pull the latest changes from the `copilot/update-cost-estimate-docs` branch.

---

**Last Updated:** 2026-02-02  
**Branch:** copilot/update-cost-estimate-docs  
**Related Issue:** Script not found when running locally
