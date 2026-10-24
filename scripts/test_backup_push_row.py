#!/usr/bin/env python3
"""The doctor's `backup-push` row: is the vault backup still pushing? (#818)

The four-hourly `vault-backup-push` job had no surface that went red when it
stopped, so a dead push looked like a working one. The daemon now reads the
push from the vault repository and reports it as `health.backup` in
`agentmd status --json`, and this row repeats what it says. The payloads below
are the wire shape the Go side pins in `TestTheBackupFieldsOnTheWire`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

import machinery_doctor as doctor_mod  # noqa: E402


def status(**backup) -> dict:
    return {"version": "test", "uptime": "1h0m0s",
            "health": {"level": "ok", "backup": backup}}


class TheBackupPushRow(unittest.TestCase):

    def test_a_recent_push_is_ok_and_names_its_age(self):
        check = doctor_mod.check_backup_push(status=status(
            state="current", remote="gitea", last_push_at="2026-10-23T15:47:00Z",
            last_push_age="2h0m0s", pending=12, oldest_pending_age="1h30m0s"))
        self.assertEqual(check.status, "OK")
        self.assertIn("last push 2h0m0s ago", check.detail)
        self.assertIn("12 commit(s) waiting", check.detail)

    def test_a_stale_backup_fails(self):
        check = doctor_mod.check_backup_push(status=status(
            state="stale", remote="gitea", last_push_at="2026-10-23T08:47:00Z",
            last_push_age="9h0m0s", pending=140, oldest_pending_age="8h30m0s"))
        self.assertEqual(check.status, "FAIL")
        self.assertIn("last push 9h0m0s ago", check.detail)
        self.assertIn("140 commit(s) waiting, the oldest 8h30m0s", check.detail)

    def test_nothing_waiting_is_ok_however_old_the_push(self):
        check = doctor_mod.check_backup_push(status=status(
            state="current", remote="gitea", last_push_at="2026-10-22T08:47:00Z",
            last_push_age="30h0m0s", pending=0))
        self.assertEqual(check.status, "OK")
        self.assertIn("nothing waiting", check.detail)

    def test_a_zero_push_age_omitted_from_the_wire_does_not_raise(self):
        # Go omits a zero Duration; a push stamped ahead of the clock reads 0.
        check = doctor_mod.check_backup_push(status=status(
            state="current", remote="gitea", last_push_at="2026-10-24T02:40:04Z", pending=0))
        self.assertEqual(check.status, "OK")
        self.assertIn("last push 0s ago", check.detail)

    def test_no_remote_is_unverified(self):
        check = doctor_mod.check_backup_push(status=status(state="none", pending=0))
        self.assertEqual(check.status, "UNVERIFIED")
        self.assertIn("no backup remote", check.detail)

    def test_a_daemon_that_predates_the_row_is_unverified(self):
        check = doctor_mod.check_backup_push(status={"health": {"level": "ok"}})
        self.assertEqual(check.status, "UNVERIFIED")
        self.assertIn("predates", check.detail)

    def test_no_binary_is_unverified(self):
        check = doctor_mod.check_backup_push(binary=Path("/nonexistent/agentmd"))
        self.assertEqual(check.status, "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
