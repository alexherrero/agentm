#!/usr/bin/env python3
"""Unit tests for scripts/project_config.py — stdlib unittest, cross-platform.

Covers V4 #32 task 2: the enablement-block builder, the merge-writer that
preserves pre-existing project.json keys, operator-override recording,
is_registered, the write/load roundtrip, and the register() integration against
a fixture vault.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import detect_project as dp  # noqa: E402
import project_config as pc  # noqa: E402
import repo_registry  # noqa: E402


@contextlib.contextmanager
def no_vault_configured():
    """Resolve as if no vault existed, without reaching the operator's install.

    Popping `$MEMORY_VAULT_PATH` alone does not achieve that. `vault_path()` has
    a second resolution path — `$AGENTM_INSTALL_PREFIX/.agentm-config.json`,
    defaulting to `~/.claude` — so a test that pops only the env var still
    resolves the real vault, and `register()` then writes its throwaway fixture
    into the live repo registry under a `/var/folders/.../T/tmpXXXX/repo` path
    that is dead the moment the test exits. That churn re-dirties
    `Agent/_meta/repos.json` on every run, and since the daemon commits markdown
    only it sits uncommitted and holds `agentmd gate corpus-write` shut.

    Both variables have to be redirected for "no vault" to mean it. Use this
    rather than hand-rolling the pop; `check-registry-hygiene` fails the battery
    if a leak reaches the live registry anyway.
    """
    import harness_memory as hm
    old_vault = os.environ.get("MEMORY_VAULT_PATH")
    old_prefix = os.environ.get("AGENTM_INSTALL_PREFIX")
    with tempfile.TemporaryDirectory() as prefix:
        os.environ.pop("MEMORY_VAULT_PATH", None)
        os.environ["AGENTM_INSTALL_PREFIX"] = prefix
        hm._reset_warn_state()
        try:
            yield Path(prefix)
        finally:
            if old_vault is None:
                os.environ.pop("MEMORY_VAULT_PATH", None)
            else:
                os.environ["MEMORY_VAULT_PATH"] = old_vault
            if old_prefix is None:
                os.environ.pop("AGENTM_INSTALL_PREFIX", None)
            else:
                os.environ["AGENTM_INSTALL_PREFIX"] = old_prefix
            hm._reset_warn_state()


def _empty_proposal() -> dp.ProposedConfig:
    with tempfile.TemporaryDirectory() as td:
        return dp.detect(Path(td))  # empty dir -> propose, all default


class TestBuildAndMerge(unittest.TestCase):
    def test_build_block_shape(self):
        block = pc.build_enablement_block(_empty_proposal(), now="2026-05-29T00:00:00Z")
        self.assertEqual(block["type"], "coding")
        self.assertEqual(block["registered_via"], "auto-detect")
        self.assertEqual(block["registered_at"], "2026-05-29T00:00:00Z")
        self.assertEqual(block["operator_overrides"], [])
        self.assertIsNone(block["last_redetect_at"])
        # Every skill entry has the expected fields.
        for name, entry in block["skills"].items():
            self.assertEqual(set(entry), {"enabled", "auto_detected", "rationale", "rule_id", "operator_action"})
            self.assertTrue(entry["enabled"])

    def test_build_block_rejects_bypass(self):
        with tempfile.TemporaryDirectory() as td:
            # The agentm SOURCE-repo self-marker (post V5 dev-loop slim) is the
            # durable memory-engine pair: the harness/ spec tree + the
            # scripts/harness_memory.py state resolver. Pre-V5 this keyed on
            # harness/phases/, which the slim removed — see
            # detect_project.rule_harness.
            (Path(td) / "harness").mkdir(parents=True)
            (Path(td) / "scripts").mkdir(parents=True)
            (Path(td) / "scripts" / "harness_memory.py").write_text("# resolver\n")
            bypass = dp.detect(Path(td))
            # Detection must short-circuit to bypass on the self-marker…
            self.assertEqual(bypass.verdict, "bypass")
            # …and build must refuse to write config for a bypass proposal.
            with self.assertRaises(ValueError):
                pc.build_enablement_block(bypass)

    def test_merge_preserves_existing_keys(self):
        pj = {
            "vault_project": "demo",
            "github": {"owner": "x", "number": 9},
            "env": {"MEMORY_VAULT_PATH": "/v"},
        }
        block = pc.build_enablement_block(_empty_proposal())
        merged = pc.merge_enablement(pj, block)
        # Pre-existing keys survive verbatim.
        self.assertEqual(merged["vault_project"], "demo")
        self.assertEqual(merged["github"], {"owner": "x", "number": 9})
        self.assertEqual(merged["env"], {"MEMORY_VAULT_PATH": "/v"})
        # Enablement keys added.
        self.assertIn("skills", merged)
        self.assertEqual(merged["type"], "coding")
        # Input not mutated.
        self.assertNotIn("skills", pj)


class TestApplyOverride(unittest.TestCase):
    def test_disable_skill_records_override(self):
        block = pc.build_enablement_block(_empty_proposal())
        out = pc.apply_override(block, kind="skill", target="design", reason="not needed")
        self.assertFalse(out["skills"]["design"]["enabled"])
        self.assertEqual(out["skills"]["design"]["operator_action"], "disabled-at-registration")
        self.assertEqual(len(out["operator_overrides"]), 1)
        ov = out["operator_overrides"][0]
        self.assertEqual(ov["skill_or_hook"], "design")
        self.assertEqual(ov["reason"], "not needed")
        # Input not mutated.
        self.assertTrue(block["skills"]["design"]["enabled"])

    def test_disable_unknown_target_raises(self):
        block = pc.build_enablement_block(_empty_proposal())
        with self.assertRaises(KeyError):
            pc.apply_override(block, kind="skill", target="nonexistent-skill")


class TestIsRegistered(unittest.TestCase):
    def test_skills_block_means_registered(self):
        self.assertTrue(pc.is_registered({"skills": {"memory": {"enabled": True}}}))

    def test_empty_skills_not_registered(self):
        self.assertFalse(pc.is_registered({"skills": {}}))
        self.assertFalse(pc.is_registered({}))
        self.assertFalse(pc.is_registered(None))

    def test_registry_hit_means_registered(self):
        with tempfile.TemporaryDirectory() as td:
            from storage_device_local import DeviceLocalBackend
            backend = DeviceLocalBackend(root=Path(td))
            repo_registry.register_repo(backend, "demo", "/some/repo")
            self.assertTrue(pc.is_registered({}, backend=backend, slug="demo"))
            self.assertFalse(pc.is_registered({}, backend=backend, slug="other"))


class TestWriteLoadRoundtrip(unittest.TestCase):
    def test_write_then_load_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            vp = Path(td) / "desk/projects" / "demo"
            resolution = {"slug": "demo", "vault_path": vp, "project_root": Path(td), "layout": "new"}
            pj = {"vault_project": "demo", "github": {"number": 2}}
            block = pc.build_enablement_block(_empty_proposal(), now="2026-05-29T00:00:00Z")
            config = pc.merge_enablement(pj, block)
            path = pc.write_config(resolution, config)
            self.assertTrue(path.is_file())
            loaded = pc.load_project_json(resolution)
            self.assertEqual(loaded["vault_project"], "demo")
            self.assertIn("skills", loaded)
            # Second write byte-identical.
            before = path.read_bytes()
            pc.write_config(resolution, config)
            self.assertEqual(path.read_bytes(), before)

    def test_load_absent_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            vp = Path(td) / "desk/projects" / "demo"
            resolution = {"slug": "demo", "vault_path": vp, "project_root": Path(td), "layout": "new"}
            self.assertEqual(pc.load_project_json(resolution), {})


class TestRegisterIntegration(unittest.TestCase):
    def test_register_writes_block_and_registry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vault = root / "vault"
            (vault / "desk/projects" / "demo").mkdir(parents=True)
            repo = root / "repo"
            (repo / ".harness").mkdir(parents=True)
            (repo / ".harness" / "project.json").write_text(
                json.dumps({"vault_project": "demo"}), encoding="utf-8"
            )
            old_env = os.environ.get("MEMORY_VAULT_PATH")
            os.environ["MEMORY_VAULT_PATH"] = str(vault)
            try:
                # Patch select_backend to return the kernel VaultBackend so the
                # test works in CI without the obsidian-vault plugin (V5-6).
                from vault_backend_stub import VaultBackend
                vault_backend = VaultBackend(root=vault)
                with unittest.mock.patch(
                    "backend_selection.select_backend", return_value=vault_backend
                ):
                    config = pc.register(repo, registered_via="auto-detect")
            finally:
                if old_env is None:
                    os.environ.pop("MEMORY_VAULT_PATH", None)
                else:
                    os.environ["MEMORY_VAULT_PATH"] = old_env
            # ADR 0020 (reverses V5-3 DC-1): a synced backend routes the rich
            # project.json to <vault>/projects/<slug>/_harness/. The device-local
            # .harness/project.json stays the thin {vault_project} routing pointer.
            vault_pj = vault / "desk/projects" / "demo" / "_harness" / "project.json"
            self.assertTrue(vault_pj.is_file())
            data = json.loads(vault_pj.read_text(encoding="utf-8"))
            self.assertIn("skills", data)
            self.assertEqual(data["registered_via"], "auto-detect")
            # The vault config is keyed by slug (its directory) — the device-local
            # vault_project pointer is not duplicated into it.
            self.assertNotIn("vault_project", data)
            # Device-local file is untouched: still the thin slug pointer.
            self.assertEqual(
                json.loads(
                    (repo / ".harness" / "project.json").read_text(encoding="utf-8")
                ),
                {"vault_project": "demo"},
            )
            # repo registered — read back via same VaultBackend.
            slugs = [r.get("slug") for r in repo_registry.list_repos(vault_backend)]
            self.assertIn("demo", slugs)
            # returned config carries the merged enablement block.
            self.assertEqual(config["registered_via"], "auto-detect")
            self.assertIn("skills", config)

    def test_register_does_not_drop_github_env_under_local_mode(self):
        # Regression (adversarial review 2026-05-29): read_state_file honors
        # .project-mode=local (reads legacy), so write_config MUST write legacy
        # too — else it clobbers the vault file, dropping github/env.
        import harness_memory as hm
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vault = root / "vault"
            (vault / "desk/projects" / "demo" / "_harness").mkdir(parents=True)
            vault_pj = vault / "desk/projects" / "demo" / "_harness" / "project.json"
            vault_pj.write_text(
                json.dumps({
                    "vault_project": "demo",
                    "github": {"owner": "acme", "repo": "acme/demo", "number": 42},
                    "env": {"SECRET": "keep-me"},
                }),
                encoding="utf-8",
            )
            repo = root / "repo"
            (repo / ".harness").mkdir(parents=True)
            (repo / ".harness" / "project.json").write_text(
                json.dumps({"vault_project": "demo"}), encoding="utf-8"
            )
            # Signal local mode via the per-repo override marker (DC-2/DC-8 — the
            # in-vault `.project-mode` marker was removed in Hardening I task 3).
            (repo / ".harness" / ".project-mode").write_text("local", encoding="utf-8")
            old_env = os.environ.get("MEMORY_VAULT_PATH")
            os.environ["MEMORY_VAULT_PATH"] = str(vault)
            hm._reset_warn_state()
            try:
                # select_backend resolves vault here (MEMORY_VAULT_PATH set) and
                # post-R0.4 raises on plugin-less CI; this test exercises the
                # local-mode write routing, not backend selection — patch it.
                import unittest.mock
                import storage_device_local as _sdl
                with unittest.mock.patch(
                    "backend_selection.select_backend",
                    return_value=_sdl.DeviceLocalBackend(root / "device_local"),
                ):
                    pc.register(repo, registered_via="auto-detect")
            finally:
                if old_env is None:
                    os.environ.pop("MEMORY_VAULT_PATH", None)
                else:
                    os.environ["MEMORY_VAULT_PATH"] = old_env
            # The vault file (with github/env) must be intact — local-mode writes
            # to legacy, not over the vault.
            vault_data = json.loads(vault_pj.read_text(encoding="utf-8"))
            self.assertEqual(vault_data.get("github"), {"owner": "acme", "repo": "acme/demo", "number": 42})
            self.assertEqual(vault_data.get("env"), {"SECRET": "keep-me"})
            # The enablement block landed in the legacy file (the local-mode target).
            legacy_data = json.loads((repo / ".harness" / "project.json").read_text(encoding="utf-8"))
            self.assertIn("skills", legacy_data)
            self.assertEqual(legacy_data["vault_project"], "demo")


class TestRegisterNoVault(unittest.TestCase):
    """Hardening I #44 task 4: register() must complete with NO vault when the
    repo is in local state mode — the `--local-state` first-class entry point.
    The enablement block lands repo-local; the vault repo_registry step skips
    silently (no `ValueError`). Two local-mode signals, both on-host (DC-2/DC-8):
    the per-repo `.project-mode` marker and the device `state_mode` config."""

    def _make_repo(self, root: Path, slug: str) -> Path:
        repo = root / "repo"
        (repo / ".harness").mkdir(parents=True)
        (repo / ".harness" / "project.json").write_text(
            json.dumps({"vault_project": slug}), encoding="utf-8"
        )
        return repo

    def test_register_completes_with_repo_local_marker_no_vault(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._make_repo(root, "novault-marker")
            # Per-repo override marker signals local mode (DC-2) — no vault needed.
            (repo / ".harness" / ".project-mode").write_text("local", encoding="utf-8")
            with no_vault_configured():
                config = pc.register(repo, registered_via="auto-detect")
            # Enablement block landed in the repo-local project.json (no ValueError).
            legacy = json.loads((repo / ".harness" / "project.json").read_text(encoding="utf-8"))
            self.assertIn("skills", legacy)
            self.assertEqual(legacy["vault_project"], "novault-marker")
            self.assertEqual(config["vault_project"], "novault-marker")

    def test_register_completes_with_device_state_mode_no_vault(self) -> None:
        # The actual `install.sh --local-state` flow: device-level state_mode in
        # .agentm-config.json (no per-repo marker), no vault → register succeeds.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._make_repo(root, "novault-device")
            with no_vault_configured() as prefix:
                (prefix / ".agentm-config.json").write_text(
                    json.dumps({"schema_version": 2, "mode": "release",
                                "state_mode": "local"}),
                    encoding="utf-8",
                )
                config = pc.register(repo, registered_via="auto-detect")
            legacy = json.loads((repo / ".harness" / "project.json").read_text(encoding="utf-8"))
            self.assertIn("skills", legacy)
            self.assertEqual(legacy["vault_project"], "novault-device")
            self.assertEqual(config["vault_project"], "novault-device")


class TestDiffDetection(unittest.TestCase):
    """The /setup --redetect diff (DC-4 follow-up). Every fixture drives a REAL
    rule through `dp.detect` — the diff is only trustworthy if the thing it
    diffs is the detection engine's own output, not a hand-built stand-in."""

    def _fresh_config(self, repo: Path) -> dict:
        return pc.merge_enablement({}, pc.build_enablement_block(dp.detect(repo)))

    def test_rule_result_changed_since_last_detect(self):
        # The load-bearing case: config written when no rule matched, then the
        # repo grows a wiki/ dir so R-wiki fires. Re-detect must notice.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            config = self._fresh_config(repo)
            self.assertFalse(config["skills"]["diataxis-author"]["auto_detected"])

            (repo / "wiki").mkdir()
            diff = pc.diff_detection(config, dp.detect(repo))

            self.assertTrue(diff.has_changes())
            changes = {c.name: c for c in diff.changes}
            self.assertIn("diataxis-author", changes)
            self.assertEqual(changes["diataxis-author"].change, pc.CHANGE_NEWLY_DETECTED)
            self.assertEqual(changes["diataxis-author"].kind, "skill")
            self.assertEqual(changes["diataxis-author"].now["rule_id"], "R-wiki")
            self.assertIn("R-wiki", diff.matched_rules)
            # Nothing else moved — a diff that reports the whole config is noise.
            self.assertEqual(list(changes), ["diataxis-author"])

    def test_rule_that_stopped_matching_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "wiki").mkdir()
            config = self._fresh_config(repo)
            self.assertTrue(config["skills"]["diataxis-author"]["auto_detected"])

            # The wiki/ dir goes away; R-wiki no longer justifies the skill.
            (repo / "wiki").rmdir()
            diff = pc.diff_detection(config, dp.detect(repo))

            changes = {c.name: c for c in diff.changes}
            self.assertEqual(changes["diataxis-author"].change, pc.CHANGE_NO_LONGER_DETECTED)
            self.assertEqual(changes["diataxis-author"].was["rule_id"], "R-wiki")

    def test_operator_override_suppresses_the_suggestion(self):
        # The case that proves overrides are honored: the operator declined
        # pii-scrubber (.envrc is direnv, not a secret — the known R-pii false
        # positive), then a real .env file appears and R-pii fires. Re-detect
        # must NOT re-suggest it.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            config = self._fresh_config(repo)
            config = pc.apply_override(
                config,
                kind="skill",
                target="pii-scrubber",
                reason=".envrc is direnv, not a secret",
                now="2026-06-01T00:00:00Z",
            )

            (repo / ".env").write_text("TOKEN=x\n", encoding="utf-8")
            diff = pc.diff_detection(config, dp.detect(repo))

            # Not in changes — the operator already answered this.
            self.assertNotIn("pii-scrubber", {c.name for c in diff.changes})
            # But visibly suppressed, carrying the operator's own reason.
            suppressed = {c.name: c for c in diff.suppressed}
            self.assertIn("pii-scrubber", suppressed)
            entry = suppressed["pii-scrubber"]
            self.assertEqual(entry.change, pc.CHANGE_NEWLY_DETECTED)
            self.assertEqual(entry.suppressed_by["reason"], ".envrc is direnv, not a secret")
            self.assertEqual(entry.suppressed_by["at"], "2026-06-01T00:00:00Z")

            # And applying the diff leaves the decline standing.
            applied = pc.apply_redetect(config, diff, now="2026-06-02T00:00:00Z")
            self.assertFalse(applied["skills"]["pii-scrubber"]["enabled"])
            self.assertFalse(applied["skills"]["pii-scrubber"]["auto_detected"])
            self.assertEqual(
                applied["skills"]["pii-scrubber"]["operator_action"],
                "disabled-at-registration",
            )

            # The suppression is legible in the operator-facing render, too.
            text = pc.render_redetect_text(diff, repo_name="demo")
            self.assertIn("Suppressed", text)
            self.assertIn("pii-scrubber", text.split("Suppressed", 1)[1])
            self.assertIn(".envrc is direnv, not a secret", text)

    def test_override_recorded_only_as_operator_action_still_suppresses(self):
        # A hand-edited config may carry operator_action without the matching
        # operator_overrides entry. Honor either signal.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            config = self._fresh_config(repo)
            config["skills"]["pii-scrubber"]["operator_action"] = "disabled-at-registration"
            config["skills"]["pii-scrubber"]["enabled"] = False

            (repo / ".env").write_text("TOKEN=x\n", encoding="utf-8")
            diff = pc.diff_detection(config, dp.detect(repo))

            self.assertNotIn("pii-scrubber", {c.name for c in diff.changes})
            self.assertIn("pii-scrubber", {c.name for c in diff.suppressed})

    def test_no_changes_when_repo_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "wiki").mkdir()
            config = self._fresh_config(repo)
            diff = pc.diff_detection(config, dp.detect(repo))
            self.assertFalse(diff.has_changes())
            self.assertEqual(diff.changes, [])
            self.assertIn("No changes", pc.render_redetect_text(diff, repo_name="demo"))

    def test_apply_never_flips_enabled(self):
        # RD-2: a lapsed rule returns the target to its default rationale; it
        # does not disable it. Detection surfaces, it never gates (DC-7).
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "wiki").mkdir()
            config = self._fresh_config(repo)
            (repo / "wiki").rmdir()

            diff = pc.diff_detection(config, dp.detect(repo))
            applied = pc.apply_redetect(config, diff, now="2026-06-02T00:00:00Z")

            entry = applied["skills"]["diataxis-author"]
            self.assertTrue(entry["enabled"])          # untouched
            self.assertFalse(entry["auto_detected"])   # rationale refreshed
            self.assertIsNone(entry["rule_id"])
            self.assertEqual(applied["last_redetect_at"], "2026-06-02T00:00:00Z")
            # Input not mutated.
            self.assertTrue(config["skills"]["diataxis-author"]["auto_detected"])

    def test_apply_never_re_enables_a_disabled_target(self):
        # The footgun this carry-over exists to stop. `enabled: false` with no
        # recorded override is reachable — a hand-edited config, or any future
        # disable path that doesn't write operator_overrides — so the target is
        # NOT suppressed and DOES land in changes. Apply must still leave it off.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            config = self._fresh_config(repo)
            config["skills"]["diataxis-author"]["enabled"] = False
            self.assertIsNone(config["skills"]["diataxis-author"]["operator_action"])

            (repo / "wiki").mkdir()  # R-wiki now fires for this very target
            diff = pc.diff_detection(config, dp.detect(repo))
            # Not suppressed — nothing recorded a decline.
            self.assertIn("diataxis-author", {c.name for c in diff.changes})
            # The fresh proposal says enabled (DC-7 default-all-enabled)…
            self.assertTrue(dp.detect(repo).to_dict()["skills"]["diataxis-author"]["enabled"])

            applied = pc.apply_redetect(config, diff)
            # …and apply must NOT let that overwrite the operator's off switch.
            self.assertFalse(applied["skills"]["diataxis-author"]["enabled"])
            # The rationale still refreshed — that half is detection's to own.
            self.assertTrue(applied["skills"]["diataxis-author"]["auto_detected"])
            self.assertEqual(applied["skills"]["diataxis-author"]["rule_id"], "R-wiki")

    def test_retired_target_reported_even_when_overridden(self):
        # RD-3: an override suppresses enablement SUGGESTIONS, not inventory
        # facts. evidence-tracker was genuinely retired in the V5 dev-loop slim;
        # a stale entry for it is housekeeping the operator should still see.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            config = self._fresh_config(repo)
            config["hooks"]["evidence-tracker"] = {
                "enabled": False,
                "auto_detected": True,
                "rationale": "retired hook",
                "rule_id": "R-tests",
                "operator_action": "disabled-at-registration",
            }
            diff = pc.diff_detection(config, dp.detect(repo))

            changes = {c.name: c for c in diff.changes}
            self.assertEqual(changes["evidence-tracker"].change, pc.CHANGE_RETIRED_TARGET)
            self.assertNotIn("evidence-tracker", {c.name for c in diff.suppressed})
            # Applying drops the dead entry.
            applied = pc.apply_redetect(config, diff)
            self.assertNotIn("evidence-tracker", applied["hooks"])

    def test_new_enableable_target_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            config = self._fresh_config(repo)
            del config["skills"]["design"]  # config predates the enableable
            diff = pc.diff_detection(config, dp.detect(repo))

            changes = {c.name: c for c in diff.changes}
            self.assertEqual(changes["design"].change, pc.CHANGE_NEW_TARGET)
            applied = pc.apply_redetect(config, diff)
            self.assertIn("design", applied["skills"])
            self.assertIsNone(applied["skills"]["design"]["operator_action"])

    def test_diff_rejects_a_bypass_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            config = self._fresh_config(repo)
            (repo / "harness").mkdir()
            (repo / "scripts").mkdir()
            (repo / "scripts" / "harness_memory.py").write_text("# resolver\n", encoding="utf-8")
            bypass = dp.detect(repo)
            self.assertEqual(bypass.verdict, "bypass")
            with self.assertRaises(ValueError):
                pc.diff_detection(config, bypass)

    def test_json_shape_is_serializable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            config = self._fresh_config(repo)
            (repo / "wiki").mkdir()
            payload = pc.diff_detection(config, dp.detect(repo)).to_dict()
            round_tripped = json.loads(json.dumps(payload))
            self.assertTrue(round_tripped["has_changes"])
            self.assertEqual(round_tripped["changes"][0]["name"], "diataxis-author")
            self.assertIn("proposal", round_tripped["changes"][0])


class TestRedetectIntegration(unittest.TestCase):
    """End-to-end re-detect against a repo-local (vault-less) project."""

    def _local_repo(self, root: Path, slug: str) -> Path:
        repo = root / "repo"
        (repo / ".harness").mkdir(parents=True)
        (repo / ".harness" / "project.json").write_text(
            json.dumps({"vault_project": slug}), encoding="utf-8"
        )
        (repo / ".harness" / ".project-mode").write_text("local", encoding="utf-8")
        return repo

    def _run(self, fn):
        """Run `fn` with no vault configured (repo-local state mode)."""
        with no_vault_configured():
            return fn()

    def test_surface_then_apply_lifecycle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._local_repo(root, "redetect-demo")
            pj = repo / ".harness" / "project.json"

            self._run(lambda: pc.register(repo, registered_via="auto-detect"))
            self.assertIsNone(json.loads(pj.read_text(encoding="utf-8"))["last_redetect_at"])

            # The repo grows a wiki/ dir after registration.
            (repo / "wiki").mkdir()

            # --dry-run: reports the change, touches nothing at all.
            before = pj.read_bytes()
            diff, _ = self._run(lambda: pc.redetect(repo, dry_run=True))
            self.assertTrue(diff.has_changes())
            self.assertEqual(pj.read_bytes(), before)

            # Default run: surfaces the diff, stamps last_redetect_at, and
            # leaves the enablement block exactly as the operator left it.
            diff, config = self._run(lambda: pc.redetect(repo))
            self.assertTrue(diff.has_changes())
            on_disk = json.loads(pj.read_text(encoding="utf-8"))
            self.assertIsNotNone(on_disk["last_redetect_at"])
            self.assertEqual(on_disk["last_redetect_at"], config["last_redetect_at"])
            self.assertFalse(on_disk["skills"]["diataxis-author"]["auto_detected"])
            self.assertIsNone(on_disk["skills"]["diataxis-author"]["rule_id"])
            # The routing pointer survives the write.
            self.assertEqual(on_disk["vault_project"], "redetect-demo")

            # --apply: now the rationale lands.
            self._run(lambda: pc.redetect(repo, apply=True))
            on_disk = json.loads(pj.read_text(encoding="utf-8"))
            self.assertTrue(on_disk["skills"]["diataxis-author"]["auto_detected"])
            self.assertEqual(on_disk["skills"]["diataxis-author"]["rule_id"], "R-wiki")
            self.assertTrue(on_disk["skills"]["diataxis-author"]["enabled"])

            # And a re-run is clean — the diff converges.
            diff, _ = self._run(lambda: pc.redetect(repo))
            self.assertFalse(diff.has_changes())

    def test_redetect_on_unregistered_repo_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._local_repo(root, "never-registered")
            with self.assertRaises(pc.NotRegisteredError):
                self._run(lambda: pc.redetect(repo))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._local_repo(root, "redetect-cli")

            # 2 — nothing to diff against yet.
            self.assertEqual(self._run(lambda: pc.main(["redetect", str(repo)])), 2)

            self._run(lambda: pc.register(repo, registered_via="auto-detect"))
            # 0 — config still matches the repo.
            self.assertEqual(self._run(lambda: pc.main(["redetect", str(repo)])), 0)

            (repo / "wiki").mkdir()
            # 1 — changes surfaced.
            self.assertEqual(
                self._run(lambda: pc.main(["redetect", str(repo), "--format", "json"])), 1
            )


class TestShouldNudgeGit(unittest.TestCase):
    def test_dotgit_file_worktree_counts_as_git(self):
        # A git worktree/submodule has `.git` as a FILE (`gitdir: …`), not a dir.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n", encoding="utf-8")
            # Not a harness source, no marker, no vault registration -> nudge.
            old_env = os.environ.get("MEMORY_VAULT_PATH")
            os.environ.pop("MEMORY_VAULT_PATH", None)
            try:
                rc = pc.main(["should-nudge", str(repo)])
            finally:
                if old_env is not None:
                    os.environ["MEMORY_VAULT_PATH"] = old_env
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
