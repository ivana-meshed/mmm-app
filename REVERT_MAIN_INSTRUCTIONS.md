# Instructions to Revert PR #173 from Main Branch

## Problem
PR #173 was mistakenly merged into `main` branch (commit `ec2fc29a7689d104a6192e04866b3b609a74798b`) when it should have been merged into `dev` branch.

## Solution for Main Branch

To revert PR #173 from the `main` branch, execute the following commands:

```bash
# Checkout main branch
git checkout main

# Revert the merge commit
git revert -m 1 ec2fc29a7689d104a6192e04866b3b609a74798b

# Push the revert to main
git push origin main
```

### Explanation
- The `-m 1` option tells git to revert to the first parent (the main branch before the merge)
- Commit `ec2fc29a` is the merge commit from PR #173
- This will create a new revert commit that undoes all changes from PR #173

### Verification
After reverting, verify that the following files NO LONGER contain the PR #173 changes:
- `app/nav/Review_Model_Stability.py` - Should not have the "All" mode filtering fix
- `app/nav/View_Best_Results.py` - Should not have the parse_stamp datetime.min fix
- `app/nav/View_Results.py` - Should not have the parse_stamp datetime.min fix
- `tests/test_model_stability_filtering.py` - Should not exist
- `tests/test_model_stability_helpers.py` - Should not exist
- `tests/test_parse_stamp.py` - Should not exist

## Solution for Dev Branch

The PR #173 changes have been applied to the `dev` branch via this PR (`copilot/reverse-merge-main-to-dev`).

When this PR is merged to `dev`, it will include:
- Fix datetime comparison bug in parse_stamp functions
- Fix Model Stability page to validate runs have required files
- Fix Model Stability "All" mode and timestamp dropdown filtering
- All associated test files

## Action Required

**Manual step needed**: A repository maintainer must execute the revert command on the `main` branch as shown above.

This cannot be automated through a PR because:
1. The feature branch workflow typically targets a single branch
2. Direct pushes to `main` require specific permissions
3. This requires coordination between two branches (main and dev)

## Alternative: Use GitHub UI

You can also revert PR #173 using the GitHub web interface:

1. Go to https://github.com/ivana-meshed/mmm-app/pull/173
2. Click the "Revert" button
3. This will create a new PR to revert the changes from main
4. Merge that revert PR to complete the process
