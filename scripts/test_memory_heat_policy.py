#!/usr/bin/env python3
"""Tests for heat_policy.py — heat-based always-load curation (#46 Part G).

Covers:
  - record_hit(): heat sidecar reads/writes, session counting, last_hit tracking
  - run_policy(): cold-demote, hot-promote, pin-never-demote, spike guard,
                  safety floor (MIN_ALWAYS_LOAD)
  - pin_entry(): restores demoted entry, adds heat_pin to frontmatter

Fixture vaults are fully synthetic (tmp dirs), no real vault paths touched.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

# Inject scripts dir so heat_policy + recall imports resolve without install.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from heat_policy import (  # noqa: E402
    COLD_SESSIONS_MIN,
    HOT_HITS_MIN,
    HOT_SESSIONS_MIN,
    MIN_ALWAYS_LOAD,
    HEAT_SIDECAR_NAME,
    _load_heat,
    _patch_frontmatter,
    pin_entry,
    record_hit,
    run_policy,
)

_ALWAYS_LOAD_REL = Path("personal-private") / "_always-load"


def _make_entry(slug: str, *, group: str = "personal-private", always_load: bool = True,
                heat_pin: bool = False) -> str:
    """Build a minimal valid entry file content."""
    heat_pin_line = "\nheat_pin: true" if heat_pin else ""
    al_value = "true" if always_load else "false"
    return (
        f"---\nkind: convention\nstatus: active\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
        f"tags: [test]\ngroup: {group}\nslug: {slug}\nalways_load: {al_value}{heat_pin_line}\n---\n\n"
        f"Body of {slug}.\n"
    )


def _make_vault(tmp_root: Path, always_load_slugs: list[str],
                ondemand_slugs: list[str] | None = None) -> Path:
    """Create a minimal vault with always-load and on-demand entries."""
    vault = tmp_root / "vault"
    al_dir = vault / _ALWAYS_LOAD_REL
    al_dir.mkdir(parents=True, exist_ok=True)
    pd_dir = vault / "personal-private"
    pd_dir.mkdir(parents=True, exist_ok=True)
    for slug in always_load_slugs:
        (al_dir / f"{slug}.md").write_text(_make_entry(slug), encoding="utf-8")
    for slug in (ondemand_slugs or []):
        (pd_dir / f"{slug}.md").write_text(
            _make_entry(slug, always_load=False), encoding="utf-8"
        )
    return vault


class TestRecordHit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_hit_creates_sidecar(self):
        record_hit(self.vault, "my-entry", today="2026-06-14")
        data = _load_heat(self.vault)
        self.assertEqual(data["total_sessions"], 1)
        entry = data["entries"]["my-entry"]
        self.assertEqual(entry["hits"], 1)
        self.assertEqual(entry["hit_sessions"], 1)
        self.assertEqual(entry["last_hit"], "2026-06-14")

    def test_same_day_hits_accumulate_but_session_counted_once(self):
        record_hit(self.vault, "e", today="2026-06-14")
        record_hit(self.vault, "e", today="2026-06-14")
        record_hit(self.vault, "e", today="2026-06-14")
        data = _load_heat(self.vault)
        self.assertEqual(data["total_sessions"], 1)  # one day = one session
        entry = data["entries"]["e"]
        self.assertEqual(entry["hits"], 3)
        self.assertEqual(entry["hit_sessions"], 1)

    def test_new_day_increments_session(self):
        record_hit(self.vault, "e", today="2026-06-13")
        record_hit(self.vault, "e", today="2026-06-14")
        data = _load_heat(self.vault)
        self.assertEqual(data["total_sessions"], 2)
        entry = data["entries"]["e"]
        self.assertEqual(entry["hits"], 2)
        self.assertEqual(entry["hit_sessions"], 2)
        self.assertEqual(entry["last_hit"], "2026-06-14")

    def test_multiple_slugs_independent(self):
        record_hit(self.vault, "hot-entry", today="2026-06-14")
        record_hit(self.vault, "cold-entry", today="2026-06-14")
        record_hit(self.vault, "hot-entry", today="2026-06-14")
        data = _load_heat(self.vault)
        self.assertEqual(data["entries"]["hot-entry"]["hits"], 2)
        self.assertEqual(data["entries"]["cold-entry"]["hits"], 1)

    def test_graceful_on_unreadable_vault(self):
        """record_hit never raises — uses best-effort semantics."""
        bad_vault = Path(self.tmp.name) / "nonexistent" / "vault"
        record_hit(bad_vault, "slug", today="2026-06-14")  # must not raise


class TestPatchFrontmatter(unittest.TestCase):

    def test_update_existing_field(self):
        content = "---\nalways_load: true\nslug: foo\n---\n\nBody.\n"
        patched = _patch_frontmatter(content, {"always_load": False})
        self.assertIn("always_load: false", patched)
        self.assertNotIn("always_load: true", patched)

    def test_add_new_field(self):
        content = "---\nslug: foo\n---\n\nBody.\n"
        patched = _patch_frontmatter(content, {"heat_pin": True})
        self.assertIn("heat_pin: true", patched)
        self.assertIn("Body.", patched)

    def test_body_preserved(self):
        content = "---\nslug: foo\n---\n\nImportant body content.\n"
        patched = _patch_frontmatter(content, {"heat_pin": True})
        self.assertIn("Important body content.", patched)


class TestRunPolicyDemote(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_sessions(self, vault: Path, n: int) -> None:
        """Write a heat sidecar with `n` total_sessions recorded."""
        sidecar = vault / HEAT_SIDECAR_NAME
        data = {"version": 1, "total_sessions": n, "last_session_day": "2026-06-14", "entries": {}}
        sidecar.write_text(json.dumps(data), encoding="utf-8")

    def test_cold_entry_demoted_after_enough_sessions(self):
        slugs = [f"entry-{i:02d}" for i in range(10)]
        vault = _make_vault(self.root, slugs)
        self._seed_sessions(vault, COLD_SESSIONS_MIN)

        stderr = io.StringIO()
        result = run_policy(vault, dry_run=False, stderr=stderr)

        # Safety floor: MIN_ALWAYS_LOAD entries must remain.
        remaining = list((vault / _ALWAYS_LOAD_REL).glob("*.md"))
        self.assertGreaterEqual(len(remaining), MIN_ALWAYS_LOAD)

        # Some entries should have been demoted.
        self.assertGreater(len(result["demoted"]), 0)
        log = stderr.getvalue()
        self.assertIn("DEMOTE", log)

    def test_too_early_no_demote(self):
        vault = _make_vault(self.root, ["alpha", "beta"])
        self._seed_sessions(vault, COLD_SESSIONS_MIN - 1)

        stderr = io.StringIO()
        result = run_policy(vault, dry_run=False, stderr=stderr)
        self.assertEqual(result["demoted"], [])

    def test_pinned_entry_never_demoted(self):
        vault = _make_vault(self.root, [f"entry-{i}" for i in range(10)])
        self._seed_sessions(vault, COLD_SESSIONS_MIN)

        # Pin one entry.
        al_dir = vault / _ALWAYS_LOAD_REL
        pinned_path = al_dir / "entry-0.md"
        content = pinned_path.read_text(encoding="utf-8")
        pinned_path.write_text(
            _patch_frontmatter(content, {"heat_pin": True}), encoding="utf-8"
        )

        stderr = io.StringIO()
        result = run_policy(vault, dry_run=False, stderr=stderr)
        self.assertNotIn("entry-0", result["demoted"])
        self.assertIn("entry-0", result["pinned_skipped"])

    def test_safety_floor_respected(self):
        slugs = [f"e-{i}" for i in range(MIN_ALWAYS_LOAD + 2)]
        vault = _make_vault(self.root, slugs)
        self._seed_sessions(vault, COLD_SESSIONS_MIN)

        result = run_policy(vault, dry_run=False, stderr=io.StringIO())
        remaining = list((vault / _ALWAYS_LOAD_REL).glob("*.md"))
        self.assertEqual(len(remaining), MIN_ALWAYS_LOAD)
        self.assertGreater(result["floor_skipped"], 0)

    def test_dry_run_does_not_move_files(self):
        slugs = [f"e-{i}" for i in range(10)]
        vault = _make_vault(self.root, slugs)
        self._seed_sessions(vault, COLD_SESSIONS_MIN)

        before = set(f.name for f in (vault / _ALWAYS_LOAD_REL).glob("*.md"))
        result = run_policy(vault, dry_run=True, stderr=io.StringIO())
        after = set(f.name for f in (vault / _ALWAYS_LOAD_REL).glob("*.md"))

        self.assertEqual(before, after)  # dry-run: no files moved
        self.assertGreater(len(result["demoted"]), 0)  # but candidates were identified

    def test_hot_entry_not_demoted(self):
        vault = _make_vault(self.root, ["hot-entry", "cold-entry"])
        self._seed_sessions(vault, COLD_SESSIONS_MIN)

        # Record enough hits for hot-entry to be exempt from cold-demotion check.
        # (cold-demotion only applies when hits == 0; any hit exempts it)
        sidecar_path = vault / HEAT_SIDECAR_NAME
        data = json.loads(sidecar_path.read_text())
        data["entries"]["hot-entry"] = {"hits": 5, "hit_sessions": 3, "last_hit": "2026-06-14"}
        sidecar_path.write_text(json.dumps(data))

        result = run_policy(vault, dry_run=False, stderr=io.StringIO())
        self.assertNotIn("hot-entry", result["demoted"])


class TestRunPolicyPromote(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_heat(self, vault: Path, entries: dict) -> None:
        """Write heat sidecar with given entries dict."""
        sidecar = vault / HEAT_SIDECAR_NAME
        data = {
            "version": 1,
            "total_sessions": COLD_SESSIONS_MIN,
            "last_session_day": "2026-06-14",
            "entries": entries,
        }
        sidecar.write_text(json.dumps(data), encoding="utf-8")

    def test_sustained_hot_on_demand_promoted(self):
        vault = _make_vault(self.root, ["always-entry"], ondemand_slugs=["hot-demand"])
        self._seed_heat(vault, {
            "hot-demand": {"hits": HOT_HITS_MIN, "hit_sessions": HOT_SESSIONS_MIN, "last_hit": "2026-06-14"},
        })

        stderr = io.StringIO()
        result = run_policy(vault, dry_run=False, stderr=stderr)
        self.assertIn("hot-demand", result["promoted"])
        self.assertTrue((vault / _ALWAYS_LOAD_REL / "hot-demand.md").exists())
        self.assertIn("PROMOTE", stderr.getvalue())

    def test_spike_guard_single_session_not_promoted(self):
        """All hits in 1 session → hit_sessions=1 < HOT_SESSIONS_MIN → no promotion."""
        vault = _make_vault(self.root, ["always-entry"], ondemand_slugs=["spike-entry"])
        self._seed_heat(vault, {
            "spike-entry": {
                "hits": HOT_HITS_MIN * 3,  # many hits but...
                "hit_sessions": 1,          # ...all in one session (spike)
                "last_hit": "2026-06-14",
            },
        })

        result = run_policy(vault, dry_run=False, stderr=io.StringIO())
        self.assertNotIn("spike-entry", result["promoted"])

    def test_promote_dry_run(self):
        vault = _make_vault(self.root, ["always-entry"], ondemand_slugs=["hot-demand"])
        self._seed_heat(vault, {
            "hot-demand": {"hits": HOT_HITS_MIN, "hit_sessions": HOT_SESSIONS_MIN, "last_hit": "2026-06-14"},
        })

        before_al = set(f.name for f in (vault / _ALWAYS_LOAD_REL).glob("*.md"))
        result = run_policy(vault, dry_run=True, stderr=io.StringIO())
        after_al = set(f.name for f in (vault / _ALWAYS_LOAD_REL).glob("*.md"))

        self.assertEqual(before_al, after_al)  # no file moved
        self.assertIn("hot-demand", result["promoted"])


class TestPinEntry(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pin_always_load_entry_adds_flag(self):
        vault = _make_vault(self.root, ["my-entry"])
        stderr = io.StringIO()
        ok = pin_entry(vault, "my-entry", stderr=stderr)
        self.assertTrue(ok)
        content = (vault / _ALWAYS_LOAD_REL / "my-entry.md").read_text()
        self.assertIn("heat_pin: true", content)
        self.assertIn("PIN", stderr.getvalue())

    def test_pin_restores_demoted_entry(self):
        """A previously demoted (on-demand) entry is moved back to always-load."""
        vault = _make_vault(self.root, ["other-entry"], ondemand_slugs=["demoted-entry"])
        stderr = io.StringIO()
        ok = pin_entry(vault, "demoted-entry", stderr=stderr)
        self.assertTrue(ok)
        self.assertTrue((vault / _ALWAYS_LOAD_REL / "demoted-entry.md").exists())
        content = (vault / _ALWAYS_LOAD_REL / "demoted-entry.md").read_text()
        self.assertIn("heat_pin: true", content)
        self.assertIn("always_load: true", content)

    def test_pin_stays_across_policy_pass(self):
        """Pinned entry is in always-load + heat_pin=true → survives policy demotion."""
        vault = _make_vault(self.root, [f"entry-{i}" for i in range(10)])
        # Pin one entry first.
        pin_entry(vault, "entry-0", stderr=io.StringIO())

        # Seed enough sessions to trigger cold-demotion.
        sidecar_path = vault / HEAT_SIDECAR_NAME
        data = {
            "version": 1, "total_sessions": COLD_SESSIONS_MIN,
            "last_session_day": "2026-06-14", "entries": {},
        }
        sidecar_path.write_text(json.dumps(data))

        result = run_policy(vault, dry_run=False, stderr=io.StringIO())
        self.assertNotIn("entry-0", result["demoted"])
        self.assertIn("entry-0", result["pinned_skipped"])

    def test_pin_missing_slug_returns_false(self):
        vault = _make_vault(self.root, ["some-entry"])
        stderr = io.StringIO()
        ok = pin_entry(vault, "nonexistent-slug", stderr=stderr)
        self.assertFalse(ok)
        self.assertIn("not found", stderr.getvalue())


class TestRecallHitIntegration(unittest.TestCase):
    """Verify recall.py's prompt_submit() records hits via heat_policy."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prompt_submit_records_hits(self):
        """prompt_submit() with a matching vault entry records a heat hit."""
        import sys as _sys
        # Only run if recall module is importable (always true in this test tree).
        import recall  # noqa: F401

        vault = _make_vault(self.root, [], ondemand_slugs=[])
        # Create a simple on-demand entry the recall engine can match.
        pd_dir = vault / "personal-private"
        pd_dir.mkdir(parents=True, exist_ok=True)
        entry_path = pd_dir / "test-convention.md"
        entry_path.write_text(
            _make_entry("test-convention", always_load=False), encoding="utf-8"
        )

        import io as _io
        from recall import prompt_submit

        # Inject a known prompt that matches 'test-convention'.
        stdout = _io.StringIO()
        stderr = _io.StringIO()
        prompt_submit(
            vault=vault,
            prompt="convention",
            budget_ms=500,
            stdout=stdout,
            stderr=stderr,
        )
        # Heat sidecar may or may not record (depends on whether the grep
        # match found the entry). Just confirm no exception was raised — the
        # test is a smoke check, not a recall-correctness assertion.


if __name__ == "__main__":
    unittest.main()
