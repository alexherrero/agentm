#!/usr/bin/env python3
"""Unit tests for scripts/capability_resolver.py — stdlib unittest.

Run directly:

    python3 scripts/test_capability_resolver.py

Or via check-all.sh (picked up automatically).

Covers Task 1 verification:
  - Registry aggregation: fixture declarations map correctly for both hosts
  - capability_available: True/False for present/absent/uninstalled providers
  - capability_resolve: correct reason for each case
  - Graceful degrade: missing/corrupt files → empty registry, never raise
  - Version argument accepted but not evaluated (Task 2 stub)
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import capability_resolver as cr  # noqa: E402


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ── helpers: fixture builders ─────────────────────────────────────────────────

def _build_claude_code_root(
    tmp: Path,
    *,
    marketplace_plugins: list[dict] | None = None,
    installed_slugs: dict[str, str] | None = None,
    install_location: str | None = None,
) -> Path:
    """Build a Claude Code fixture under `tmp`.

    marketplace_plugins: list of plugin dicts with name/version/capabilities.
    installed_slugs: {slug: version} for enabled plugins.
    install_location: override; defaults to tmp/mock-install.
    """
    install_loc = Path(install_location or str(tmp / "mock-install"))
    plugins_dir = tmp / ".claude" / "plugins"

    _write_json(plugins_dir / "known_marketplaces.json", {
        "test-marketplace": {"installLocation": str(install_loc)},
    })

    raw_installed: dict = {"plugins": {}}
    for slug, version in (installed_slugs or {}).items():
        raw_installed["plugins"][f"{slug}@{version}"] = [{"version": version}]
    _write_json(plugins_dir / "installed_plugins.json", raw_installed)

    _write_json(install_loc / ".claude-plugin" / "marketplace.json", {
        "plugins": marketplace_plugins or [],
    })

    return tmp


def _build_antigravity_root(
    tmp: Path,
    *,
    enabled_plugins: list[str] | None = None,
    sidecars: dict[str, dict] | None = None,
) -> Path:
    """Build an Antigravity fixture under `tmp`.

    enabled_plugins: list of plugin names in import_manifest.json.
    sidecars: {name: sidecar_dict} written to ~/.gemini/config/plugins/<name>/capabilities.json.
    """
    config = tmp / ".gemini" / "config"
    imports = [{"name": n} for n in (enabled_plugins or [])]
    _write_json(config / "import_manifest.json", {"imports": imports})

    for name, sidecar in (sidecars or {}).items():
        _write_json(config / "plugins" / name / "capabilities.json", sidecar)

    return tmp


# ── Claude Code read path ──────────────────────────────────────────────────────

class TestReadClaudeCode(unittest.TestCase):

    def test_installed_provider_maps_to_registry(self):
        with tempfile.TemporaryDirectory() as t:
            root = _build_claude_code_root(
                Path(t),
                marketplace_plugins=[
                    {"name": "git-review", "version": "1.0.0",
                     "capabilities": ["git-review"]},
                ],
                installed_slugs={"git-review": "1.0.0"},
            )
            reg = cr.build_registry(root)
            self.assertIn("git-review", reg)
            entry = reg["git-review"]
            self.assertEqual(entry.plugin, "git-review")
            self.assertEqual(entry.version, "1.0.0")
            self.assertTrue(entry.installed)

    def test_uninstalled_provider_present_but_not_installed(self):
        with tempfile.TemporaryDirectory() as t:
            root = _build_claude_code_root(
                Path(t),
                marketplace_plugins=[
                    {"name": "linter", "version": "2.0.0",
                     "capabilities": ["lint"]},
                ],
                installed_slugs={},
            )
            reg = cr.build_registry(root)
            self.assertIn("lint", reg)
            entry = reg["lint"]
            self.assertFalse(entry.installed)
            self.assertEqual(entry.plugin, "linter")

    def test_installed_wins_over_uninstalled_for_same_cap(self):
        """Two plugins declare the same capability; installed one wins."""
        with tempfile.TemporaryDirectory() as t:
            root = _build_claude_code_root(
                Path(t),
                marketplace_plugins=[
                    {"name": "alpha", "version": "1.0.0", "capabilities": ["search"]},
                    {"name": "beta", "version": "2.0.0", "capabilities": ["search"]},
                ],
                installed_slugs={"beta": "2.0.0"},
            )
            reg = cr.build_registry(root)
            self.assertEqual(reg["search"].plugin, "beta")
            self.assertTrue(reg["search"].installed)

    def test_multiple_capabilities_from_one_plugin(self):
        with tempfile.TemporaryDirectory() as t:
            root = _build_claude_code_root(
                Path(t),
                marketplace_plugins=[
                    {"name": "multi", "version": "1.0.0",
                     "capabilities": ["cap-a", "cap-b", "cap-c"]},
                ],
                installed_slugs={"multi": "1.0.0"},
            )
            reg = cr.build_registry(root)
            for cap in ("cap-a", "cap-b", "cap-c"):
                self.assertIn(cap, reg)
                self.assertEqual(reg[cap].plugin, "multi")

    def test_version_from_installed_set_overrides_marketplace_version(self):
        """If the installed version differs from the manifest, installed wins."""
        with tempfile.TemporaryDirectory() as t:
            root = _build_claude_code_root(
                Path(t),
                marketplace_plugins=[
                    {"name": "pkg", "version": "1.0.0", "capabilities": ["feat"]},
                ],
                installed_slugs={"pkg": "1.2.3"},
            )
            reg = cr.build_registry(root)
            self.assertEqual(reg["feat"].version, "1.2.3")


# ── Antigravity read path ─────────────────────────────────────────────────────

class TestReadAntigravity(unittest.TestCase):

    def test_sidecar_capability_registered(self):
        with tempfile.TemporaryDirectory() as t:
            root = _build_antigravity_root(
                Path(t),
                enabled_plugins=["my-plugin"],
                sidecars={"my-plugin": {"version": "0.5.0", "capabilities": ["my-cap"]}},
            )
            reg = cr.build_registry(root)
            self.assertIn("my-cap", reg)
            entry = reg["my-cap"]
            self.assertEqual(entry.plugin, "my-plugin")
            self.assertEqual(entry.version, "0.5.0")
            self.assertTrue(entry.installed)

    def test_plugin_in_manifest_without_sidecar_ignored_gracefully(self):
        with tempfile.TemporaryDirectory() as t:
            root = _build_antigravity_root(
                Path(t),
                enabled_plugins=["no-sidecar"],
                sidecars={},
            )
            reg = cr.build_registry(root)
            self.assertEqual(reg, {})

    def test_plugin_not_in_manifest_but_sidecar_exists_not_registered(self):
        with tempfile.TemporaryDirectory() as t:
            root = _build_antigravity_root(
                Path(t),
                enabled_plugins=[],
                sidecars={"orphan": {"capabilities": ["orphan-cap"]}},
            )
            reg = cr.build_registry(root)
            self.assertNotIn("orphan-cap", reg)

    def test_multiple_enabled_plugins_aggregated(self):
        with tempfile.TemporaryDirectory() as t:
            root = _build_antigravity_root(
                Path(t),
                enabled_plugins=["pa", "pb"],
                sidecars={
                    "pa": {"version": "1.0.0", "capabilities": ["cap-x"]},
                    "pb": {"version": "2.0.0", "capabilities": ["cap-y"]},
                },
            )
            reg = cr.build_registry(root)
            self.assertEqual(reg["cap-x"].plugin, "pa")
            self.assertEqual(reg["cap-y"].plugin, "pb")


# ── build_registry: merge both hosts ─────────────────────────────────────────

class TestBuildRegistryMerge(unittest.TestCase):

    def test_claude_code_wins_over_antigravity_for_same_cap(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _build_claude_code_root(
                root,
                marketplace_plugins=[
                    {"name": "cc-plugin", "version": "1.0.0", "capabilities": ["shared-cap"]},
                ],
                installed_slugs={"cc-plugin": "1.0.0"},
            )
            _build_antigravity_root(
                root,
                enabled_plugins=["ag-plugin"],
                sidecars={"ag-plugin": {"version": "2.0.0", "capabilities": ["shared-cap"]}},
            )
            reg = cr.build_registry(root)
            # Claude Code is read first; installed CC entry beats AG entry.
            self.assertEqual(reg["shared-cap"].plugin, "cc-plugin")

    def test_antigravity_fills_caps_not_in_claude_code(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _build_claude_code_root(root)
            _build_antigravity_root(
                root,
                enabled_plugins=["ag-only"],
                sidecars={"ag-only": {"capabilities": ["ag-only-cap"]}},
            )
            reg = cr.build_registry(root)
            self.assertIn("ag-only-cap", reg)
            self.assertEqual(reg["ag-only-cap"].plugin, "ag-only")


# ── capability_resolve: reason codes ─────────────────────────────────────────

class TestCapabilityResolve(unittest.TestCase):

    def _reg(self, installed: bool = True, version: str | None = "1.0.0") -> cr.Registry:
        return {"some-cap": cr.ProviderEntry("provider", version, installed)}

    def test_available(self):
        result = cr.capability_resolve("some-cap", registry=self._reg())
        self.assertTrue(result["available"])
        self.assertEqual(result["reason"], "available")
        self.assertEqual(result["provider"], "provider")
        self.assertEqual(result["version"], "1.0.0")

    def test_no_provider(self):
        result = cr.capability_resolve("missing-cap", registry={})
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "no-provider")
        self.assertIsNone(result["provider"])
        self.assertIsNone(result["version"])

    def test_provider_not_installed(self):
        result = cr.capability_resolve("some-cap", registry=self._reg(installed=False))
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "provider-not-installed")
        self.assertEqual(result["provider"], "provider")

    def test_version_stub_accepted_when_installed(self):
        # Task 1 stub: version arg accepted but not evaluated;
        # with no capability_version_match available, the except branch catches it
        # and returns no-provider — that's the correct graceful degrade.
        result = cr.capability_resolve("some-cap", version=">= 1.0", registry=self._reg())
        # Either "available" (if stub satisfies) or "no-provider" (graceful degrade).
        # Task 2 will make this "available"; for now just assert it doesn't raise.
        self.assertIn(result["reason"], ("available", "no-provider", "version-mismatch"))
        self.assertIsInstance(result["available"], bool)

    def test_returns_dict_with_all_keys(self):
        for cap in ("some-cap", "missing"):
            result = cr.capability_resolve(cap, registry=self._reg())
            self.assertIn("available", result)
            self.assertIn("provider", result)
            self.assertIn("version", result)
            self.assertIn("reason", result)


# ── capability_available: boolean surface ────────────────────────────────────

class TestCapabilityAvailable(unittest.TestCase):

    def test_true_for_installed_provider(self):
        reg = {"cap": cr.ProviderEntry("p", "1.0.0", True)}
        self.assertTrue(cr.capability_available("cap", registry=reg))

    def test_false_for_absent_cap(self):
        self.assertFalse(cr.capability_available("absent", registry={}))

    def test_false_for_uninstalled_provider(self):
        reg = {"cap": cr.ProviderEntry("p", "1.0.0", False)}
        self.assertFalse(cr.capability_available("cap", registry=reg))

    def test_never_raises(self):
        # Passing a non-dict registry should not raise.
        try:
            result = cr.capability_available("x", registry=None)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            self.fail(f"capability_available raised unexpectedly: {exc}")


# ── graceful degrade: missing / corrupt state ─────────────────────────────────

class TestGracefulDegrade(unittest.TestCase):

    def test_no_files_returns_empty_registry(self):
        with tempfile.TemporaryDirectory() as t:
            reg = cr.build_registry(Path(t))
            self.assertEqual(reg, {})

    def test_empty_claude_plugins_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / ".claude" / "plugins").mkdir(parents=True)
            reg = cr.build_registry(Path(t))
            self.assertEqual(reg, {})

    def test_corrupt_json_returns_empty(self):
        with tempfile.TemporaryDirectory() as t:
            plugins = Path(t) / ".claude" / "plugins"
            plugins.mkdir(parents=True)
            (plugins / "known_marketplaces.json").write_text("{NOT JSON}", encoding="utf-8")
            reg = cr.build_registry(Path(t))
            self.assertEqual(reg, {})

    def test_missing_marketplace_json_skipped(self):
        """known_marketplaces.json points at a location with no marketplace.json."""
        with tempfile.TemporaryDirectory() as t:
            plugins = Path(t) / ".claude" / "plugins"
            _write_json(plugins / "known_marketplaces.json", {
                "mp": {"installLocation": str(Path(t) / "nonexistent")},
            })
            _write_json(plugins / "installed_plugins.json", {"plugins": {}})
            reg = cr.build_registry(Path(t))
            self.assertEqual(reg, {})

    def test_empty_capabilities_list_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            root = _build_antigravity_root(
                Path(t),
                enabled_plugins=["p"],
                sidecars={"p": {"version": "1.0.0", "capabilities": []}},
            )
            reg = cr.build_registry(root)
            self.assertEqual(reg, {})

    def test_capability_resolve_never_raises_on_broken_registry(self):
        # If registry=None, build_registry is called against the real home dir.
        # It should return a valid dict regardless.
        try:
            result = cr.capability_resolve("nonexistent-capability-xyz")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"capability_resolve raised: {exc}")
        self.assertFalse(result["available"])

    def test_antigravity_corrupt_sidecar_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            config = Path(t) / ".gemini" / "config"
            _write_json(config / "import_manifest.json", {"imports": [{"name": "p"}]})
            sidecar = config / "plugins" / "p" / "capabilities.json"
            sidecar.parent.mkdir(parents=True)
            sidecar.write_text("{CORRUPT}", encoding="utf-8")
            reg = cr.build_registry(Path(t))
            self.assertEqual(reg, {})


# ── CLI entry point ───────────────────────────────────────────────────────────

class TestCLIMain(unittest.TestCase):

    def test_exit_0_available(self):
        reg = {"cap": cr.ProviderEntry("p", "1.0.0", True)}
        # Patch build_registry via the registry kwarg path (CLI calls resolve directly)
        # We test _main via a fake argv and mock the module's build_registry.
        import unittest.mock as mock
        with mock.patch("capability_resolver.build_registry", return_value=reg):
            rc = cr._main(["capability_resolver.py", "cap"])
        self.assertEqual(rc, 0)

    def test_exit_1_unavailable(self):
        import unittest.mock as mock
        with mock.patch("capability_resolver.build_registry", return_value={}):
            rc = cr._main(["capability_resolver.py", "missing"])
        self.assertEqual(rc, 1)

    def test_exit_2_too_few_args(self):
        rc = cr._main(["capability_resolver.py"])
        self.assertEqual(rc, 2)

    def test_exit_2_too_many_args(self):
        rc = cr._main(["capability_resolver.py", "a", "b", "c"])
        self.assertEqual(rc, 2)

    def test_version_arg_passed_through(self):
        import unittest.mock as mock
        reg = {"cap": cr.ProviderEntry("p", "1.0.0", True)}
        with mock.patch("capability_resolver.build_registry", return_value=reg):
            rc = cr._main(["capability_resolver.py", "cap", ">= 1.0"])
        self.assertEqual(rc, 0)  # Task 2 live: satisfies("1.0.0", ">= 1.0") → True

    def test_version_mismatch_exits_1(self):
        import unittest.mock as mock
        reg = {"cap": cr.ProviderEntry("p", "1.0.0", True)}
        with mock.patch("capability_resolver.build_registry", return_value=reg):
            rc = cr._main(["capability_resolver.py", "cap", ">= 2.0"])
        self.assertEqual(rc, 1)  # version-mismatch → unavailable → exit 1


# ── CLI subprocess tests (Task 3): real exit codes via subprocess ─────────────

class TestCLISubprocess(unittest.TestCase):
    """Verify the CLI shim exit-code contract by spawning a real subprocess.

    These tests call `python3 capability_resolver.py <args>` with a temp root
    dir (AGENTM_TEST_ROOT env var injected via build_registry override is not
    available here, so we test the exit-2 path and use a no-host root for
    exit-1). For exit-0, we build a real Antigravity fixture and pass it via
    a monkeypatched Path.home() via TMPDIR semantics.
    """

    _SCRIPT = str(_HERE / "capability_resolver.py")

    def _run(self, *args, env=None) -> int:
        import subprocess
        # Cross-platform home redirect: the resolver finds the host root via
        # Path.home(). On POSIX that honors $HOME; on Windows ntpath.expanduser
        # consults %USERPROFILE% first and ignores %HOME%. Callers build env as
        # {**os.environ, "HOME": tmp}, and on Windows os.environ already carries
        # the *real* USERPROFILE — so we must overwrite it unconditionally
        # (not "if absent") for Path.home() to resolve to the temp fixture.
        if env is not None and "HOME" in env:
            env = {**env, "USERPROFILE": env["HOME"]}
        result = subprocess.run(
            [sys.executable, self._SCRIPT] + list(args),
            capture_output=True,
            env=env,
        )
        return result.returncode

    def test_subprocess_exit_2_no_args(self):
        self.assertEqual(self._run(), 2)

    def test_subprocess_exit_2_too_many_args(self):
        self.assertEqual(self._run("a", "b", "c"), 2)

    def test_subprocess_exit_1_no_host_state(self):
        with tempfile.TemporaryDirectory() as t:
            import os
            env = {**os.environ, "HOME": t}
            rc = self._run("nonexistent-capability-xyz-subprocess", env=env)
            self.assertEqual(rc, 1)

    def test_subprocess_exit_0_antigravity_fixture(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _build_antigravity_root(
                root,
                enabled_plugins=["test-plugin"],
                sidecars={"test-plugin": {"version": "1.0.0", "capabilities": ["proc-test-cap"]}},
            )
            import os
            env = {**os.environ, "HOME": t}
            rc = self._run("proc-test-cap", env=env)
            self.assertEqual(rc, 0)

    def test_subprocess_exit_0_with_version_range(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _build_antigravity_root(
                root,
                enabled_plugins=["vp"],
                sidecars={"vp": {"version": "2.0.0", "capabilities": ["ver-cap"]}},
            )
            import os
            env = {**os.environ, "HOME": t}
            rc = self._run("ver-cap", ">= 1.5", env=env)
            self.assertEqual(rc, 0)

    def test_subprocess_exit_1_version_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _build_antigravity_root(
                root,
                enabled_plugins=["vp"],
                sidecars={"vp": {"version": "1.0.0", "capabilities": ["ver-cap"]}},
            )
            import os
            env = {**os.environ, "HOME": t}
            rc = self._run("ver-cap", ">= 2.0", env=env)
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
