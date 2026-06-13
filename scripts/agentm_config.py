#!/usr/bin/env python3
"""agentm_config — read/write fields in `~/.claude/.agentm-config.json`.

The on-device source of truth for the agentm install. Schema v2 added
`vault_path` as the top-level field that backs `harness_memory.py::vault_path()`
when `$MEMORY_VAULT_PATH` env is unset. Hardening I #44 added `state_mode`
(local|vault) — the on-host run mode (DC-8): `.agentm-config.json` is the single
config file, the vault holds data only. This CLI is the operator-facing way to
set / read / unset those fields without re-running the full installer.

Operations:

    agentm_config.py --vault-path <path>   # write vault_path (validates dir exists)
    agentm_config.py --state-mode local    # write state_mode (local|vault); opt a
                                            #   vault-less machine into repo-local state
    agentm_config.py --storage-backend <name>  # write storage.backend (the selected
                                            #   storage backend protocol name; V5-1 part 5)
    agentm_config.py --get vault_path      # read single field; rc=0 if present, rc=1 if absent
    agentm_config.py --list                # dump full config as JSON
    agentm_config.py --unset vault_path    # clear a single field

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
_STATE_MODES = ("local", "vault")

#: The literal flat key for the selected storage backend protocol name (V5-1
#: part 5). Flat (with a dot in the name, not a nested object) so it round-trips
#: through the existing `--get`/`--unset` field lookup, and named to match the
#: V5-7 config-model target so there is no later rename.
_STORAGE_BACKEND_KEY = "storage.backend"


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
    """Set vault_path field. Validates that the target is an existing directory."""
    candidate = Path(os.path.expanduser(raw_path)).resolve()
    if not candidate.is_dir():
        print(
            f"[agentm_config] refusing to set vault_path: {candidate} is not an existing directory",
            file=sys.stderr,
        )
        return 2
    config = _read_config(prefix) or {}
    existing = config.get("vault_path")
    if existing == str(candidate):
        # Idempotent — silent no-op when value unchanged.
        return 0
    config["vault_path"] = str(candidate)
    written = _write_config(prefix, config)
    print(f"vault_path = {candidate}")
    print(f"(written to {written})", file=sys.stderr)
    return 0


def cmd_set_state_mode(prefix: Path, mode: str) -> int:
    """Set the device-level `state_mode` field (the on-host run mode; DC-8).

    Accepts 'local' or 'vault' only. This is the post-install / `/setup` way to
    opt a machine into repo-local (vault-less) harness state — or back to
    vault-resident — without re-running the full installer. Idempotent: a silent
    no-op when the value is already set. The repo-local `<repo>/.harness/.project-mode`
    marker stays the higher-precedence per-repo override (DC-2).
    """
    if mode not in _STATE_MODES:
        print(
            f"[agentm_config] refusing to set state_mode: {mode!r} is not 'local' or 'vault'",
            file=sys.stderr,
        )
        return 2
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
    """Read a single field; rc=0 if present, rc=1 silent if absent."""
    config = _read_config(prefix)
    if not config:
        return 1
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
    """Clear a field. rc=0 if removed; rc=1 silent if config or field missing."""
    config = _read_config(prefix)
    if not config:
        return 1
    if field not in config:
        return 1
    if field == "schema_version":
        # schema_version is structural — refuse to remove via --unset.
        print(
            "[agentm_config] refusing to --unset schema_version (structural field)",
            file=sys.stderr,
        )
        return 2
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
                    help="set state_mode field (how harness state is stored: local|vault)")
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
