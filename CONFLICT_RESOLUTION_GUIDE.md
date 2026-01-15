# Rebase Conflict Resolution Guide

**Issue:** You're trying to rebase dev onto main and encountered conflicts

**Error Message:**
```
CONFLICT (content): Merge conflict in app/pages/Review_Data.py
error: could not apply 04b77ab... dataprofile
```

---

## Why This Conflict Happens

The dev branch has a **major directory restructure**:
- Dev moved files from `app/pages/` → `app/nav/`
- Main still has files in `app/pages/`

When rebasing, Git tries to apply changes to files that are in different locations, causing conflicts.

---

## Resolution Steps

### Step 1: Check Current Rebase Status

```bash
git status
```

You should see something like:
```
interactive rebase in progress
Conflicts: app/pages/Review_Data.py
```

### Step 2: Understand the File Structure Difference

**In main:** `app/pages/Review_Data.py` exists  
**In dev:** `app/nav/Review_Data.py` exists (file was moved)

The rebase is trying to apply changes to the old location while dev has the new structure.

### Step 3: Option A - Abort and Use Different Strategy (RECOMMENDED)

Since the rebase is encountering structural conflicts due to major refactoring, **abort the rebase** and use a different approach:

```bash
# Abort the current rebase
git rebase --abort

# Go back to main
git checkout main
```

Then use **Option 1 (Force Merge)** or **Option 3 (Cherry-pick)** instead, as these handle structural changes better.

#### Using Option 1 - Force Merge:

```bash
git checkout main
git merge dev --allow-unrelated-histories -m "Merge dev branch with structural changes"

# If conflicts occur during merge:
# 1. Git will tell you which files have conflicts
# 2. Open each file and resolve conflicts manually
# 3. git add <resolved-files>
# 4. git commit
```

---

### Step 4: Option B - Continue Rebase (More Complex)

If you want to continue with the rebase despite the complexity:

#### 4a. Check what files are in conflict:

```bash
git status
```

#### 4b. For each conflicting file:

**If the file exists in both locations:**
```bash
# Check if the file is at the old location in main
ls -la app/pages/Review_Data.py

# Check if it should be at new location in dev
git show dev:app/nav/Review_Data.py
```

**To resolve:**
1. Decide which version to keep
2. Edit the file to resolve conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
3. Stage the resolved file: `git add app/pages/Review_Data.py`

#### 4c. Continue the rebase:

```bash
git rebase --continue
```

#### 4d. Handle subsequent conflicts:

The rebase will likely hit more conflicts because of the directory restructure. For each:
1. Resolve the conflict
2. `git add <file>`
3. `git rebase --continue`

Repeat until rebase completes or until you decide to abort.

---

## Why Rebase Is Difficult Here

The dev branch includes:
- **Directory restructure:** `app/pages/` → `app/nav/`
- **145 files changed** with major refactoring
- **Unrelated history** means no common ancestor to base the rebase on

This creates many conflicts that are difficult to resolve automatically.

---

## Recommended Alternative: Force Merge (Option 1)

Given the complexity, **Option 1 (Force Merge)** is more practical:

```bash
# Make sure you're on main
git checkout main

# Merge dev with unrelated histories
git merge dev --allow-unrelated-histories

# If there are conflicts:
# 1. Open each conflicting file
# 2. Look for conflict markers:
#    <<<<<<< HEAD (your main branch)
#    code from main
#    =======
#    code from dev
#    >>>>>>> dev
# 3. Choose which code to keep or combine them
# 4. Remove the conflict markers
# 5. Save the file

# Stage resolved files
git add <resolved-files>

# Complete the merge
git commit
```

### Handling Merge Conflicts

For the directory structure conflict:

1. **Decide on structure:** Keep `app/nav/` from dev (recommended for consistency)
2. **Remove old structure:** Delete `app/pages/` files if they're duplicates
3. **Update imports:** Make sure all imports reference the new locations

Example conflict resolution in `app/streamlit_app.py`:
```python
# If main has:
from app.pages.Review_Data import review_data

# And dev has:
from app.nav.Review_Data import review_data

# Choose the dev version (new structure)
```

---

## After Successful Merge/Rebase

1. **Test the application:**
   ```bash
   # Test locally
   streamlit run app/streamlit_app.py
   ```

2. **Run tests:**
   ```bash
   pytest tests/
   ```

3. **Check for import errors:**
   ```bash
   python -m py_compile app/**/*.py
   ```

4. **Commit and push:**
   ```bash
   git push origin main
   ```

---

## Quick Decision Tree

**Are you comfortable resolving many complex conflicts?**
- **No** → Abort rebase, use Option 1 (Force Merge)
- **Yes** → Continue with rebase, resolve each conflict

**Do you need clean linear history?**
- **Yes** → Complete the rebase (more work)
- **No** → Use Force Merge (faster, messier history)

**Want to review each change carefully?**
- **Yes** → Use Option 3 (Cherry-pick) from MERGE_SAFETY_REPORT.md
- **No** → Use Force Merge

---

## Getting Help

If you're stuck during rebase:

1. **Check rebase status:**
   ```bash
   git status
   git rebase --show-current-patch
   ```

2. **See what commit is being applied:**
   ```bash
   git log --oneline dev
   ```

3. **Abort and try different approach:**
   ```bash
   git rebase --abort
   ```

---

## Summary

**Current Situation:** Rebase hitting conflicts due to major directory restructure

**Best Option:** Abort rebase, use Force Merge (Option 1) instead

**Command:**
```bash
git rebase --abort
git checkout main
git merge dev --allow-unrelated-histories
# Resolve any merge conflicts
# git add <files>
# git commit
```

This approach is **faster** and **easier** to resolve than continuing the rebase.
