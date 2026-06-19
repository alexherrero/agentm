#!/usr/bin/env python3
"""agentm_config — read/write fields in `~/.claude/.agentm-config.json`.

The on-device source of truth for the agentm install. V5-7 moved vault_path
from the kernel's flat `vault_path` key to the plugin-namespaced key
`plugins.obsidian-vault.vault_path` (stored flat with dots, same convention as
`storage.backend`). Hardening I #44 added `state_mode` (local|vault) — the
on-host run mode (DC-8): `.agentm-config.json` is the single config file, the
vault holds data only. This CLI is the operator-facing way to set / read / unset
those fields without re-running the full installer.

Operations:

    agentm_config.py --vault-path <path>   # write plugins.obsidian-vault.vault_path
                                            #   + storage.backend=vault (validates dir exists)
    agentm_config.py --state-mode local    # write state_mode (local|vault); opt a
                                            #   vault-less machine into repo-local state
    agentm_config.py --storage-backend <name>  # write storage.backend (the selected
                                            #   storage backend protocol name; V5-1 part 5)
    agentm_config.py --get vault_path      # read plugins.obsidian-vault.vault_path (with
                                            #   legacy vault_path fallback); rc=0 if present
    agentm_config.py --list                # dump full config as JSON
    agentm_config.py --unset vault_path    # clear plugins.obsidian-vault.vault_path field

Common flags:

    --install-prefix <path>   # override default; honored before $AGENTM_INSTALL_PREFIX
                                env which is honored before ~/.claude

Exit codes:

    0   success (operation completed; --get found the field)
    1   silent graceful-skip (--get field absent, --list config missing, --unset
        of a missing field) — no stderr noise
    2   user error (invalid path for --vault-path, missing required arg)

Atomic writes via tmp+os.replace() per the established convention from
install_state.py + V4 #26's safe_write_replace_style().

Stdlib-only per ADR 0001. Per v4.5.1 task 3.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional


_CONFIG_FILENAME = ".agentm-config.json"
_SCHEMA_VERSION = 2
_STATE_MODES = ("local", "backend", "vault")  # "vault" is a deprecated alias; normalized to "backend" at write time

#: The literal flat key for the selected storage backend protocol name (V5-1
#: part 5). Flat (with a dot in the name, not a nested object) so it round-trips
#: through the existing `--get`/`--unset` field lookup, and named to match the
#: V5-7 config-model target so there is no later rename.
_STORAGE_BACKEND_KEY = "storage.backend"

#: Plugin-namespaced vault path key (V5-7). Stored flat with dots — same
#: convention as _STORAGE_BACKEND_KEY — so it round-trips through --get/--unset.
#: Must match harness_memory._PLUGIN_VAULT_PATH_KEY exactly.
_PLUGIN_VAULT_PATH_KEY = "plugins.obsidian-vault.vault_path"


def _resolve_install_prefix(cli_arg: Optional[str] = None) -> Path:
    """Resolve install prefix: --install-prefix → $AGENTM_INSTALL_PREFIX → ~/.claude."""
    if cli_arg:
        return Path(os.path.expanduser(cli_arg))
    env = os.environ.get("AGENTM_INSTALL_PREFIX", "").strip()
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".claude"


def _config_path(prefix: Path) -> Path:
    return prefix / _CONFIG_FILENAME


def _read_config(prefix: Path) -> Optional[dict]:
    """Return parsed config dict or None if file missing/malformed."""
    path = _config_path(prefix)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_config(prefix: Path, config: dict) -> Path:
    """Write config atomically; ensure schema_version present + bumped to v2."""
    prefix.mkdir(parents=True, exist_ok=True)
    config = dict(config)
    config["schema_version"] = _SCHEMA_VERSION
    path = _config_path(prefix)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


# -----------------------------------------------------------------------------
# Operations
# -----------------------------------------------------------------------------

def cmd_set_vault_path(prefix: Path, raw_path: str) -> int:
    """Write vault path to the plugin-namespaced key + set storage.backend=vault.

    Writes `plugins.obsidian-vault.vault_path` (V5-7) and `storage.backend=vault`
    so the selection resolver sees an explicit backend. Validates the target is an
    existing directory. Idempotent: silent no-op when the value is unchanged.
    """
    candidate = Path(os.path.expanduser(raw_path)).resolve()
    if not candidate.is_dir():
        print(
            f"[agentm_config] refusing to set vault_path: {candidate} is not an existing directory",
            file=sys.stderr,
        )
        return 2
    config = _read_config(prefix) or {}
    if config.get(_PLUGIN_VAULT_PATH_KEY) == str(candidate) and config.get(_STORAGE_BACKEND_KEY) == "vault":
        # Idempotent — silent no-op when both values are already correct.
        return 0
    config[_PLUGIN_VAULT_PATH_KEY] = str(candidate)
    config[_STORAGE_BACKEND_KEY] = "vault"
    written = _write_config(prefix, config)
    print(f"{_PLUGIN_VAULT_PATH_KEY} = {candidate}")
    print(f"(written to {written})", file=sys.stderr)
    return 0


def cmd_set_state_mode(prefix: Path, mode: str) -> int:
    """Set the device-level `state_mode` field (the on-host run mode; DC-8).

    Accepts 'local' or 'backend'. 'vault' is a deprecated alias for 'backend'
    that is accepted for backward compat and normalized at write time (LC-5).
    This is the post-install / `/setup` way to opt a machine into repo-local
    (vault-less) harness state without re-running the full installer. Idempotent:
    a silent no-op when the value is already set. The repo-local
    `<repo>/.harness/.project-mode` marker stays the higher-precedence per-repo
    override (DC-2).
    """
    if mode not in _STATE_MODES:
        print(
            f"[agentm_config] refusing to set state_mode: {mode!r} is not 'local', 'backend', or 'vault'",
            file=sys.stderr,
        )
        return 2
    # LC-5: normalize deprecated "vault" alias to "backend" at write time.
    if mode == "vault":
        mode = "backend"
    config = _read_config(prefix) or {}
    if config.get("state_mode") == mode:
        # Idempotent — silent no-op when value unchanged.
        return 0
    config["state_mode"] = mode
    written = _write_config(prefix, config)
    print(f"state_mode = {mode}")
    print(f"(written to {written})", file=sys.stderr)
    return 0


def cmd_set_storage_backend(prefix: Path, name: str) -> int:
    """Set the device-level `storage.backend` field — the selected backend protocol name (V5-1 part 5).

    Validates a **non-empty** string only — deliberately NOT against the backend
    registry. The fail-loud philosophy of the selection resolver
    (`backend_selection.py`) requires being able to *configure* a backend whose
    plugin is not yet installed: the loud refusal happens at selection time (the
    resolver raises an install-the-plugin error), never here, and never as a
    silent demotion. Stored under the literal flat key `"storage.backend"` so it
    round-trips through `--get`/`--unset`. Idempotent: a silent no-op when the
    value is already set.
    """
    backend = name.strip()
    if not backend:
        print(
            "[agentm_config] refusing to set storage.backend: name must be a non-empty string",
            file=sys.stderr,
        )
        return 2
    config = _read_config(prefix) or {}
    if config.get(_STORAGE_BACKEND_KEY) == backend:
        # Idempotent — silent no-op when value unchanged.
        return 0
    config[_STORAGE_BACKEND_KEY] = backend
    written = _write_config(prefix, config)
    print(f"storage.backend = {backend}")
    print(f"(written to {written})", file=sys.stderr)
    return 0


def cmd_get(prefix: Path, field: str) -> int:
    """Read a single field; rc=0 if present, rc=1 silent if absent.

    For `vault_path`: checks `plugins.obsidian-vault.vault_path` first (V5-7
    plugin-namespaced key), then falls back to the legacy flat `vault_path` key,
    so shell scripts that call `--get vault_path` work before and after migration.
    """
    config = _read_config(prefix)
    if not config:
        return 1
    if field == "vault_path":  # CLI field name — reads plugin key (legacy fallback)
        value = config.get(_PLUGIN_VAULT_PATH_KEY) or config.get("vault_path")  # legacy fallback
    else:
        value = config.get(field)
    if value is None:
        return 1
    if isinstance(value, (dict, list)):
        print(json.dumps(value))
    else:
        print(value)
    return 0


def cmd_list(prefix: Path) -> int:
    """Dump the full config as JSON. rc=1 silent if config missing."""
    config = _read_config(prefix)
    if config is None:
        return 1
    print(json.dumps(config, indent=2))
    return 0


def cmd_unset(prefix: Path, field: str) -> int:
    """Clear a field. rc=0 if removed; rc=1 silent if config or field missing.

    For `vault_path`: removes `plugins.obsidian-vault.vault_path` (V5-7) AND the
    legacy flat `vault_path` key if present, so a single `--unset vault_path`
    fully clears the vault configuration regardless of which key format is in use.
    Returns rc=0 if either key was removed; rc=1 only when neither is present.
    """
    config = _read_config(prefix)
    if not config:
        return 1
    if field == "schema_version":
        # schema_version is structural — refuse to remove via --unset.
        print(
            "[agentm_config] refusing to --unset schema_version (structural field)",
            file=sys.stderr,
        )
        return 2
    if field == "vault_path":  # CLI field name — clears plugin + legacy keys
        removed = False
        if _PLUGIN_VAULT_PATH_KEY in config:
            del config[_PLUGIN_VAULT_PATH_KEY]
            removed = True
        if "vault_path" in config:  # legacy key — remove both on unset
            del config["vault_path"]  # legacy key
            removed = True
        if not removed:
            return 1
        _write_config(prefix, config)
        return 0
    if field not in config:
        return 1
    del config[field]
    _write_config(prefix, config)
    return 0


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read/write fields in ~/.claude/.agentm-config.json.",
    )
    parser.add_argument(
        "--install-prefix",
        default=None,
        help="override install prefix; default: $AGENTM_INSTALL_PREFIX or ~/.claude",
    )
    op = parser.add_mutually_exclusive_group(required=True)
    op.add_argument("--vault-path", metavar="PATH", help="set vault_path field")
    op.add_argument("--state-mode", metavar="MODE", choices=_STATE_MODES,
                    help="set state_mode field (how harness state is stored: local|backend; 'vault' is a deprecated alias for 'backend')")
    op.add_argument("--storage-backend", metavar="NAME",
                    help="set storage.backend field (the selected storage backend protocol name)")
    op.add_argument("--get", metavar="FIELD", help="read single field to stdout")
    op.add_argument("--list", action="store_true", help="dump full config as JSON")
    op.add_argument("--unset", metavar="FIELD", help="clear a field")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    prefix = _resolve_install_prefix(args.install_prefix)

    if args.vault_path is not None:
        return cmd_set_vault_path(prefix, args.vault_path)
    if args.state_mode is not None:
        return cmd_set_state_mode(prefix, args.state_mode)
    if args.storage_backend is not None:
        return cmd_set_storage_backend(prefix, args.storage_backend)
    if args.get is not None:
        return cmd_get(prefix, args.get)
    if args.list:
        return cmd_list(prefix)
    if args.unset is not None:
        return cmd_unset(prefix, args.unset)
    # argparse should prevent this branch via required=True.
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
