# Branch Switching Guide

## ⚠️ YOU ARE ON THE WRONG BRANCH! ⚠️

If you're seeing this error:
```
benchmark_mmm.py: error: unrecognized arguments: --all-benchmarks --test-run-all
```

And `git branch` shows:
```
* copilot/build-benchmarking-script  <-- WRONG!
```

**You need to switch to `copilot/follow-up-on-pr-170`** where all the new features were implemented.

---

## Quick Fix (30 seconds)

```bash
# 1. Switch to the correct branch
git checkout copilot/follow-up-on-pr-170

# 2. Pull latest changes
git pull origin copilot/follow-up-on-pr-170

# 3. Verify it works
python scripts/benchmark_mmm.py --help | grep "all-benchmarks"

# 4. Run your command
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all
```

---

## Why This Happened

You were working on `copilot/follow-up-on-pr-170` but somehow switched to `copilot/build-benchmarking-script`. This can happen due to:

1. **Multiple feature branches** - You have several `copilot/*` branches
2. **Git operations** - Merge/rebase/checkout commands switched branches
3. **IDE actions** - Your IDE may have switched branches
4. **Manual switch** - You ran `git checkout` to a different branch

---

## Detailed Instructions

### Step 1: Check Your Current Branch

```bash
git branch
```

Look for the `*` which indicates your current branch:
```
  copilot/build-benchmarking-script  <-- If this has *, you're on wrong branch
* copilot/follow-up-on-pr-170       <-- This is correct
```

### Step 2: List All Branches

To see all available branches:
```bash
git branch -a
```

You should see:
```
* copilot/build-benchmarking-script
  copilot/follow-up-on-pr-170
  remotes/origin/copilot/follow-up-on-pr-170
```

### Step 3: Switch to Correct Branch

```bash
git checkout copilot/follow-up-on-pr-170
```

Expected output:
```
Switched to branch 'copilot/follow-up-on-pr-170'
Your branch is up to date with 'origin/copilot/follow-up-on-pr-170'.
```

### Step 4: Pull Latest Changes

```bash
git pull origin copilot/follow-up-on-pr-170
```

Expected output:
```
Already up to date.
```

Or if there were new commits:
```
Updating abc1234..def5678
Fast-forward
 scripts/benchmark_mmm.py | 180 +++++++++++++++++++++
 1 file changed, 180 insertions(+)
```

### Step 5: Verify Arguments Exist

```bash
grep -n "all-benchmarks" scripts/benchmark_mmm.py
```

You should see output like:
```
1149:        "--all-benchmarks",
1240:    # Handle --all-benchmarks mode
1243:            logger.error("Cannot use both --all-benchmarks and --config")
```

Or check help:
```bash
python scripts/benchmark_mmm.py --help | grep -A2 "all-benchmarks"
```

### Step 6: Run Your Command

```bash
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all
```

---

## Verify You're on the Right Branch

After switching, verify with:

```bash
# Check current branch
git branch --show-current
# Should output: copilot/follow-up-on-pr-170

# Check recent commits
git log --oneline -5
# Should show commits like:
#   2ff3a82 Add troubleshooting guide
#   2e2aed8 Add --all-benchmarks flag
#   815063c Add --test-run-all flag

# Verify arguments in file
python scripts/benchmark_mmm.py --help | tail -20
# Should show both --all-benchmarks and --test-run-all
```

---

## Understanding the Branches

### copilot/follow-up-on-pr-170 (CORRECT ✅)
- Contains all PR #170 work
- Has benchmarking system
- Has --test-run, --test-run-all, --all-benchmarks
- Has job config upload fix
- Has result verification
- 20+ commits
- 16 files changed
- ~5000 lines added

### copilot/build-benchmarking-script (OLD ❌)
- Older branch
- May have some early work
- **Does NOT have the new flags**
- **Does NOT have the fixes**
- Not the active branch

---

## Common Mistakes

### ❌ Wrong: Trying to pull while on wrong branch
```bash
# On copilot/build-benchmarking-script
git pull  # This pulls build-benchmarking-script, not follow-up-on-pr-170!
```

### ✅ Correct: Switch first, then pull
```bash
git checkout copilot/follow-up-on-pr-170  # Switch first
git pull origin copilot/follow-up-on-pr-170  # Then pull
```

---

## Still Having Issues?

If after switching branches you still see the error:

1. **Clear Python cache:**
   ```bash
   find . -name "*.pyc" -delete
   find . -name "__pycache__" -type d -delete
   ```

2. **Verify file content:**
   ```bash
   grep -n "test-run-all\|all-benchmarks" scripts/benchmark_mmm.py
   ```
   Should show multiple lines.

3. **Check git status:**
   ```bash
   git status
   ```
   Should show "On branch copilot/follow-up-on-pr-170"

4. **Check for uncommitted changes:**
   ```bash
   git diff scripts/benchmark_mmm.py
   ```
   Should show nothing (or your local changes)

5. **Force reset (if needed):**
   ```bash
   git fetch origin
   git reset --hard origin/copilot/follow-up-on-pr-170
   ```
   ⚠️ WARNING: This will discard any uncommitted local changes!

---

## Quick Reference

| Branch | Has New Flags? | Active? |
|--------|---------------|---------|
| copilot/follow-up-on-pr-170 | ✅ YES | ✅ YES |
| copilot/build-benchmarking-script | ❌ NO | ❌ NO |

**Always use: copilot/follow-up-on-pr-170**

---

## Summary

**Problem:** You're on `copilot/build-benchmarking-script` instead of `copilot/follow-up-on-pr-170`

**Solution:** Run `git checkout copilot/follow-up-on-pr-170`

**Verification:** Run `python scripts/benchmark_mmm.py --help | grep all-benchmarks`

**Then:** Run your command: `python scripts/benchmark_mmm.py --all-benchmarks --test-run-all`

---

Need more help? Check `TROUBLESHOOTING_ARGS.md` for additional troubleshooting steps.
