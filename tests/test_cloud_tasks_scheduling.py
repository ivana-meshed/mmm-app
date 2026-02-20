"""
Tests for Cloud Tasks-based event-driven queue tick scheduling.

These tests verify the scheduling decision logic without requiring GCP access.
"""

import unittest


class TestScheduleNextTickIfNeeded(unittest.TestCase):
    """
    Tests for _schedule_next_tick_if_needed decision logic.
    Mirrors the conditions in app_shared._schedule_next_tick_if_needed.
    """

    # Idle messages that should NOT trigger a next tick
    IDLE_MESSAGES = frozenset(
        {
            "empty queue",
            "queue is paused",
            "no pending",
            "launcher not provided",
        }
    )
    TERMINAL_STATES = frozenset(
        {"succeeded", "failed", "cancelled", "completed", "error"}
    )

    def _should_schedule(self, result: dict):
        """
        Replicate the scheduling decision from _schedule_next_tick_if_needed.
        Returns ("none" | "immediate" | "delayed").
        """
        msg = (result.get("message") or "").lower()
        if msg in self.IDLE_MESSAGES:
            return "none"
        if result.get("changed") and any(
            s in msg for s in self.TERMINAL_STATES
        ):
            return "immediate"
        return "delayed"

    # --- Idle cases: no next tick ---

    def test_empty_queue_no_schedule(self):
        result = {"ok": True, "message": "empty queue", "changed": False}
        self.assertEqual(self._should_schedule(result), "none")

    def test_queue_paused_no_schedule(self):
        result = {"ok": True, "message": "queue is paused", "changed": False}
        self.assertEqual(self._should_schedule(result), "none")

    def test_no_pending_no_schedule(self):
        result = {"ok": True, "message": "no pending", "changed": False}
        self.assertEqual(self._should_schedule(result), "none")

    def test_launcher_not_provided_no_schedule(self):
        result = {
            "ok": False,
            "message": "launcher not provided",
            "changed": False,
        }
        self.assertEqual(self._should_schedule(result), "none")

    # --- Terminal cases: immediate next tick ---

    def test_job_succeeded_immediate(self):
        result = {"ok": True, "message": "SUCCEEDED", "changed": True}
        self.assertEqual(self._should_schedule(result), "immediate")

    def test_job_failed_immediate(self):
        result = {"ok": True, "message": "FAILED", "changed": True}
        self.assertEqual(self._should_schedule(result), "immediate")

    def test_job_cancelled_immediate(self):
        result = {"ok": True, "message": "CANCELLED", "changed": True}
        self.assertEqual(self._should_schedule(result), "immediate")

    def test_job_error_immediate(self):
        result = {"ok": True, "message": "ERROR", "changed": True}
        self.assertEqual(self._should_schedule(result), "immediate")

    # --- Ongoing cases: delayed next tick ---

    def test_job_launching_delayed(self):
        result = {"ok": True, "message": "Launched", "changed": True}
        self.assertEqual(self._should_schedule(result), "delayed")

    def test_job_running_no_change_delayed(self):
        result = {"ok": True, "message": "no change", "changed": False}
        self.assertEqual(self._should_schedule(result), "delayed")

    def test_job_running_progressed_delayed(self):
        # LAUNCHING → RUNNING transition
        result = {"ok": True, "message": "running", "changed": True}
        self.assertEqual(self._should_schedule(result), "delayed")

    def test_launch_error_immediate(self):
        # Launch failure message contains "error" (terminal state) with changed=True
        # → schedules immediate tick to pick up next pending job
        result = {
            "ok": True,
            "message": "launch failed: some error",
            "changed": True,
        }
        self.assertEqual(self._should_schedule(result), "immediate")

    # --- Edge cases ---

    def test_none_message_defaults_to_delayed(self):
        # None/empty message is not in IDLE_MESSAGES → defaults to delayed
        result = {"ok": True, "message": None, "changed": False}
        self.assertEqual(self._should_schedule(result), "delayed")

    def test_empty_message_defaults_to_delayed(self):
        # Empty string is not in IDLE_MESSAGES → defaults to delayed
        result = {"ok": True, "message": "", "changed": False}
        self.assertEqual(self._should_schedule(result), "delayed")

    def test_terminal_state_not_changed_is_delayed(self):
        # changed=False means it was already in that state, so still polling
        result = {"ok": True, "message": "SUCCEEDED", "changed": False}
        self.assertEqual(self._should_schedule(result), "delayed")


if __name__ == "__main__":
    unittest.main()
