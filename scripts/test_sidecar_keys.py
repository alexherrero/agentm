#!/usr/bin/env python3
"""The sidecars key on a note's path, and follow a note that moves.

A slug is not an identity. Measured on the live sidecar the morning this was
written: 26 keys matched more than one file in the vault, `progress` matching
eight, `_index` twenty-four. Eight notes shared one decay clock, so a recall of
any one of them read as a recall of all eight — and the curve that reads that
clock is the one deciding what sinks.

Covers the keying in both sidecars, the version-1 fallback that keeps a vault
from before the change, the re-key a hand move needs, and the migration that
moves the live file over — including what it refuses to guess at.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))
sys.path.insert(0, str(_REPO / "scripts" / "migrate"))

import heat_policy  # noqa: E402
import lifecycle  # noqa: E402
import sidecar_keys  # noqa: E402
import vault_layout  # noqa: E402


class _Base(unittest.TestCase):
    """A flat fixture vault: the memory root and the vault root are the same
    directory, so a key and a path agree and the test is about the keying rather
    than about a layout."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir(parents=True)
        # The sidecar helpers resolve the engine state dir; point it here so the
        # test never reads or writes the machine's own.
        self._state = Path(self._tmp.name) / "state"
        self._state.mkdir()
        import engine_state
        self._saved_dir = engine_state.engine_state_dir
        engine_state.engine_state_dir = lambda: self._state
        self.addCleanup(lambda: setattr(engine_state, "engine_state_dir", self._saved_dir))

    def write(self, rel: str, body: str = "a body") -> Path:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\nkind: note\n---\n\n{body}\n", encoding="utf-8")
        return p

    def sidecar(self, name: str) -> dict:
        path = vault_layout.sidecar_path(self.vault, name)
        return json.loads(path.read_text(encoding="utf-8"))


class TwoNotesOneBasename(_Base):

    def test_the_clocks_are_separate(self):
        self.write("projects/agentm/progress.md")
        self.write("projects/blog/progress.md")
        fm = {"kind": "note"}

        lifecycle.record_recall_access(
            self.vault, "progress", fm, "projects/agentm/progress.md", today="2026-09-10")
        lifecycle.record_recall_access(
            self.vault, "progress", fm, "projects/blog/progress.md", today="2026-01-01")

        entries = self.sidecar(lifecycle.LIFECYCLE_SIDECAR_NAME)["entries"]
        self.assertEqual(entries["projects/agentm/progress.md"]["last_access"], "2026-09-10")
        self.assertEqual(entries["projects/blog/progress.md"]["last_access"], "2026-01-01")

        # And the curve reads them apart, which is the point of keeping them
        # apart: one note is fresh and the other is not.
        fresh = lifecycle.compute_decay_score(
            self.vault, "progress", fm, "projects/agentm/progress.md", now="2026-09-11")
        stale = lifecycle.compute_decay_score(
            self.vault, "progress", fm, "projects/blog/progress.md", now="2030-01-01")
        self.assertEqual(fresh, 1.0)
        self.assertLess(stale, 1.0)

    def test_the_heat_counters_are_separate(self):
        self.write("projects/agentm/progress.md")
        self.write("projects/blog/progress.md")
        heat_policy.record_hits(self.vault, [
            ("projects/agentm/progress.md", "progress"),
        ], today="2026-09-10")
        entries = self.sidecar(heat_policy.HEAT_SIDECAR_NAME)["entries"]
        self.assertEqual(sorted(entries), ["projects/agentm/progress.md"])
        self.assertEqual(entries["projects/agentm/progress.md"]["hits"], 1)


class AVaultFromBeforeTheChange(_Base):
    """A version-1 sidecar keeps every clock it has until the migration runs."""

    def _write_v1(self, name: str, entries: dict) -> None:
        path = vault_layout.sidecar_path(self.vault, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")

    def test_a_version_one_clock_is_still_read(self):
        self.write("memory/semantic/a-fact.md")
        self._write_v1(lifecycle.LIFECYCLE_SIDECAR_NAME,
                       {"a-fact": {"last_access": "2026-09-10"}})
        score = lifecycle.compute_decay_score(
            self.vault, "a-fact", {"kind": "note"}, "memory/semantic/a-fact.md",
            now="2026-09-11")
        self.assertEqual(score, 1.0)

    def test_a_version_two_file_does_not_fall_back_to_the_slug(self):
        """Otherwise the collision comes back through the reader."""
        self.write("memory/semantic/a-fact.md")
        path = vault_layout.sidecar_path(self.vault, lifecycle.LIFECYCLE_SIDECAR_NAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"version": 2, "entries": {"a-fact": {"last_access": "2026-09-10"}}}),
            encoding="utf-8")
        data = lifecycle._load_sidecar(self.vault)
        self.assertEqual(
            lifecycle._lookup(data, self.vault, "a-fact", "memory/semantic/a-fact.md"), {})


class AMoveKeepsTheClock(_Base):

    def test_rekey_follows_the_note(self):
        self.write("memory/semantic/a-fact.md")
        lifecycle.record_recall_access(
            self.vault, "a-fact", {"kind": "note"}, "memory/semantic/a-fact.md",
            today="2026-09-10")

        (self.vault / "agent" / "archive" / "memory" / "semantic").mkdir(parents=True)
        (self.vault / "memory" / "semantic" / "a-fact.md").rename(
            self.vault / "agent" / "archive" / "memory" / "semantic" / "a-fact.md")

        moved = lifecycle.rekey(self.vault, "memory/semantic/a-fact.md",
                                "agent/archive/memory/semantic/a-fact.md")
        self.assertTrue(moved)
        score = lifecycle.compute_decay_score(
            self.vault, "a-fact", {"kind": "note"},
            "agent/archive/memory/semantic/a-fact.md", now="2026-09-11")
        self.assertEqual(score, 1.0, "a moved note lost its clock")

    def test_the_entry_carries_a_fingerprint_to_be_found_by(self):
        self.write("memory/semantic/a-fact.md", "the body a move would carry with it")
        lifecycle.record_recall_access(
            self.vault, "a-fact", {"kind": "note"}, "memory/semantic/a-fact.md",
            today="2026-09-10")
        entry = self.sidecar(lifecycle.LIFECYCLE_SIDECAR_NAME)["entries"][
            "memory/semantic/a-fact.md"]
        self.assertTrue(entry.get("fingerprint"))
        # The same body at a new path fingerprints the same, which is what makes
        # the pairing possible at all.
        self.write("elsewhere/a-fact.md", "the body a move would carry with it")
        self.assertEqual(lifecycle.fingerprint_of(self.vault / "elsewhere/a-fact.md"),
                         entry["fingerprint"])

    def test_the_fingerprint_ignores_frontmatter(self):
        """Enrichment, the backfills and the operator all rewrite frontmatter. A
        fingerprint that changed when a tag was added would follow nothing."""
        a = self.write("one.md", "the same body")
        (self.vault / "two.md").write_text(
            "---\nkind: note\ntags: [added, later]\nsummary: written by a pass\n---\n\n"
            "the same body\n", encoding="utf-8")
        self.assertEqual(lifecycle.fingerprint_of(a),
                         lifecycle.fingerprint_of(self.vault / "two.md"))


class TheMigration(_Base):

    def _write_v1(self, entries: dict) -> Path:
        path = vault_layout.sidecar_path(self.vault, lifecycle.LIFECYCLE_SIDECAR_NAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
        return path

    def test_it_rekeys_what_it_can_and_refuses_to_guess(self):
        self.write("memory/semantic/a-fact.md")
        self.write("projects/agentm/progress.md")
        self.write("projects/blog/progress.md")
        path = self._write_v1({
            "a-fact": {"last_access": "2026-09-10"},
            "progress": {"last_access": "2026-09-10"},
            "long-gone": {"last_access": "2026-01-01"},
        })

        by_slug = sidecar_keys.index_by_slug(self.vault)
        plan = sidecar_keys.plan_for(path, by_slug, self.vault)
        self.assertEqual(plan["rekeyed"], {"a-fact": "memory/semantic/a-fact.md"})
        self.assertEqual(sorted(plan["ambiguous"]), ["progress"])
        self.assertEqual(plan["orphaned"], ["long-gone"])

        sidecar_keys.apply_plan(path, plan, self.vault)
        after = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(after["version"], 2)
        self.assertEqual(sorted(after["entries"]), ["memory/semantic/a-fact.md"])
        self.assertTrue(after["entries"]["memory/semantic/a-fact.md"].get("fingerprint"))

    def test_an_ambiguous_key_is_dropped_rather_than_given_to_one_of_them(self):
        """The entry is the sum of several notes' history. Handing it to one
        would tell that note it was recalled when another was."""
        self.write("projects/agentm/progress.md")
        self.write("projects/blog/progress.md")
        path = self._write_v1({"progress": {"last_access": "2026-09-10"}})
        by_slug = sidecar_keys.index_by_slug(self.vault)
        plan = sidecar_keys.plan_for(path, by_slug, self.vault)
        sidecar_keys.apply_plan(path, plan, self.vault)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["entries"], {})

    def test_a_frontmatter_slug_is_a_key_the_index_knows(self):
        p = self.vault / "memory" / "semantic" / "filed-under-another-name.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\nkind: note\nslug: the-written-slug\n---\n\nbody\n",
                     encoding="utf-8")
        by_slug = sidecar_keys.index_by_slug(self.vault)
        self.assertEqual(by_slug["the-written-slug"],
                         ["memory/semantic/filed-under-another-name.md"])

    def test_running_it_again_changes_nothing(self):
        self.write("memory/semantic/a-fact.md")
        path = self._write_v1({"a-fact": {"last_access": "2026-09-10"}})
        by_slug = sidecar_keys.index_by_slug(self.vault)
        sidecar_keys.apply_plan(path, sidecar_keys.plan_for(path, by_slug, self.vault),
                                self.vault)
        first = path.read_text(encoding="utf-8")
        second_plan = sidecar_keys.plan_for(path, by_slug, self.vault)
        self.assertEqual(second_plan["rekeyed"], {})
        self.assertEqual(path.read_text(encoding="utf-8"), first)

    def test_the_manifest_names_what_it_dropped(self):
        self.write("projects/agentm/progress.md")
        self.write("projects/blog/progress.md")
        path = self._write_v1({"progress": {"last_access": "2026-09-10"},
                               "long-gone": {"last_access": "2026-01-01"}})
        by_slug = sidecar_keys.index_by_slug(self.vault)
        plan = sidecar_keys.plan_for(path, by_slug, self.vault)
        text = sidecar_keys.render_manifest([plan], "now")
        self.assertIn("projects/agentm/progress.md", text)
        self.assertIn("long-gone", text)
        self.assertIn("Dropped", text)


if __name__ == "__main__":
    unittest.main()
