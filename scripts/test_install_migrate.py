#!/usr/bin/env python3
"""Unit tests for lib/install/python/install_migrate.py — stdlib unittest.

Run directly:

    python3 -m unittest scripts.test_install_migrate

Or:

    python3 scripts/test_install_migrate.py

Covers (per V4 #30 plan 3 task 2 verification):
  - classify(): each of the 4 classifications + dir bundle + missing source
  - apply(): dry-run preserves target; real apply removes SAFE; writes record
  - apply(): OPERATOR_EDITED skipped without --force; force migrates with backup
  - apply(): record merging on partial-migration re-run
  - rollback(): restores SAFE from source; restores force_migrated from backup
  - rollback(): missing-record raises; record + backup removed on full success
  - cleanup(): refuses if operator content remains; removes install subdirs otherwise
  - inverse_mapping_for_clones(): round-trip with symlink_targets_for_clone
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_LIB = _REPO / "lib" / "install" / "python"
for p in (str(_LIB), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import install_migrate as im  # noqa: E402
from install_symlinks import symlink_targets_for_clone  # noqa: E402


# -----------------------------------------------------------------------------
# Fixture helpers
# -----------------------------------------------------------------------------

def _make_source_clones(root: Path) -> dict[str, str]:
    """Build a fake source-clones tree under `root` shaped like the agentm clone.

    Returns the source_clones dict {agentm} as paths (agentm-only since the
    crickets decouple — crickets self-manages as native plugins).

    Layout (matches symlink_targets_for_clone():

      <root>/agentm/.git/
      <root>/agentm/harness/agents/explorer.md
      <root>/agentm/harness/skills/wiki-author/SKILL.md
      <root>/agentm/harness/hooks/post-tool-use/hook.sh
      <root>/agentm/adapters/claude-code/agents/adversarial.md
      <root>/agentm/adapters/claude-code/commands/work.md
      <root>/agentm/adapters/claude-code/skills/diataxis/SKILL.md
    """
    agentm = root / "agentm"
    (agentm / ".git").mkdir(parents=True)

    # agentm/harness/
    (agentm / "harness" / "agents").mkdir(parents=True)
    (agentm / "harness" / "agents" / "explorer.md").write_text("explorer agent\n")
    (agentm / "harness" / "skills" / "wiki-author").mkdir(parents=True)
    (agentm / "harness" / "skills" / "wiki-author" / "SKILL.md").write_text("wiki-author skill\n")
    (agentm / "harness" / "hooks" / "post-tool-use").mkdir(parents=True)
    (agentm / "harness" / "hooks" / "post-tool-use" / "hook.sh").write_text("#!/bin/sh\n")

    # agentm/adapters/claude-code/
    (agentm / "adapters" / "claude-code" / "agents").mkdir(parents=True)
    (agentm / "adapters" / "claude-code" / "agents" / "adversarial.md").write_text("adversarial reviewer\n")
    (agentm / "adapters" / "claude-code" / "commands").mkdir(parents=True)
    (agentm / "adapters" / "claude-code" / "commands" / "work.md").write_text("work slash command\n")
    (agentm / "adapters" / "claude-code" / "skills" / "diataxis").mkdir(parents=True)
    (agentm / "adapters" / "claude-code" / "skills" / "diataxis" / "SKILL.md").write_text("diataxis skill\n")

    return {"agentm": str(agentm)}


def _copy_from_source(source_clones: dict[str, str], target: Path, rels: list[str]) -> None:
    """Copy SOURCE-canonical files into `<target>/.claude/<rel>` to simulate
    a pre-V4.3 per-project install. `rels` are install-rel paths
    (e.g. "agents/explorer.md", "skills/wiki-author")."""
    inverse = im.inverse_mapping_for_clones(source_clones)
    for rel in rels:
        slug, src, is_dir = inverse[rel]
        dest = target / ".claude" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if is_dir:
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

class InverseMappingTests(unittest.TestCase):
    def test_round_trip_with_forward_mapping(self):
        """For every entry in symlink_targets_for_clone, inverse_mapping returns
        the same source_path + is_dir back. Round-trip identity."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clones = _make_source_clones(root)
            inverse = im.inverse_mapping_for_clones(clones)
            # Walk forward; verify each forward entry appears in inverse with same data.
            for slug, clone_root in clones.items():
                for src_path, rel, is_dir in symlink_targets_for_clone(slug, Path(clone_root)):
                    key = rel.replace(os.sep, "/")
                    self.assertIn(key, inverse, f"missing inverse entry for {key}")
                    inv_slug, inv_src, inv_is_dir = inverse[key]
                    self.assertEqual(inv_slug, slug)
                    self.assertEqual(inv_src, src_path)
                    self.assertEqual(inv_is_dir, is_dir)

    def test_missing_clone_root_skipped(self):
        """If a clone path doesn't exist, inverse_mapping skips it silently."""
        clones = {"agentm": "/nonexistent/path/agentm"}
        result = im.inverse_mapping_for_clones(clones)
        self.assertEqual(result, {})


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.root = Path(self.td)
        self.clones = _make_source_clones(self.root / "clones")
        self.target = self.root / "target"
        self.target.mkdir()

    def tearDown(self):
        shutil.rmtree(self.td)

    def test_classify_empty_target(self):
        """Target with no `.claude/` returns empty list."""
        result = im.classify(self.target, self.clones)
        self.assertEqual(result, [])

    def test_classify_safe_to_migrate_file(self):
        """A byte-identical agent .md file → SAFE_TO_MIGRATE."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        result = im.classify(self.target, self.clones)
        self.assertEqual(len(result), 1)
        e = result[0]
        self.assertEqual(e["rel_path"], "agents/explorer.md")
        self.assertEqual(e["classification"], im.SAFE_TO_MIGRATE)
        self.assertEqual(e["source_clone"], "agentm")
        self.assertEqual(e["target_sha"], e["source_sha"])

    def test_classify_safe_to_migrate_dir_bundle(self):
        """A byte-identical skill dir bundle → SAFE_TO_MIGRATE."""
        _copy_from_source(self.clones, self.target, ["skills/wiki-author"])
        result = im.classify(self.target, self.clones)
        self.assertEqual(len(result), 1)
        e = result[0]
        self.assertEqual(e["rel_path"], "skills/wiki-author")
        self.assertEqual(e["classification"], im.SAFE_TO_MIGRATE)
        self.assertEqual(e["source_clone"], "agentm")
        self.assertTrue(e["is_dir"])

    def test_classify_already_symlinked(self):
        """A symlink under target → ALREADY_SYMLINKED."""
        # Build path under .claude/agents/foo.md as a symlink
        link_dir = self.target / ".claude" / "agents"
        link_dir.mkdir(parents=True)
        src = Path(self.clones["agentm"]) / "harness" / "agents" / "explorer.md"
        os.symlink(src, link_dir / "explorer.md")
        result = im.classify(self.target, self.clones)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["classification"], im.ALREADY_SYMLINKED)

    def test_classify_operator_edited(self):
        """File present in source mapping but SHA differs → OPERATOR_EDITED."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        # Mutate target content
        target_file = self.target / ".claude" / "agents" / "explorer.md"
        target_file.write_text("EDITED by operator\n")
        result = im.classify(self.target, self.clones)
        self.assertEqual(result[0]["classification"], im.OPERATOR_EDITED)
        self.assertNotEqual(result[0]["target_sha"], result[0]["source_sha"])

    def test_classify_unrecognized(self):
        """File with no source-mapping entry → UNRECOGNIZED."""
        ag_dir = self.target / ".claude" / "agents"
        ag_dir.mkdir(parents=True)
        (ag_dir / "operator-custom.md").write_text("my own thing\n")
        result = im.classify(self.target, self.clones)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["classification"], im.UNRECOGNIZED)
        self.assertIsNone(result[0]["source_clone"])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.root = Path(self.td)
        self.clones = _make_source_clones(self.root / "clones")
        self.target = self.root / "target"
        self.target.mkdir()

    def tearDown(self):
        shutil.rmtree(self.td)

    def test_apply_dry_run_preserves_target(self):
        """dry_run=True must not mutate filesystem or write record."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        before_exists = (self.target / ".claude" / "agents" / "explorer.md").exists()
        result = im.apply(self.target, source_clones=self.clones, dry_run=True)
        self.assertTrue(before_exists)
        # Still present
        self.assertTrue((self.target / ".claude" / "agents" / "explorer.md").exists())
        # No record
        self.assertFalse((self.target / im._RECORD_FILENAME).exists())
        self.assertTrue(result["dry_run"])
        # Action was planned
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["kind"], "safe_to_migrate")

    def test_apply_removes_safe_to_migrate_and_writes_record(self):
        """dry_run=False removes SAFE files + writes .agentm-migrate-record.json."""
        _copy_from_source(self.clones, self.target, [
            "agents/explorer.md",
            "skills/wiki-author",
        ])
        result = im.apply(self.target, source_clones=self.clones, dry_run=False)
        # Files gone
        self.assertFalse((self.target / ".claude" / "agents" / "explorer.md").exists())
        self.assertFalse((self.target / ".claude" / "skills" / "wiki-author").exists())
        # Record written
        rp = self.target / im._RECORD_FILENAME
        self.assertTrue(rp.exists())
        with rp.open() as f:
            record = json.load(f)
        self.assertEqual(record["version"], 1)
        rels = sorted(a["rel_path"] for a in record["actions"])
        self.assertEqual(rels, ["agents/explorer.md", "skills/wiki-author"])
        for a in record["actions"]:
            self.assertEqual(a["kind"], "safe_to_migrate")

    def test_apply_skips_operator_edited_without_force(self):
        """OPERATOR_EDITED + force=False → skip-with-warn, file remains."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        target_file = self.target / ".claude" / "agents" / "explorer.md"
        target_file.write_text("EDITED\n")
        result = im.apply(self.target, source_clones=self.clones, dry_run=False, force=False)
        # File still present
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_text(), "EDITED\n")
        # Skipped in summary
        self.assertEqual(result["skipped_force_needed"], 1)
        kinds = [a["kind"] for a in result["actions"]]
        self.assertIn("operator_edited_skipped", kinds)

    def test_apply_force_migrates_with_backup(self):
        """OPERATOR_EDITED + force=True → backs up to .agentm-migrate-backup, removes target."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        target_file = self.target / ".claude" / "agents" / "explorer.md"
        target_file.write_text("EDITED\n")
        result = im.apply(self.target, source_clones=self.clones, dry_run=False, force=True)
        # Original gone
        self.assertFalse(target_file.exists())
        # Backup exists
        backup_path = self.target / im._BACKUP_DIRNAME / "agents" / "explorer.md"
        self.assertTrue(backup_path.exists())
        self.assertEqual(backup_path.read_text(), "EDITED\n")
        # Action recorded
        kinds = [a["kind"] for a in result["actions"]]
        self.assertIn("force_migrated", kinds)

    def test_apply_record_merges_on_rerun(self):
        """Partial migration + second apply on new file → record merges actions."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        im.apply(self.target, source_clones=self.clones, dry_run=False)
        # Now add another safe file + re-run
        _copy_from_source(self.clones, self.target, ["commands/work.md"])
        im.apply(self.target, source_clones=self.clones, dry_run=False)
        # Both in record
        with (self.target / im._RECORD_FILENAME).open() as f:
            record = json.load(f)
        rels = sorted(a["rel_path"] for a in record["actions"])
        self.assertEqual(rels, ["agents/explorer.md", "commands/work.md"])

    def test_apply_records_registry_slug(self):
        """registry_slug arg is recorded in the .migrate-record.json."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        im.apply(self.target, source_clones=self.clones, dry_run=False,
                 registry_slug="myproject")
        with (self.target / im._RECORD_FILENAME).open() as f:
            record = json.load(f)
        self.assertEqual(record["registered_slug"], "myproject")


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.root = Path(self.td)
        self.clones = _make_source_clones(self.root / "clones")
        self.target = self.root / "target"
        self.target.mkdir()

    def tearDown(self):
        shutil.rmtree(self.td)

    def test_rollback_restores_safe_to_migrate(self):
        """rollback() restores byte-identical SAFE files from source clone."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        im.apply(self.target, source_clones=self.clones, dry_run=False)
        # File gone
        target_file = self.target / ".claude" / "agents" / "explorer.md"
        self.assertFalse(target_file.exists())
        # Rollback
        result = im.rollback(self.target)
        self.assertIn("agents/explorer.md", result["restored"])
        self.assertEqual(result["skipped"], [])
        # File back
        self.assertTrue(target_file.exists())
        # Record + backup gone
        self.assertFalse((self.target / im._RECORD_FILENAME).exists())

    def test_rollback_restores_force_migrated_from_backup(self):
        """rollback() restores OPERATOR_EDITED files from .agentm-migrate-backup."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        target_file = self.target / ".claude" / "agents" / "explorer.md"
        target_file.write_text("EDITED\n")
        im.apply(self.target, source_clones=self.clones, dry_run=False, force=True)
        self.assertFalse(target_file.exists())
        result = im.rollback(self.target)
        self.assertIn("agents/explorer.md", result["restored"])
        self.assertTrue(target_file.exists())
        # Restored content matches operator-edited version (not source)
        self.assertEqual(target_file.read_text(), "EDITED\n")
        # Backup dir gone
        self.assertFalse((self.target / im._BACKUP_DIRNAME).exists())

    def test_rollback_missing_record_raises(self):
        """rollback() with no record file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            im.rollback(self.target)

    def test_rollback_refuses_to_clobber_existing_safe_file(self):
        """Defect #4 regression (file-branch): rollback must NOT silently
        overwrite a file the operator re-staged at the dest between apply()
        and rollback(). The dir-branch refuses with `target dest exists;
        not overwriting` — file-branch must do the same."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        f = self.target / ".claude" / "agents" / "explorer.md"
        im.apply(self.target, source_clones=self.clones, dry_run=False)
        self.assertFalse(f.exists())
        # Operator re-stages a different file at the same path before rollback
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("OPERATOR RE-STAGED\n")
        result = im.rollback(self.target)
        # rollback must SKIP, not clobber
        self.assertIn("agents/explorer.md", [r for r, _ in result["skipped"]],
            "rollback should refuse to overwrite operator's re-staged file")
        # File content preserved
        self.assertEqual(f.read_text(), "OPERATOR RE-STAGED\n")

    def test_rollback_refuses_to_clobber_existing_force_migrated_file(self):
        """Defect #4 regression (force-branch + backup-collision scenario):
        after a backup_collision skip leaves the record+backup with a stale
        force_migrated action, rollback iterates that action and must not
        silently overwrite whatever file the operator now has at the dest."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        f = self.target / ".claude" / "agents" / "explorer.md"
        f.write_text("EDIT A\n")
        im.apply(self.target, source_clones=self.clones, dry_run=False, force=True)
        self.assertFalse(f.exists())
        # Operator re-stages a DIFFERENT file at the dest
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("EDIT B AFTER MIGRATION\n")
        result = im.rollback(self.target)
        # rollback must SKIP, not clobber
        self.assertIn("agents/explorer.md", [r for r, _ in result["skipped"]],
            "rollback should refuse to overwrite operator's re-staged file")
        # File content preserved
        self.assertEqual(f.read_text(), "EDIT B AFTER MIGRATION\n")

    def test_rollback_dir_bundle_safe(self):
        """rollback() restores a SAFE dir bundle (skill dir) from source."""
        _copy_from_source(self.clones, self.target, ["skills/wiki-author"])
        im.apply(self.target, source_clones=self.clones, dry_run=False)
        dest = self.target / ".claude" / "skills" / "wiki-author"
        self.assertFalse(dest.exists())
        result = im.rollback(self.target)
        self.assertIn("skills/wiki-author", result["restored"])
        self.assertTrue(dest.is_dir())
        self.assertTrue((dest / "SKILL.md").exists())


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.root = Path(self.td)
        self.clones = _make_source_clones(self.root / "clones")
        self.target = self.root / "target"
        self.target.mkdir()

    def tearDown(self):
        shutil.rmtree(self.td)

    def test_cleanup_refuses_with_operator_content(self):
        """If unrecognized operator content remains, cleanup refuses."""
        # Operator's own custom agent (UNRECOGNIZED)
        ag_dir = self.target / ".claude" / "agents"
        ag_dir.mkdir(parents=True)
        (ag_dir / "operator-custom.md").write_text("my own\n")
        result = im.cleanup(self.target)
        self.assertTrue(result["refused"])
        self.assertEqual(result["removed"], [])
        self.assertIn("agents/operator-custom.md", result["kept"])
        # File still present
        self.assertTrue((ag_dir / "operator-custom.md").exists())

    def test_cleanup_removes_empty_subdirs(self):
        """If install subdirs are empty (post-migration), cleanup removes them."""
        # Simulate post-migration: empty subdirs
        for sub in ("skills", "hooks", "agents", "commands"):
            (self.target / ".claude" / sub).mkdir(parents=True)
        result = im.cleanup(self.target)
        self.assertFalse(result["refused"])
        for sub in ("skills", "hooks", "agents", "commands"):
            self.assertFalse((self.target / ".claude" / sub).exists())

    def test_cleanup_no_claude_dir(self):
        """If no `.claude/` at all, cleanup is a no-op (not refused)."""
        result = im.cleanup(self.target)
        self.assertFalse(result["refused"])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["kept"], [])

    def test_cleanup_does_not_delete_operator_non_md_files(self):
        """Defect #1 regression: cleanup must NOT silently delete operator-
        dropped non-`.md` files under `.claude/{...}/`. The walker filters by
        known shapes for `classify()`; cleanup's verification must be
        shape-agnostic so operator content is sacred even if it doesn't
        match agentm file extensions."""
        skills_dir = self.target / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        operator_notes = skills_dir / "my-notes.txt"
        operator_notes.write_text("important operator notes\n")
        agents_dir = self.target / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        operator_helper = agents_dir / "helper.py"
        operator_helper.write_text("print('mine')\n")
        result = im.cleanup(self.target)
        self.assertTrue(result["refused"],
            "cleanup should refuse when operator non-.md files exist")
        self.assertTrue(operator_notes.exists(),
            "cleanup destroyed operator .txt file")
        self.assertTrue(operator_helper.exists(),
            "cleanup destroyed operator .py file")
        # And `kept` should report both
        self.assertIn("skills/my-notes.txt", result["kept"])
        self.assertIn("agents/helper.py", result["kept"])


class Sha256DirNoiseTests(unittest.TestCase):
    """Defect #2 regression: dir-bundle SHA must be stable across the
    `.DS_Store` / `.git/` / editor-swp dotfile noise the OS sprinkles into
    visited directories. Without this skip, every macOS user would see
    false OPERATOR_EDITED on dir bundles."""

    def test_sha256_dir_skips_dotfile_noise(self):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "bundle"
            b.mkdir()
            (b / "SKILL.md").write_text("skill\n")
            sha_clean = im._sha256_dir(b)
            # macOS Finder noise
            (b / ".DS_Store").write_bytes(b"\x00finder")
            # Editor swap file noise
            (b / ".SKILL.md.swp").write_bytes(b"swp")
            # Hidden subdir noise (e.g. __pycache__/.pyc)
            (b / ".git").mkdir()
            (b / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
            sha_with_noise = im._sha256_dir(b)
            self.assertEqual(sha_clean, sha_with_noise,
                "dotfile noise leaked into dir-bundle SHA — macOS users would "
                "see false OPERATOR_EDITED on every classify")


class ApplyForceCollisionTests(unittest.TestCase):
    """Defect #3 regression: re-running `apply --force` on the same rel_path
    must not silently overwrite the prior backup while the merge-actions
    de-dup keeps the original `target_sha_before` — that combination
    corrupts the rollback contract."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.root = Path(self.td)
        self.clones = _make_source_clones(self.root / "clones")
        self.target = self.root / "target"
        self.target.mkdir()

    def tearDown(self):
        shutil.rmtree(self.td)

    def test_apply_force_rerun_refuses_backup_collision(self):
        """Second force-apply with backup file already present at the same
        rel_path: record an `operator_edited_skipped` with
        `backup_collision: true`; do NOT overwrite the prior backup."""
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        f = self.target / ".claude" / "agents" / "explorer.md"
        # First edit + force-apply
        f.write_text("EDIT A\n")
        im.apply(self.target, source_clones=self.clones, dry_run=False, force=True)
        backup = self.target / im._BACKUP_DIRNAME / "agents" / "explorer.md"
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(), "EDIT A\n")
        # Second edit + force-apply — should refuse the collision
        _copy_from_source(self.clones, self.target, ["agents/explorer.md"])
        f.write_text("EDIT B\n")
        result = im.apply(self.target, source_clones=self.clones, dry_run=False, force=True)
        # Backup A is preserved (NOT overwritten with B)
        self.assertEqual(backup.read_text(), "EDIT A\n",
            "second force-apply silently overwrote the prior backup")
        # New entry recorded as operator_edited_skipped with collision flag
        with (self.target / im._RECORD_FILENAME).open() as fh:
            record = json.load(fh)
        # The actions list has the original force_migrated PLUS a new
        # operator_edited_skipped with backup_collision=true
        collision_actions = [
            a for a in record["actions"]
            if a.get("backup_collision") is True
        ]
        self.assertEqual(len(collision_actions), 1,
            "expected one backup_collision skip; record actions: " +
            json.dumps(record["actions"], indent=2))
        # And the original target file is still on disk (not destroyed)
        self.assertTrue(f.exists())
        self.assertEqual(f.read_text(), "EDIT B\n")
        # apply() summary reports the skip
        self.assertGreaterEqual(result["skipped_force_needed"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
