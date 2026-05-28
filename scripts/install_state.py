#!/usr/bin/env python3
"""install_state — probe + persist install mode (source vs release).

Used by `install.sh` + `install.ps1` to detect whether the operator has
source-clone canonical paths for agentm + crickets. The decision drives
tasks 4-5 in V4 #30 plan #22:

  - Source mode  → symlink the customizations subset from source clones
                   into the install prefix (skill dirs, agent .md, command
                   .md, hook scripts, hook .py helpers — per locked DC-7).
  - Release mode → copy customizations from release tarballs into the
                   install prefix; SessionStart hook surfaces upstream-
                   version-check notices (per locked DC-3).

Canonical clone paths checked:

  ~/Antigravity/agentm/    + has .git/  + has harness/  → agentm source present
  ~/Antigravity/crickets/  + has .git/  + has skills/   → crickets source present

The probe is **silent** — emits nothing to stdout except the JSON persist
target. Operator sees no prompt; the install dispatches based on the
detected mode without ceremony (per FOLLOWUPS locked semantics 2026-05-27).

Schema (v1) — written to `<install-prefix>/.agentm-install-state.json`:

    {
      "version": 1,
      "mode": "source" | "release",
      "source_clones": {
        "agentm":  "/srv/projects/agentm"  // present iff detected
        "crickets": "/srv/projects/crickets"
      },
      "installed_at": "2026-05-27T18:00:00Z",
      "harness_version": "v4.2.0"                    // semver string
    }

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

_SCHEMA_VERSION = 1
_STATE_FILENAME = ".agentm-install-state.json"

# Default canonical clone paths — operator's documented dev-setup convention.
# Overridable via CLI flags for tests + non-default setups.
_DEFAULT_AGENTM_CLONE = "~/Antigravity/agentm"
_DEFAULT_CRICKETS_CLONE = "~/Antigravity/crickets"


# -----------------------------------------------------------------------------
# Probe primitives
# -----------------------------------------------------------------------------

def _is_agentm_source_clone(path: Path) -> bool:
    """Heuristic: path is an agentm source clone if it has .git/ + harness/."""
    return path.is_dir() and (path / ".git").exists() and (path / "harness").is_dir()


def _is_crickets_source_clone(path: Path) -> bool:
    """Heuristic: path is a crickets source clone if it has .git/ + skills/."""
    return path.is_dir() and (path / ".git").exists() and (path / "skills").is_dir()


def detect_source_clones(
    agentm_path: Path | str | None = None,
    crickets_path: Path | str | None = None,
) -> dict[str, str]:
    """Probe canonical paths; return `{agentm?, crickets?}` of detected clones.

    Returns a dict containing only the keys whose paths were verified as
    source clones. Missing keys mean "not detected".

    Both args default to the operator-canonical paths (`~/Antigravity/<repo>`);
    pass explicit paths for tests or non-default setups.

    Pure read; no writes. Cross-platform via pathlib.
    """
    if agentm_path is None:
        agentm_path = _DEFAULT_AGENTM_CLONE
    if crickets_path is None:
        crickets_path = _DEFAULT_CRICKETS_CLONE
    out: dict[str, str] = {}
    agentm = Path(os.path.expanduser(str(agentm_path)))
    crickets = Path(os.path.expanduser(str(crickets_path)))
    if _is_agentm_source_clone(agentm):
        out["agentm"] = str(agentm)
    if _is_crickets_source_clone(crickets):
        out["crickets"] = str(crickets)
    return out


def detect_install_mode(
    agentm_path: Path | str | None = None,
    crickets_path: Path | str | None = None,
) -> tuple[str, dict[str, str]]:
    """Return `(mode, source_clones)` based on detected clones.

    - At least one clone detected → mode='source'
    - Neither clone detected      → mode='release'

    Source mode wins even with partial detection (e.g. only agentm cloned;
    crickets installed via release). The mixed case is handled by the
    install dispatch logic — symlink what's cloned, copy what isn't.
    """
    clones = detect_source_clones(agentm_path, crickets_path)
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
        the settings.json fragments that were merged at install time. Used
        by `install_state_sync.py` SessionStart hook for digest-aware
        re-merge when fragments drift (operator edits source clone in
        source mode; --update refreshes copy in release mode).
    """
    if mode not in ("source", "release"):
        raise ValueError(f"mode must be 'source' or 'release', got: {mode!r}")
    prefix = Path(install_prefix)
    prefix.mkdir(parents=True, exist_ok=True)
    if installed_at is None:
        installed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data: dict = {
        "version": _SCHEMA_VERSION,
        "mode": mode,
        "source_clones": source_clones,
        "installed_at": installed_at,
        "harness_version": harness_version,
    }
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

    None semantics: caller treats no-state as "first install / pre-#30
    install"; the next persist will create the file.
    """
    path = state_path(install_prefix)
    if not path.is_file():
        return None
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
    p_detect.add_argument("--crickets-path", default=None, help="override canonical crickets clone path")

    p_persist = sub.add_parser(
        "persist",
        help="probe + persist install state to <install-prefix>/.agentm-install-state.json",
    )
    p_persist.add_argument("install_prefix", help="install destination (e.g. ~/.claude or <target>/.claude)")
    p_persist.add_argument("--harness-version", required=True, help="harness version string (e.g. v4.3.0)")
    p_persist.add_argument("--agentm-path", default=None, help="override canonical agentm clone path")
    p_persist.add_argument("--crickets-path", default=None, help="override canonical crickets clone path")
    p_persist.add_argument("--installer-source", default=None, help="absolute path to install.sh used to bootstrap (recorded for agentm-update launcher)")

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
        mode, clones = detect_install_mode(args.agentm_path, args.crickets_path)
        sys.stdout.write(json.dumps({
            "mode": mode,
            "source_clones": clones,
        }, indent=2) + "\n")
        return 0

    if args.cmd == "persist":
        try:
            mode, clones = detect_install_mode(args.agentm_path, args.crickets_path)
            prefix = Path(os.path.expanduser(args.install_prefix))
            path = persist_install_state(
                prefix, mode, clones, args.harness_version,
                installer_source=args.installer_source,
            )
        except (ValueError, OSError) as exc:
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
