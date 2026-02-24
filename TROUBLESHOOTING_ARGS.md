# Troubleshooting: Unrecognized Arguments Error

## Issue

When running:
```bash
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all
```

You get:
```
benchmark_mmm.py: error: unrecognized arguments: --all-benchmarks --test-run-all
```

## Root Cause

The arguments `--all-benchmarks` and `--test-run-all` were recently added to the script. If you're seeing this error, your local copy is outdated.

## Quick Fix

```bash
# 1. Navigate to repository
cd /path/to/mmm-app

# 2. Pull latest changes
git pull origin copilot/follow-up-on-pr-170

# 3. Clear Python cache
find . -name "*.pyc" -delete
find . -name "__pycache__" -type d -delete

# 4. Verify arguments exist
python scripts/benchmark_mmm.py --help | grep -A2 "all-benchmarks"

# 5. Run the command
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all
```

## Detailed Verification Steps

### Step 1: Check Current Commit

```bash
git log -1 scripts/benchmark_mmm.py
```

**Expected output should include:**
- Commit hash starting with `2e2aed8` (for --all-benchmarks)
- OR commit `815063c` or later

If you see an older commit, you need to pull latest changes.

### Step 2: Verify Arguments in File

```bash
grep -n "all-benchmarks\|test-run-all" scripts/benchmark_mmm.py
```

**Expected output:**
```
1145:        help="Run quick test with minimal iterations (10) and trials (1) for ALL variants (validates queue processing)",
1149:        "--all-benchmarks",
1151:        help="Run ALL benchmark configurations in one command (discovers all .json files in benchmarks/)",
```

If these lines are NOT present, pull latest changes.

### Step 3: View Help Output

```bash
python scripts/benchmark_mmm.py --help
```

**Look for these sections:**
```
--test-run            Run quick test with minimal iterations (10) and 
                      trials (1), first variant only
--test-run-all        Run quick test with minimal iterations (10) and 
                      trials (1) for ALL variants (validates queue processing)
--all-benchmarks      Run ALL benchmark configurations in one command 
                      (discovers all .json files in benchmarks/)
```

If you DON'T see these, your Python is using a cached version.

### Step 4: Check Python Compilation

```bash
python3 -m py_compile scripts/benchmark_mmm.py
echo $?
```

**Expected:** Exit code 0 (no errors)

## Common Issues

### Issue 1: Branch Not Updated

**Symptom:** `git log` shows old commits

**Solution:**
```bash
git fetch origin
git checkout copilot/follow-up-on-pr-170
git pull origin copilot/follow-up-on-pr-170
```

### Issue 2: Python Bytecode Cache

**Symptom:** Help doesn't show new arguments even after git pull

**Solution:**
```bash
# Delete all .pyc files
find . -name "*.pyc" -delete

# Delete all __pycache__ directories
find . -name "__pycache__" -type d -exec rm -rf {} +

# Or use git clean (careful - removes untracked files!)
git clean -fdx scripts/__pycache__
```

### Issue 3: Wrong Directory

**Symptom:** File not found or wrong file

**Solution:**
```bash
# Verify you're in the repo root
pwd
# Should show: /path/to/mmm-app

# Check file exists
ls -la scripts/benchmark_mmm.py
# Should show the file
```

### Issue 4: File Permissions

**Symptom:** Permission denied

**Solution:**
```bash
# Make script executable
chmod +x scripts/benchmark_mmm.py

# Or run with python explicitly
python scripts/benchmark_mmm.py --help
```

## Verification Commands

After pulling latest changes, verify everything works:

```bash
# 1. Arguments exist in file
grep -c "all-benchmarks" scripts/benchmark_mmm.py
# Should output: 2 or more

# 2. Help shows arguments  
python scripts/benchmark_mmm.py --help | grep -c "all-benchmarks"
# Should output: 1 or more

# 3. Dry run works
python scripts/benchmark_mmm.py --all-benchmarks --dry-run
# Should run without errors and show benchmark discovery
```

## Expected Output After Fix

When you run:
```bash
python scripts/benchmark_mmm.py --help
```

You should see (partial output):
```
optional arguments:
  -h, --help            show this help message and exit
  --config CONFIG       Path to benchmark configuration JSON file
  --list-configs        List available benchmark configurations
  ...
  --test-run            Run quick test with minimal iterations (10) and
                        trials (1), first variant only
  --test-run-all        Run quick test with minimal iterations (10) and
                        trials (1) for ALL variants (validates queue
                        processing)
  --all-benchmarks      Run ALL benchmark configurations in one command
                        (discovers all .json files in benchmarks/)
```

## Still Not Working?

If you've tried all the above and still get the error:

1. **Check branch name:**
   ```bash
   git branch
   # Should show: * copilot/follow-up-on-pr-170
   ```

2. **Force clean:**
   ```bash
   git reset --hard origin/copilot/follow-up-on-pr-170
   ```

3. **Re-clone (last resort):**
   ```bash
   cd ..
   mv mmm-app mmm-app.bak
   git clone https://github.com/ivana-meshed/mmm-app.git
   cd mmm-app
   git checkout copilot/follow-up-on-pr-170
   ```

## Contact

If none of these solutions work, please provide:
1. Output of: `git log -1 scripts/benchmark_mmm.py`
2. Output of: `git status`
3. Output of: `grep -n "all-benchmarks" scripts/benchmark_mmm.py`

This will help diagnose the specific issue with your environment.
