#!/usr/bin/env python3
"""install_state — probe + persist install mode (source vs release).

Used by `install.sh` + `install.ps1` to detect whether the operator has a
source-clone canonical path for agentm. The decision drives the install
dispatch (V4 #30 plan #22 tasks 4-5):

  - Source mode  → symlink the customizations subset from the agentm source
                   clone into the install prefix (skill dirs, agent .md,
                   command .md, hook scripts, hook .py helpers — per DC-7).
  - Release mode → copy customizations from the release tree into the
                   install prefix.

Canonical clone path checked:

  ~/Antigravity/agentm/    + has .git/  + has harness/  → agentm source present

The probe is **silent** — emits nothing to stdout except the JSON persist
target. Operator sees no prompt; the install dispatches based on the
detected mode without ceremony (per FOLLOWUPS locked semantics 2026-05-27).

Schema (v2; v4.5.1+) — written to `<install-prefix>/.agentm-config.json`:

    {
      "schema_version": 2,
      "mode": "source" | "release",
      "source_clones": {
        "agentm":  "/srv/projects/agentm"  // present iff detected
      },
      "installed_at": "2026-05-27T18:00:00Z",
      "harness_version": "v4.5.1",                   // semver string
      "vault_path": "/path/to/Obsidian/MyVault"      // null when unset; the
                                                      // on-device source of truth
                                                      // for the MemoryVault root
                                                      // (env MEMORY_VAULT_PATH wins
                                                      // as override per locked DC-2)
    }

Pre-v4.5.1 installs may have a legacy `.agentm-install-state.json` file with
schema v1 (`"version": 1`, no `vault_path` field). `persist_install_state()`
auto-migrates: reads the legacy file (if new file absent), preserves any
`vault_path` field found, removes the legacy file, and writes schema v2 under
the new name. The read side is `read_install_state()` below.

Stdlib-only (ADR 0001). Cross-platform via pathlib + os.path.expanduser.

Per V4 #30 plan #22 task 3.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# v4.5.1 task 4+5 fold: bumped to schema v2 + renamed config file.
# `_LEGACY_FILENAME` is read at persist-time to migrate pre-v4.5.1 installs:
# if the legacy file exists at the install prefix and the new file doesn't,
# we read the legacy contents + persist them under the new name + remove the
# legacy. Atomic via `os.replace()`. The read side is `read_install_state()`
# below (resolves new → legacy filename without rewriting on disk).
_SCHEMA_VERSION = 2
_STATE_FILENAME = ".agentm-config.json"
_LEGACY_FILENAME = ".agentm-install-state.json"

# Default canonical clone path — operator's documented dev-setup convention.
# Overridable via CLI flag for tests + non-default setups.
_DEFAULT_AGENTM_CLONE = "~/Antigravity/agentm"


# -----------------------------------------------------------------------------
# Probe primitives
# -----------------------------------------------------------------------------

def _is_agentm_source_clone(path: Path) -> bool:
    """Heuristic: path is an agentm source clone if it has .git/ + harness/."""
    return path.is_dir() and (path / ".git").exists() and (path / "harness").is_dir()


def detect_source_clones(
    agentm_path: Path | str | None = None,
) -> dict[str, str]:
    """Probe the canonical path; return `{agentm?}` of the detected clone.

    Returns a dict containing the `agentm` key iff the path was verified as
    an agentm source clone. Missing key means "not detected".

    The arg defaults to the operator-canonical path (`~/Antigravity/agentm`);
    pass an explicit path for tests or non-default setups.

    Pure read; no writes. Cross-platform via pathlib.
    """
    if agentm_path is None:
        agentm_path = _DEFAULT_AGENTM_CLONE
    out: dict[str, str] = {}
    agentm = Path(os.path.expanduser(str(agentm_path)))
    if _is_agentm_source_clone(agentm):
        out["agentm"] = str(agentm)
    return out


def detect_install_mode(
    agentm_path: Path | str | None = None,
) -> tuple[str, dict[str, str]]:
    """Return `(mode, source_clones)` based on the detected agentm clone.

    - agentm clone detected → mode='source' (symlink customizations from it)
    - not detected          → mode='release' (copy customizations from tarball)
    """
    clones = detect_source_clones(agentm_path)
    mode = "source" if clones else "release"
    return mode, clones


# -----------------------------------------------------------------------------
# Persist + read primitives
# -----------------------------------------------------------------------------

def state_path(install_prefix: Path | str) -> Path:
    """Return the install-state JSON path under the given install prefix."""
    return Path(install_prefix) / _STATE_FILENAME


def persist_install_state(
    install_prefix: Path | str,
    mode: str,
    source_clones: dict[str, str],
    harness_version: str,
    *,
    installed_at: Optional[str] = None,
    installer_source: Optional[str] = None,
    installed_shas: Optional[dict[str, str]] = None,
    fragments: Optional[list[dict]] = None,
) -> Path:
    """Write `<install-prefix>/.agentm-install-state.json` atomically.

    Atomic via tmp+rename. Creates the install-prefix dir if absent.

    Returns the written path.

    Fields:
      - `mode` must be 'source' or 'release'.
      - `installed_at` defaults to current UTC ISO8601 timestamp.
      - `installer_source` (optional): absolute path to the install.sh that
        bootstrapped this install. Used by `agentm-update` launcher to
        invoke `--update` against the correct installer. If unset, the
        launcher surfaces an actionable error.
      - `installed_shas` (optional): {rel_path: sha256, ...} map of
        customizations as last installed. Used by `install_copy.py` for
        divergence detection on subsequent updates.
      - `fragments` (optional): list of `{path, sha256}` records describing
        the settings.json fragments that were merged at install time.
        Retained as install-time metadata (records which hook fragments were
        merged + their SHA at install).
    """
    if mode not in ("source", "release"):
        raise ValueError(f"mode must be 'source' or 'release', got: {mode!r}")
    prefix = Path(install_prefix)
    prefix.mkdir(parents=True, exist_ok=True)
    if installed_at is None:
        installed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # v4.5.1: preserve pre-existing vault_path field across re-install + handle
    # the legacy filename. Resolution: read new file → fall back to legacy
    # filename if present (and remove legacy after harvest; mirrors the
    # read side in read_install_state()).
    new_path = prefix / _STATE_FILENAME
    legacy_path = prefix / _LEGACY_FILENAME
    preserved: dict = {}
    source_for_preserve: Optional[Path] = None
    if new_path.is_file():
        source_for_preserve = new_path
    elif legacy_path.is_file():
        source_for_preserve = legacy_path
    if source_for_preserve is not None:
        try:
            prior = json.loads(source_for_preserve.read_text(encoding="utf-8"))
            if isinstance(prior, dict) and "vault_path" in prior:
                preserved["vault_path"] = prior["vault_path"]
        except (json.JSONDecodeError, OSError):
            pass
    # Drop the legacy file if it exists — the persist below writes the new
    # path; leaving the legacy around would create a split-brain state until
    # the next SessionStart hook fires.
    if legacy_path.is_file() and legacy_path != new_path:
        try:
            legacy_path.unlink()
        except OSError:
            pass

    data: dict = {
        "schema_version": _SCHEMA_VERSION,
        "mode": mode,
        "source_clones": source_clones,
        "installed_at": installed_at,
        "harness_version": harness_version,
    }
    if "vault_path" in preserved:
        data["vault_path"] = preserved["vault_path"]
    else:
        # Field is always present in schema v2; null when unset.
        data["vault_path"] = None
    if installer_source is not None:
        data["installer_source"] = installer_source
    if installed_shas is not None:
        data["installed_shas"] = installed_shas
    if fragments is not None:
        data["fragments"] = fragments
    path = state_path(prefix)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def read_install_state(install_prefix: Path | str) -> Optional[dict]:
    """Read the install-state JSON; return None if absent or malformed.

    Resolution: prefer `.agentm-config.json` (v4.5.1+ canonical); fall back
    to legacy `.agentm-install-state.json` if only the legacy file exists.
    This is a READ — does not migrate the file on disk. Migration happens
    on the next `persist_install_state()` call.

    None semantics: caller treats no-state as "first install / pre-#30
    install"; the next persist will create the file.
    """
    prefix = Path(install_prefix)
    path = prefix / _STATE_FILENAME
    if not path.is_file():
        legacy = prefix / _LEGACY_FILENAME
        if not legacy.is_file():
            return None
        path = legacy
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe + persist install mode (source vs release).",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_detect = sub.add_parser(
        "detect", help="emit detected install mode + source clones as JSON",
    )
    p_detect.add_argument("--agentm-path", default=None, help="override canonical agentm clone path")

    p_persist = sub.add_parser(
        "persist",
        help="probe + persist install state to <install-prefix>/.agentm-install-state.json",
    )
    p_persist.add_argument("install_prefix", help="install destination (e.g. ~/.claude or <target>/.claude)")
    p_persist.add_argument("--harness-version", required=True, help="harness version string (e.g. v4.3.0)")
    p_persist.add_argument("--agentm-path", default=None, help="override canonical agentm clone path")
    p_persist.add_argument("--installer-source", default=None, help="absolute path to install.sh used to bootstrap (recorded for agentm-update launcher)")
    p_persist.add_argument("--fragments-file", default=None, help="path to a JSON file containing a list of {path, sha256} records for settings.json fragments merged at install time (written to the .fragments install-time metadata field)")

    p_read = sub.add_parser(
        "read", help="emit current install state JSON, or empty if absent",
    )
    p_read.add_argument("install_prefix", help="install destination")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd is None:
        parser.print_help()
        return 2

    if args.cmd == "detect":
        mode, clones = detect_install_mode(args.agentm_path)
        sys.stdout.write(json.dumps({
            "mode": mode,
            "source_clones": clones,
        }, indent=2) + "\n")
        return 0

    if args.cmd == "persist":
        try:
            mode, clones = detect_install_mode(args.agentm_path)
            prefix = Path(os.path.expanduser(args.install_prefix))
            fragments = None
            if args.fragments_file is not None:
                frag_path = Path(os.path.expanduser(args.fragments_file))
                loaded = json.loads(frag_path.read_text(encoding="utf-8"))
                if not isinstance(loaded, list):
                    raise ValueError("--fragments-file must contain a JSON list")
                fragments = loaded
            path = persist_install_state(
                prefix, mode, clones, args.harness_version,
                installer_source=args.installer_source,
                fragments=fragments,
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"[install_state] {exc}", file=sys.stderr)
            return 2
        sys.stdout.write(str(path) + "\n")
        return 0

    if args.cmd == "read":
        prefix = Path(os.path.expanduser(args.install_prefix))
        data = read_install_state(prefix)
        if data is None:
            sys.stdout.write("{}\n")
            return 0
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
