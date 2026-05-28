#!/usr/bin/env python3
"""install_state_sync — SessionStart re-merge of settings.json fragments.

Per V4 #30 plan #22 task 6: detect drift between the recorded fragments in
`<install-prefix>/.agentm-config.json` and the current source SHA;
re-merge divergent fragments into `<install-prefix>/settings.json`.

Single hook handles both install modes:
  - **Source mode**: operator edits a fragment in the source clone
    (`~/Antigravity/<repo>/hooks/<name>/settings-fragment-*.json`) → SHA
    drifts → re-merge picks up the edit live.
  - **Release mode**: `--update` refreshes copy at `<install-prefix>/...`
    → SHA drifts → re-merge applies the new fragment.

Idempotent + non-blocking:
  - On every SessionStart, for each tracked fragment:
    - Compute current SHA at recorded `path`.
    - If matches recorded SHA → no-op.
    - If differs → re-merge via `merge-settings-fragment.py` + update SHA.
  - Hook surfaces a one-line stderr notice on re-merge; silent otherwise.
  - Failure (missing fragment, malformed JSON, merge error) emits one-line
    stderr + continues (never freezes session start).

Graceful-skip:
  - Missing config file (pre-V4 #30 install) → exit 0, silent.
  - No `fragments` field in state → exit 0, silent.

Schema v2 (v4.5.1+):
  - Config file renamed `.agentm-install-state.json` → `.agentm-config.json`.
    Hard cutover: `_read_state()` atomically renames legacy → new on first
    read (single-shot per install). No breadcrumb left at the old path.
  - `schema_version: 2` written on every write (replaces legacy `version: 1`).
  - `vault_path` field added at top level; persisted as `null` when unset.
    Read by `harness_memory.py::vault_path()` as the on-device source of
    truth for the MemoryVault location (env `$MEMORY_VAULT_PATH` still wins
    as an override).

Stdlib-only (ADR 0001). Per V4 #30 plan #22 task 6 + v4.5.1 task 1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


_BUF_SIZE = 65536

# v4.5.1 task 1 — config file rename + schema v2.
_CONFIG_FILENAME = ".agentm-config.json"
_LEGACY_FILENAME = ".agentm-install-state.json"
_SCHEMA_VERSION = 2


def _sha256(path: Path) -> str:
    """Return hex SHA256 of a file's contents. Empty string if absent."""
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(_BUF_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _read_state(install_prefix: Path) -> Optional[dict]:
    """Read the agentm config file; auto-migrate legacy filename on first read.

    Resolution:
      1. If `.agentm-config.json` exists → read it directly.
      2. Else if legacy `.agentm-install-state.json` exists → atomically
         rename to `.agentm-config.json` (one-shot migration) + read.
         No breadcrumb left at the old path per v4.5.1 locked DC-1.
      3. Else → return None (graceful-skip; same semantics as pre-v4.5.1).

    Reads return the dict AS-IS from disk. Callers handle schema-version
    differences via `state.get("schema_version", 1)` — legacy files lack the
    field; force-write to v2 happens in `_write_state()`.
    """
    config_path = install_prefix / _CONFIG_FILENAME
    if not config_path.is_file():
        legacy_path = install_prefix / _LEGACY_FILENAME
        if not legacy_path.is_file():
            return None
        # One-shot migration. os.replace() is atomic on POSIX + Windows.
        try:
            os.replace(str(legacy_path), str(config_path))
        except OSError:
            return None
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_state(install_prefix: Path, state: dict) -> None:
    """Write the agentm config file atomically; force schema v2.

    Always writes to `.agentm-config.json`. Forces `schema_version: 2` on
    every write + ensures `vault_path` field is present (None when unset).
    Drops the legacy `version` field if present (superseded by
    `schema_version`).
    """
    config_path = install_prefix / _CONFIG_FILENAME
    state = dict(state)  # don't mutate caller's dict
    state["schema_version"] = _SCHEMA_VERSION
    state.setdefault("vault_path", None)
    state.pop("version", None)  # legacy schema-version field, superseded
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(config_path)


def _maybe_run_version_check(install_prefix: Path) -> None:
    """Optionally invoke upstream_version_check if available + applicable.

    Best-effort import; defensive try/except. Only runs in release mode.
    Never crashes the hook on failure.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import upstream_version_check as uvc
    except Exception:
        return
    state = _read_state(install_prefix)
    if not state:
        return
    if state.get("mode") != "release":
        # Source mode operators get updates via symlinks; no upstream check.
        return
    installed_version = state.get("harness_version")
    if not installed_version:
        return
    installed = {"alexherrero/agentm": installed_version}
    try:
        uvc.check_and_notify(install_prefix, installed=installed)
    except Exception:
        pass  # non-blocking


def sync_fragments(
    install_prefix: Path | str,
    *,
    merge_script: Optional[Path | str] = None,
    settings_path: Optional[Path | str] = None,
) -> dict:
    """Detect + re-merge divergent settings.json fragments.

    Returns {checked, no_change, re_merged, errors} lists of fragment paths.

    `merge_script`: path to merge-settings-fragment.py (defaults to sibling
        scripts/merge-settings-fragment.py from this file's directory).
    `settings_path`: target settings.json (defaults to
        `<install-prefix>/settings.json`).
    """
    prefix = Path(install_prefix)
    state = _read_state(prefix)
    out: dict[str, list[str]] = {
        "checked": [], "no_change": [], "re_merged": [], "errors": [],
    }
    if state is None:
        return out  # graceful-skip — no state, nothing to sync

    fragments = state.get("fragments")
    if not isinstance(fragments, list):
        return out  # graceful-skip — no fragments tracked

    if merge_script is None:
        merge_script = Path(__file__).resolve().parent / "merge-settings-fragment.py"
    merge_script = Path(merge_script)
    if settings_path is None:
        settings_path = prefix / "settings.json"
    settings_path = Path(settings_path)

    state_changed = False
    for idx, entry in enumerate(fragments):
        if not isinstance(entry, dict):
            continue
        frag_path_str = entry.get("path")
        recorded_sha = entry.get("sha256", "")
        if not isinstance(frag_path_str, str) or not frag_path_str:
            continue
        frag_path = Path(frag_path_str)
        out["checked"].append(frag_path_str)
        current_sha = _sha256(frag_path)
        if not current_sha:
            # Source fragment is gone; nothing to merge. Surface as error.
            out["errors"].append(frag_path_str)
            continue
        if current_sha == recorded_sha:
            out["no_change"].append(frag_path_str)
            continue
        # Divergence — re-merge
        if not merge_script.is_file():
            out["errors"].append(frag_path_str)
            continue
        try:
            res = subprocess.run(
                [sys.executable, str(merge_script),
                 str(settings_path), str(frag_path)],
                capture_output=True, text=True, check=False,
            )
        except OSError:
            out["errors"].append(frag_path_str)
            continue
        if res.returncode != 0:
            out["errors"].append(frag_path_str)
            continue
        # Successful re-merge — update recorded SHA
        fragments[idx]["sha256"] = current_sha
        state_changed = True
        out["re_merged"].append(frag_path_str)

    if state_changed:
        state["fragments"] = fragments
        try:
            _write_state(prefix, state)
        except OSError as exc:
            # State write failure — surface but don't fail-loud (hook is
            # non-blocking)
            print(f"[install_state_sync] state write failed: {exc}", file=sys.stderr)

    return out


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Re-merge divergent settings.json fragments per recorded install state.",
    )
    parser.add_argument(
        "--install-prefix",
        default=None,
        help="install prefix (default: $AGENTM_INSTALL_PREFIX or ~/.claude)",
    )
    parser.add_argument(
        "--settings-path",
        default=None,
        help="path to target settings.json (default: <install-prefix>/settings.json)",
    )
    parser.add_argument(
        "--merge-script",
        default=None,
        help="path to merge-settings-fragment.py (default: sibling in scripts/)",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress stdout JSON; stderr summary only on changes")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.install_prefix:
        prefix = Path(os.path.expanduser(args.install_prefix))
    elif os.environ.get("AGENTM_INSTALL_PREFIX"):
        prefix = Path(os.path.expanduser(os.environ["AGENTM_INSTALL_PREFIX"]))
    else:
        prefix = Path.home() / ".claude"

    result = sync_fragments(
        prefix,
        merge_script=args.merge_script,
        settings_path=args.settings_path,
    )

    # Also run upstream-version-check (best-effort; release-mode-only).
    _maybe_run_version_check(prefix)

    if not args.quiet:
        sys.stdout.write(json.dumps(result, indent=2) + "\n")

    # One-line stderr notice on re-merge (visible in session-start logs)
    if result["re_merged"]:
        print(
            f"[install-state-sync] re-merged {len(result['re_merged'])} fragment(s) into settings.json",
            file=sys.stderr,
        )
    if result["errors"]:
        print(
            f"[install-state-sync] {len(result['errors'])} fragment(s) skipped due to errors",
            file=sys.stderr,
        )
    # Exit 0 always — non-blocking; hook contract.
    return 0


if __name__ == "__main__":
    sys.exit(main())
