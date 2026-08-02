#!/usr/bin/env python3
"""Regression tests for the merge contract in
lib/install/python/install_state.py's `persist_install_state()`.

The defect these pin: persist used to rebuild `.agentm-config.json` from a
fixed allowlist of five keys it knew about, so every key written by the OTHER
writer of that file — `agentm_config.py`'s flat, dotted `plugins.*` /
`storage.*` families — was destroyed on every re-install. Observed live on
2026-08-02: `bash install.sh --hooks --update --scope user` silently removed
`plugins.autonomy.notify_enabled`, `.email_to`, `.email_smtp_url`,
`.email_from`, `storage.backend`, and `plugins.obsidian-vault.vault_path`.
The vault pair self-healed through harness_memory's V5-8 legacy migration;
the four autonomy keys had no safety net and were simply gone.

Note the key SHAPE these tests use: flat top-level strings with literal dots
in the name (`"plugins.autonomy.email_to"`), not nested objects. That is the
on-disk convention `agentm_config.py` writes, and it is why a fix reasoning
about `data["plugins"]` would not work.

Both surfaces are exercised: the Python API directly, and the `persist`
subcommand as a subprocess (the path `install.sh` actually takes).
`--agentm-path` points at an empty temp dir to force deterministic
release-mode detection, independent of the test host's real clone.

Run: python3 scripts/test_install_state_preserve.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_INSTALL_STATE = _HERE.parent / "lib" / "install" / "python" / "install_state.py"

_spec = importlib.util.spec_from_file_location("install_state_preserve_sut", _INSTALL_STATE)
assert _spec is not None and _spec.loader is not None
install_state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_state)

# The exact six keys the 2026-08-02 re-install destroyed, with representative
# values. `notify_enabled` is a bool on purpose — a preservation fix that
# stringifies or truthiness-filters values must fail here.
_LIVE_KEYS = {
    "plugins.obsidian-vault.vault_path": "/srv/vaults/Agent",
    "storage.backend": "vault",
    "plugins.autonomy.notify_enabled": True,
    "plugins.autonomy.email_to": "ops@example.com",
    "plugins.autonomy.email_smtp_url": "smtp://relay@localhost:587",
    "plugins.autonomy.email_from": "digest@example.com",
}


class _PrefixCase(unittest.TestCase):
    """Shared temp install prefix + config read/write helpers."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.prefix = self.root / "prefix"
        self.prefix.mkdir()
        # Empty dir → not a source clone → deterministic release mode.
        self.fake_agentm = self.root / "no-agentm"
        self.fake_agentm.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_config(self, data: dict, *, legacy: bool = False) -> Path:
        name = ".agentm-install-state.json" if legacy else ".agentm-config.json"
        path = self.prefix / name
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return path

    def _config(self) -> dict:
        return json.loads((self.prefix / ".agentm-config.json").read_text(encoding="utf-8"))

    def _persist(self, **kwargs):
        return install_state.persist_install_state(
            self.prefix, kwargs.pop("mode", "release"), kwargs.pop("source_clones", {}),
            kwargs.pop("harness_version", "v9.6.0"), **kwargs,
        )


class PluginNamespacedKeysSurviveTests(_PrefixCase):
    """The reported defect, pinned directly."""

    def test_all_six_live_keys_survive_re_persist(self) -> None:
        self._write_config({
            "schema_version": 2, "mode": "release", "source_clones": {},
            "installed_at": "2026-07-01T00:00:00Z", "harness_version": "v9.5.0",
            "vault_path": None, "installer_source": "/old/install.sh",
            **_LIVE_KEYS,
        })
        self._persist(harness_version="v9.6.0", installer_source="/new/install.sh")
        cfg = self._config()
        for key, value in _LIVE_KEYS.items():
            with self.subTest(key=key):
                self.assertIn(key, cfg, f"{key} was destroyed by re-persist")
                self.assertEqual(cfg[key], value)
                # Identical type, not merely ==; True must not become 1 or "true".
                self.assertIs(type(cfg[key]), type(value))

    def test_managed_keys_still_update_alongside(self) -> None:
        """Preservation must not turn into "never overwrite anything"."""
        self._write_config({
            "schema_version": 2, "mode": "release", "source_clones": {},
            "installed_at": "2026-07-01T00:00:00Z", "harness_version": "v9.5.0",
            "installer_source": "/old/install.sh",
            **_LIVE_KEYS,
        })
        self._persist(
            mode="source", source_clones={"agentm": "/srv/agentm"},
            harness_version="v9.6.0", installed_at="2026-08-02T12:00:00Z",
            installer_source="/new/install.sh",
        )
        cfg = self._config()
        self.assertEqual(cfg["mode"], "source")
        self.assertEqual(cfg["source_clones"], {"agentm": "/srv/agentm"})
        self.assertEqual(cfg["harness_version"], "v9.6.0")
        self.assertEqual(cfg["installed_at"], "2026-08-02T12:00:00Z")
        self.assertEqual(cfg["installer_source"], "/new/install.sh")
        self.assertEqual(cfg["schema_version"], 2)
        # ...and the unmanaged keys came through the same write untouched.
        self.assertEqual(cfg["plugins.autonomy.email_to"], _LIVE_KEYS["plugins.autonomy.email_to"])

    def test_repeated_persists_do_not_erode_keys(self) -> None:
        """Three round-trips — a slow leak would show up here, not in one pass."""
        self._write_config({"schema_version": 2, "mode": "release", **_LIVE_KEYS})
        for version in ("v9.6.0", "v9.6.1", "v9.7.0"):
            self._persist(harness_version=version)
        cfg = self._config()
        self.assertEqual({k: cfg.get(k) for k in _LIVE_KEYS}, _LIVE_KEYS)


class ArbitraryUnknownKeyTests(_PrefixCase):
    """The general rule: unrecognized keys are preserved, whatever they are.

    A fix that special-cased the `plugins.` / `storage.` prefixes would pass
    the tests above and fail every one of these — which is the point. The
    fixed list is what caused the defect.
    """

    def test_unknown_scalar_key_survives(self) -> None:
        self._write_config({"schema_version": 2, "some_future_key": "hello"})
        self._persist()
        self.assertEqual(self._config()["some_future_key"], "hello")

    def test_unknown_nested_and_list_values_survive_intact(self) -> None:
        payload = {
            "mcp.servers": {"a": {"cmd": "x", "args": ["--p", "1"], "on": False}},
            "some.list": [1, "two", {"three": None}],
        }
        self._write_config({"schema_version": 2, **payload})
        self._persist()
        cfg = self._config()
        self.assertEqual(cfg["mcp.servers"], payload["mcp.servers"])
        self.assertEqual(cfg["some.list"], payload["some.list"])

    def test_unknown_key_survives_legacy_filename_migration(self) -> None:
        """Migration reads the legacy file; its unmanaged keys must come along."""
        self._write_config({"version": 1, "vault_path": "/srv/v", **_LIVE_KEYS}, legacy=True)
        self._persist()
        cfg = self._config()
        self.assertEqual({k: cfg.get(k) for k in _LIVE_KEYS}, _LIVE_KEYS)
        self.assertEqual(cfg["vault_path"], "/srv/v")
        self.assertFalse((self.prefix / ".agentm-install-state.json").exists())


class RetiredKeyTests(_PrefixCase):
    """Retirement is explicit, because a merge makes silent omission impossible.

    Under the old rebuild, a key vanished the moment its write site went away.
    Under the merge it would live forever, so retiring one takes an entry in
    `_RETIRED_KEYS`. `version` — schema v1's field, replaced by
    `schema_version` in v4.5.1 — is the shipped case.
    """

    def test_legacy_version_key_is_dropped_on_migration(self) -> None:
        self._write_config({"version": 1, "vault_path": "/srv/v"}, legacy=True)
        self._persist()
        cfg = self._config()
        self.assertNotIn("version", cfg)
        self.assertEqual(cfg["schema_version"], 2)

    def test_retired_key_dropped_from_the_current_filename_too(self) -> None:
        """Not just the legacy path — a v1 remnant in the live file goes too."""
        self._write_config({"schema_version": 2, "version": 1, "storage.backend": "vault"})
        self._persist()
        cfg = self._config()
        self.assertNotIn("version", cfg)
        self.assertEqual(cfg["storage.backend"], "vault")

    def test_retired_set_is_the_only_deletion_mechanism(self) -> None:
        """Guard the contract itself: nothing gets retired by accident."""
        self.assertEqual(install_state._RETIRED_KEYS, frozenset({"version"}))


class ScrubbedKeyTests(_PrefixCase):
    """Value-scrubbing (drop garbage) stays separate from key-preservation."""

    def test_corrupt_state_mode_still_self_heals_while_unknowns_survive(self) -> None:
        self._write_config({
            "schema_version": 2, "state_mode": "bogus", "keep.me": "yes", **_LIVE_KEYS,
        })
        self._persist()
        cfg = self._config()
        self.assertNotIn("state_mode", cfg)
        self.assertEqual(cfg["keep.me"], "yes")
        self.assertEqual(cfg["plugins.autonomy.notify_enabled"], True)

    def test_valid_state_mode_survives_with_unknowns(self) -> None:
        self._write_config({"schema_version": 2, "state_mode": "local", **_LIVE_KEYS})
        self._persist()
        cfg = self._config()
        self.assertEqual(cfg["state_mode"], "local")
        self.assertEqual(cfg["storage.backend"], "vault")

    def test_explicit_state_mode_overrides_prior_without_touching_unknowns(self) -> None:
        self._write_config({"schema_version": 2, "state_mode": "local", **_LIVE_KEYS})
        self._persist(state_mode="vault")
        cfg = self._config()
        self.assertEqual(cfg["state_mode"], "vault")
        self.assertEqual(cfg["plugins.obsidian-vault.vault_path"],
                         _LIVE_KEYS["plugins.obsidian-vault.vault_path"])


class OptionalManagedFieldTests(_PrefixCase):
    """Omitting an optional managed field means "not managing it this run",
    not "assert it is absent" — so a prior value stands."""

    def test_fragments_and_installer_source_carry_forward_when_omitted(self) -> None:
        self._persist(installer_source="/srv/install.sh",
                      fragments=[{"path": "p", "sha256": "s"}])
        self._persist()  # the agentm-update path: neither flag supplied
        cfg = self._config()
        self.assertEqual(cfg["installer_source"], "/srv/install.sh")
        self.assertEqual(cfg["fragments"], [{"path": "p", "sha256": "s"}])

    def test_explicitly_supplied_fragments_replace_the_prior(self) -> None:
        self._persist(fragments=[{"path": "old", "sha256": "1"}])
        self._persist(fragments=[{"path": "new", "sha256": "2"}])
        self.assertEqual(self._config()["fragments"], [{"path": "new", "sha256": "2"}])

    def test_vault_path_null_when_never_set(self) -> None:
        self._persist()
        self.assertIsNone(self._config()["vault_path"])

    def test_prior_vault_path_survives(self) -> None:
        self._write_config({"schema_version": 2, "vault_path": "/srv/legacy-top-level"})
        self._persist()
        self.assertEqual(self._config()["vault_path"], "/srv/legacy-top-level")


class CorruptPriorTests(_PrefixCase):
    """A prior config that can't be merged degrades to a clean first-install
    write — the merge must not turn unreadable state into a crash."""

    def test_unparseable_prior_yields_clean_config(self) -> None:
        (self.prefix / ".agentm-config.json").write_text("{not json", encoding="utf-8")
        self._persist(harness_version="v9.6.0")
        cfg = self._config()
        self.assertEqual(cfg["schema_version"], 2)
        self.assertEqual(cfg["harness_version"], "v9.6.0")

    def test_non_dict_prior_yields_clean_config(self) -> None:
        (self.prefix / ".agentm-config.json").write_text("[1, 2, 3]", encoding="utf-8")
        self._persist(harness_version="v9.6.0")
        self.assertEqual(self._config()["harness_version"], "v9.6.0")


class CliPreservationTests(_PrefixCase):
    """The real install.sh path, driven as a subprocess."""

    def _run(self, *extra: str):
        return subprocess.run(
            [sys.executable, str(_INSTALL_STATE), "persist", str(self.prefix),
             "--harness-version", "v9.6.0",
             "--agentm-path", str(self.fake_agentm),
             *extra],
            capture_output=True, text=True,
        )

    def test_cli_persist_preserves_the_six_live_keys(self) -> None:
        self._write_config({
            "schema_version": 2, "mode": "release", "source_clones": {},
            "installed_at": "2026-07-01T00:00:00Z", "harness_version": "v9.5.0",
            "vault_path": None, **_LIVE_KEYS,
        })
        frags = self.root / "frags.json"
        frags.write_text(json.dumps([{"path": "p", "sha256": "s"}]), encoding="utf-8")
        r = self._run("--installer-source", "/srv/install.sh", "--fragments-file", str(frags))
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = self._config()
        self.assertEqual({k: cfg.get(k) for k in _LIVE_KEYS}, _LIVE_KEYS)
        self.assertEqual(cfg["harness_version"], "v9.6.0")
        self.assertEqual(cfg["fragments"], [{"path": "p", "sha256": "s"}])


if __name__ == "__main__":
    unittest.main()
