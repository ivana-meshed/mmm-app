# Fix: "Start Next Job" Button Not Working When Queue is Paused

## Problem

When clicking the "⏭️ Start Next Job" button in the Queue Monitor, pending jobs would not upgrade to RUNNING status if the queue was paused. The user would see a "Ticked queue" message but the job would remain in PENDING state.

## Root Cause

The `_safe_tick_once()` function in `app/app_shared.py` contains an early return check (line 187-188):

```python
if not running_flag:
    return {"ok": True, "message": "queue is paused", "changed": False}
```

This check prevents any queue operations when `queue_running = False`. While this is correct behavior for automatic queue processing (Cloud Scheduler ticks and auto-refresh), it's incorrect for the manual "Start Next Job" button, which should work even when the queue is paused.

## Solution

Added a `force: bool = False` parameter to the queue tick functions that allows bypassing the `queue_running` check when explicitly set to `True`.

### Modified Functions

1. **`_safe_tick_once()`** in `app/app_shared.py`
   - Added `force: bool = False` parameter
   - Modified check to: `if not running_flag and not force:`
   - Added documentation explaining the force parameter

2. **`queue_tick_once_headless()`** in `app/app_shared.py`
   - Added `force: bool = False` parameter
   - Passes force parameter to `_safe_tick_once()`

3. **`_queue_tick()`** in `app/app_split_helpers.py`
   - Added `force: bool = False` parameter
   - Passes force parameter to `queue_tick_once_headless()`

4. **"Start Next Job" button handler** in `app/nav/Run_Experiment.py`
   - Changed from `_queue_tick()` to `_queue_tick(force=True)`
   - Updated log message to indicate force=True

### Behavior Matrix

| Queue State | Auto-refresh/Scheduler | Manual "Start Next Job" |
|-------------|------------------------|-------------------------|
| Running     | ✅ Processes jobs      | ✅ Processes jobs       |
| Paused      | ❌ Returns early       | ✅ Processes jobs (force=True) |

## Testing

### Unit Tests

Added `tests/test_queue_tick_force.py` with comprehensive test coverage:
- Truth table for all combinations of `queue_running` and `force`
- Function signature tests
- All 8 tests passing ✅

**Note**: Current tests validate the logic of the force parameter. Future enhancement: Add integration tests that import actual functions with mocked GCS/launcher dependencies.

Run tests:
```bash
pytest tests/test_queue_tick_force.py -v
```

### Manual Testing Steps

#### Test Case 1: Manual Start with Paused Queue (Main Fix)

1. Navigate to "Run Experiment" → "Status" tab
2. Ensure at least one PENDING job exists in the queue
3. Click "⏸️ Stop Queue" to pause the queue
4. Verify queue caption shows "Queue is STOPPED"
5. Click "⏭️ Start Next Job" button
6. **Expected**: Job should transition from PENDING → LAUNCHING → RUNNING
7. **Expected**: Toast message "Ticked queue" appears
8. **Expected**: Queue remains STOPPED (manual action doesn't auto-start queue)

#### Test Case 2: Manual Start with Running Queue (Regression)

1. Navigate to "Run Experiment" → "Status" tab
2. Ensure at least one PENDING job exists
3. Click "▶️ Start Queue" to start the queue
4. Verify queue caption shows "Queue is RUNNING"
5. Click "⏭️ Start Next Job" button
6. **Expected**: Job processes normally (no regression)
7. **Expected**: Queue remains RUNNING

#### Test Case 3: Auto-refresh Respects Queue State (Regression)

1. Add jobs to queue
2. Click "▶️ Start Queue"
3. Verify jobs automatically process via auto-refresh
4. Click "⏸️ Stop Queue" 
5. **Expected**: Auto-refresh stops processing new jobs
6. **Expected**: Running jobs complete, but no new PENDING jobs start
7. Click "⏭️ Start Next Job"
8. **Expected**: One PENDING job starts (force=True)
9. Queue should still show STOPPED

#### Test Case 4: Cloud Scheduler Respects Queue State (Regression)

1. Ensure queue has PENDING jobs
2. Stop the queue via "⏸️ Stop Queue"
3. Wait for Cloud Scheduler to trigger (every 1 minute)
4. Check Cloud Run logs for `[QUEUE_TICK]` entries
5. **Expected**: Logs show "queue is paused" message
6. **Expected**: No jobs are launched by scheduler while paused

## Deployment

This fix is deployed to the dev environment via the `ci-dev.yml` workflow when pushing to feature branches.

### Dev Environment
- Branch: `copilot/fix-start-next-job-error` or `feat-*` branches
- Service: `mmm-app-dev`
- Queue: `default-dev`

### Production Deployment
After testing in dev:
1. Create PR to merge feature branch to `main`
2. Review and approve PR
3. Merge to `main` triggers `ci.yml` workflow
4. Deploys to production `mmm-app` service

## Edge Cases Handled

1. **Empty queue**: Returns early before force check
2. **Already RUNNING job**: Updates that job first (Phase 1), doesn't start new one
3. **LAUNCHING job**: Promotes to RUNNING on next tick
4. **Concurrent ticks**: Optimistic concurrency with generation matching
5. **Missing launcher**: Returns error (existing behavior)

## Backward Compatibility

✅ **Fully backward compatible**: The `force` parameter defaults to `False`, so all existing callers continue to work with no changes:
- Auto-refresh in `_auto_refresh_and_tick()`
- Cloud Scheduler endpoint in `handle_queue_tick_from_query_params()`
- Any other internal queue tick calls

Only the manual "Start Next Job" button explicitly uses `force=True`.

## Related Files

- `app/app_shared.py` - Core queue tick logic
- `app/app_split_helpers.py` - Queue tick wrapper and auto-refresh
- `app/nav/Run_Experiment.py` - UI buttons and handlers
- `tests/test_queue_tick_force.py` - Unit tests

## Monitoring

Check Cloud Run logs for these messages:

```
[QUEUE] Manual queue tick triggered (force=True) for 'default'
[QUEUE] Starting queue tick
[QUEUE] Queue tick result: {ok: True, message: ..., changed: True}
[QUEUE_TICK] Job <id> progressed from PENDING to RUNNING
```

If force parameter is not working correctly, you'll see:
```
[QUEUE] Queue tick result: {ok: True, message: "queue is paused", changed: False}
```

## Rollback Plan

If issues arise, revert the commits:
```bash
git revert 7f43d11 c908fd5
git push origin copilot/fix-start-next-job-error
```

This will remove the force parameter and restore original behavior.
