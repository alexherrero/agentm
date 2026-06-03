#!/usr/bin/env python3
"""harness_memory — auto-context dispatcher for harness phases.

Wires MemoryVault read + write into each harness phase command
(`/setup`, `/plan`, `/work`, `/review`, `/release`, `/bugfix`). Phase specs
invoke this CLI unconditionally; the dispatcher graceful-skips when
MemoryVault is not installed (`$MEMORY_VAULT_PATH` env unset AND no
`vault_path` in `<install-prefix>/.agentm-config.json` OR directory
missing), so the harness runs the same on systems with or without the
sibling `crickets` install.

Sub-commands:

    recall              — phase-specific context-load for the agent's prompt
    offer-save          — preview + ask + dispatch to /memory save (self-modulating
                          by --confidence: ≥ threshold auto-saves silently)
    plan-done-promotion — dumps progress.md tail past a byte cursor; advances cursor
    available           — exit 0 if vault accessible, 1 otherwise
    phase-dispatch      — V4 #23: post-work reflect / post-release refresh
                          (shells out to orchestration_phase.py; non-blocking)
    read-state          — read a project state file via the resolver (vault-first)
    write-state         — write a project state file via the resolver (vault-only)
    vault-state-path    — emit the resolved on-disk path for a state file
    documenter-context  — V4 #35: doc-write-time recall bundle (operator conventions
                          + project decisions + wiki-style) for the documenter
                          sub-agent + wiki-author/diataxis-author skills

Stdlib-only. Cross-platform via pathlib + subprocess. No third-party deps.

Env vars consulted:

    MEMORY_VAULT_PATH                       — root of MemoryVault (required for non-skip)
    HARNESS_AUTO_SAVE_MODE                  — ask | silent | off (default: ask)
    HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD  — float 0–1 (default: 0.8)
    HARNESS_RECALL_BUDGET_<PHASE>           — int tokens per-phase (defaults below)
    HARNESS_MEMORY_TOOLKIT_PATH             — override toolkit memory scripts dir
                                              (used by tests; auto-detect otherwise)

Default recall budgets (tokens, approximate by chars/4):

    setup      = 4000
    plan       = 6000
    work       = 6000
    review     = 4000
    release    = 6000
    bugfix     = 6000
    documenter = 10000  (V4 #35; raised from 4k after task-5 dogfood +
                         project-first ordering; HARNESS_RECALL_BUDGET_DOCUMENTER override)

Per-phase recall query templates live in `_RECALL_QUERIES`. Per-phase
permanence rules live in `_PERMANENT_ONLY_DIRS`.

See agentm ROADMAP #8 + ADR 0009 (lands in plan #8 task 9).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Allow direct import of vault_project (same scripts/ dir).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vault_project as vp  # noqa: E402


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

# The six lifecycle phases plus `documenter` — a recall-context pseudo-phase
# (V4 #35). `documenter` is NOT a lifecycle phase the operator invokes; it's the
# context-load surface the documenter sub-agent + wiki-author/diataxis-author
# skills consume at doc-write time so they don't re-litigate settled style calls.
# It rides the same recall machinery (budget env, project-dir scope) as the real
# phases, which is why it lives in this tuple — `phase_recall("documenter", ...)`
# must validate.
_VALID_PHASES = (
    "setup", "plan", "work", "review", "release", "bugfix", "documenter",
)

_DEFAULT_BUDGETS = {
    "setup": 4000,
    "plan": 6000,
    "work": 6000,
    "review": 4000,
    "release": 6000,
    "bugfix": 6000,
    # V4 #35: documenter-time context budget. Originally 4k (locked DC-3), but
    # the task-5 dogfood showed 4k truncated away the project decisions + the
    # doc-relevant conventions (31 always-load entries total ~27k tokens). Raised
    # to 10k (DC-3 revised 2026-05-28) + the documenter recall now emits project
    # context FIRST (see phase_recall `project_first`), so the project decisions
    # always survive the budget and the conventions fill the remainder.
    # Overrideable via HARNESS_RECALL_BUDGET_DOCUMENTER.
    "documenter": 10000,
}

# Phase-specific subdirectory mappings under `projects/<slug>/` (or legacy
# `personal-projects/<slug>/` pre-V4 #26 rename — resolved via
# `_vault_projects_dir()`). Used by recall to scope the per-project read.
_PHASE_PROJECT_DIRS = {
    "setup": ("_index.md",),  # bootstrap signal only
    "plan": ("_index.md", "decisions", "open-questions"),
    "work": ("decisions", "known-issues"),
    "review": (),  # review reads global conventions, not per-project
    "release": ("decisions",),
    "bugfix": ("known-issues",),
    # V4 #35: documenter-time context. `_index.md` anchors the project; settled
    # `decisions/` keep the documenter from re-litigating ADR-recorded calls;
    # `wiki-style/` (optional — graceful-skip if absent) carries per-project doc
    # conventions (heading shape, page-length norms, mode preferences). Operator
    # globals from `_always-load/` load unconditionally via `_load_always_load`,
    # so they're not listed here.
    "documenter": ("_index.md", "decisions", "wiki-style"),
}

# Per-phase recall query templates. Used for the toolkit-side `recall.py query`
# subprocess call (when the toolkit is available).
_RECALL_QUERIES = {
    "setup": "operator conventions coding style tools setup",
    "plan": "decisions open questions roadmap design",
    "work": "decisions known-issues gotchas conventions",
    "review": "conventions code review patterns",
    "release": "decisions changelog what-shipped",
    "bugfix": "known-issues gotchas recurring root-cause",
    "documenter": "documentation conventions decisions wiki style mode",
}

# Always-load conventions live at personal-private/_always-load/.
_ALWAYS_LOAD_REL = ("personal-private", "_always-load")

# Cursor file relative to project root.
_CURSOR_REL = (".harness", ".promoted-progress-cursor")
_PROGRESS_REL = (".harness", "progress.md")


# -----------------------------------------------------------------------------
# Vault + toolkit discovery
# -----------------------------------------------------------------------------

def _agentm_install_prefix() -> Path:
    """Resolve install prefix per established convention: $AGENTM_INSTALL_PREFIX → ~/.claude."""
    raw = os.environ.get("AGENTM_INSTALL_PREFIX", "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    return Path.home() / ".claude"


def _read_config_vault_path(install_prefix: Optional[Path] = None) -> Optional[Path]:
    """Read `vault_path` from `<install-prefix>/.agentm-config.json`.

    Returns the parsed Path if the field is set + the directory exists.
    Graceful-skips silently on any I/O or parse error — never raises.

    v4.5.1 task 2: introduced as the on-device source-of-truth for the
    vault root. Env `$MEMORY_VAULT_PATH` still wins as an override.
    """
    if install_prefix is None:
        install_prefix = _agentm_install_prefix()
    config_path = install_prefix / ".agentm-config.json"
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("vault_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    p = Path(os.path.expanduser(raw.strip()))
    if not p.is_dir():
        return None
    return p


def _read_config_state_mode(install_prefix: Optional[Path] = None) -> Optional[str]:
    """Read `state_mode` from `<install-prefix>/.agentm-config.json`.

    Returns the stripped-lowercase value ("local" / "vault") or None when the
    field is absent / empty / unreadable. This is the **device-level** run-mode
    config — read **vault-free** (mirrors `_read_config_vault_path`), so it works
    on a machine with no vault. Honors `$AGENTM_INSTALL_PREFIX`.

    Per Hardening I #44 task 3 / locked DC-8: config is on-host only; the vault
    holds data, never configuration. This is the second resolution layer in
    `_read_project_mode()` (under the per-repo `.harness/.project-mode` marker).
    """
    if install_prefix is None:
        install_prefix = _agentm_install_prefix()
    config_path = install_prefix / ".agentm-config.json"
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("state_mode")
    if not isinstance(raw, str):
        return None
    return raw.strip().lower() or None


def vault_path() -> Optional[Path]:
    """Return the MemoryVault root if accessible, else None.

    Resolution order (first hit wins):
      1. `$MEMORY_VAULT_PATH` env — preserved as override for CI / debugging /
         per-session use. Even when set to a non-existent path, env takes
         precedence: this branch returns None rather than falling through,
         so operators can detect + fix a broken export. (v4.5.1 locked DC-2.)
      2. `<install-prefix>/.agentm-config.json::vault_path` — the on-device
         source of truth written by `agentm_config.py --vault-path <path>` or
         the installer's first-run prompt. Install prefix honors
         `$AGENTM_INSTALL_PREFIX` for non-default setups.
      3. `None` — graceful-skip; same semantics as pre-v4.5.1 but now fires
         only when BOTH paths are empty.

    The directory must exist for the path to be returned.
    """
    raw = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if raw:
        p = Path(os.path.expanduser(raw))
        if not p.is_dir():
            return None
        return p
    return _read_config_vault_path()


def is_available() -> bool:
    """True iff MemoryVault is accessible."""
    return vault_path() is not None


# -----------------------------------------------------------------------------
# Project resolution (V4 #26 — vault-backed state)
# -----------------------------------------------------------------------------

# Canonical project-tree subdir under <vault>. V4 #26 renames
# `personal-projects/` → `projects/`. During the transition window (operators
# who haven't yet run rename-vault-personal-projects.sh), the legacy name
# remains as a fallback — see `_vault_projects_dir()` below.
_VAULT_PROJECTS_REL_NEW = "projects"
_VAULT_PROJECTS_REL_LEGACY = "personal-projects"


def _vault_projects_dir(vault: Path) -> Path:
    """Return <vault>/projects/ if present, else <vault>/personal-projects/.

    Prefers the new (post-V4 #26) name. Falls back to the legacy name if
    the operator hasn't run the vault rename yet. Returns the new path even
    if neither exists — callers that need to write should target the new
    layout.

    No warning emitted here — that's task 3's `warn_once()` job, which wraps
    this helper from the dispatcher path.
    """
    new = vault / _VAULT_PROJECTS_REL_NEW
    if new.is_dir():
        return new
    legacy = vault / _VAULT_PROJECTS_REL_LEGACY
    if legacy.is_dir():
        return legacy
    # Neither exists — return new (preferred) for caller to mkdir as needed.
    return new


def resolve_project(context: Optional[dict] = None) -> dict:
    """Return a resolution dict: {slug, vault_path, project_root, layout}.

    Resolution chain (per plan #18 task 4 — `04-project-resolution.md`):
      1. Read the project slug via `vault_project.read_vault_project(cwd)`.
      2. Resolve `<vault>/projects/<slug>/` (new) or `<vault>/personal-projects/<slug>/`
         (legacy fallback). Prefer new layout.
      3. Build the resolution dict.

    Returns dict fields:
      - `slug`: project slug string, or None if unresolvable.
      - `vault_path`: Path to `<vault>/projects/<slug>/` (or legacy fallback),
                      or None if vault unavailable / slug unresolvable.
      - `project_root`: Path to the cwd / context-provided project root.
      - `layout`: "new" | "legacy" | "none" — which vault layout the resolution
                  used. Useful for the dispatcher's warn-once decision in task 3.

    For state lookups, use the companion `vault_state_path(resolution, filename)`
    which appends `_harness/<filename>` to `vault_path`.

    Pure function; no side effects. Safe to call from any phase / hook.
    """
    if context is None:
        context = {}
    project_root = Path(context.get("cwd", Path.cwd()))

    # Defer vault_project import to here to avoid circular imports if any.
    sys.path.insert(0, str(_HERE)) if str(_HERE) not in sys.path else None
    import vault_project  # noqa: E402

    slug = vault_project.read_vault_project(project_root)
    if slug is None:
        return {
            "slug": None,
            "vault_path": None,
            "project_root": project_root,
            "layout": "none",
        }

    v = vault_path()
    if v is None:
        return {
            "slug": slug,
            "vault_path": None,
            "project_root": project_root,
            "layout": "none",
        }

    new = v / _VAULT_PROJECTS_REL_NEW / slug
    if new.is_dir():
        return {
            "slug": slug,
            "vault_path": new,
            "project_root": project_root,
            "layout": "new",
        }

    legacy = v / _VAULT_PROJECTS_REL_LEGACY / slug
    if legacy.is_dir():
        return {
            "slug": slug,
            "vault_path": legacy,
            "project_root": project_root,
            "layout": "legacy",
        }

    # Project not yet present in vault — return new path so writes target
    # the post-rename layout. Callers that need to mkdir do so explicitly.
    return {
        "slug": slug,
        "vault_path": new,
        "project_root": project_root,
        "layout": "new",
    }


def vault_state_path(resolution: dict, filename: str) -> Optional[Path]:
    """Return <vault_path>/_harness/<filename> for a project state file.

    Returns None if resolution lacks vault_path (no slug, no vault, etc.).

    Pure path-construction; doesn't check existence. Callers that need to
    read should use `read_state_file()` below (checks vault first, falls
    back to legacy <project_root>/.harness/<file>). Writes go only to this
    path via `write_state_file()`.

    Per plan #18 task 5 — `05-state-migration.md` § "Per-file target mapping".
    """
    if not resolution.get("vault_path"):
        return None
    return resolution["vault_path"] / "_harness" / filename


# -----------------------------------------------------------------------------
# Backward-compat read/write dispatcher (V4 #26 task 3)
# -----------------------------------------------------------------------------

# Session-scoped set of (filename, source) tuples we've already warned about.
# Resets when the Python process exits (the recall hooks are short-lived
# subprocess invocations; this set lives for one invocation. Per-session
# semantics from the operator perspective = per-invocation in practice).
# Per locked design call DC-2: warn once per session per file.
_warned_legacy_reads: set = set()


def warn_once(filename: str, source: str = "legacy") -> None:
    """Emit a deprecation-warn on stderr — only the first time per session per file.

    `filename` is the state file shortname (e.g. "PLAN.md", "progress.md").
    `source` describes the read origin (currently "legacy" is the only value;
    leaves room for future "vault-stale" or similar markers).

    Idempotent. Safe to call from any phase / hook.
    """
    key = (filename, source)
    if key in _warned_legacy_reads:
        return
    _warned_legacy_reads.add(key)
    if source == "legacy":
        print(
            f"[harness_memory] reading {filename} from legacy <project>/.harness/ "
            f"— run `bash agentm/scripts/migrate-harness-to-vault.sh <project>` "
            f"to move state to <vault>/projects/<slug>/_harness/. "
            f"This warning will not repeat this session.",
            file=sys.stderr,
        )
    else:
        print(
            f"[harness_memory] {filename}: {source}",
            file=sys.stderr,
        )


def _reset_warn_state() -> None:
    """Test-only: clear the warned-set. Not part of the public API."""
    _warned_legacy_reads.clear()


def _read_mode_marker(path: Path) -> Optional[str]:
    """Return the stripped-lowercase contents of a `.project-mode` marker file,
    or None when the file is absent / unreadable / empty.

    An empty (whitespace-only) marker is treated as absent so a stray blank
    file doesn't accidentally override a meaningful marker elsewhere.
    """
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8").strip().lower()
        except OSError:
            return None
        return text or None
    return None


def _read_project_mode(resolution: dict) -> Optional[str]:
    """Resolve the effective state mode for this project.

    Returns the mode value (e.g. "local") or None when nothing resolves
    (the default, vault-backed mode).

    Resolution — **two on-host layers, no vault involved** (locked DC-2/DC-8;
    config is on-host only, the vault holds data not configuration):

      1. Repo-local marker ``<project_root>/.harness/.project-mode`` — the
         optional **per-repo override**, in the repo so it is reachable with no
         vault. Wins when present.
      2. ``state_mode`` in ``<install-prefix>/.agentm-config.json`` — the
         **device-level default** ("how agentm runs on this machine"), read
         vault-free via ``_read_config_state_mode``.

    There is **no in-vault marker layer** — configuration never lives in the
    vault. The mode is never inferred from a missing ``vault_path`` (DC-3:
    that is ambiguous and would split-brain a transiently-unreachable vault).
    """
    project_root = resolution.get("project_root") or Path.cwd()

    # 1. Repo-local marker — per-repo override, on-host, vault-independent.
    repo_local = _read_mode_marker(Path(project_root) / ".harness" / ".project-mode")
    if repo_local is not None:
        return repo_local

    # 2. Device-level default — state_mode in .agentm-config.json (on-host).
    return _read_config_state_mode()


def _read_repo_local_state_file(project_root: Path, filename: str) -> str:
    """Read ``<project_root>/.harness/<filename>`` as the *configured* local-mode
    home — without the legacy-migration warning.

    Distinct from ``_read_legacy_state_file``: that path is a *fallback* (a vault
    is expected but the file happens to live in the repo, so it nags the operator
    to migrate). Local mode is a deliberate opt-in, so the repo-local ``.harness/``
    is the canonical home and reading it is not a deprecation event.
    """
    local = Path(project_root) / ".harness" / filename
    if local.is_file():
        try:
            return local.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"[harness_memory] failed to read {local}: {exc}",
                file=sys.stderr,
            )
    return ""


def read_state_file(resolution: dict, filename: str) -> str:
    """Read a project state file, preferring vault-backed location.

    Resolution chain:
      1. <vault>/projects/<slug>/_harness/<filename>  (V4 #26 canonical)
      2. <project_root>/.harness/<filename>           (legacy fallback;
                                                       emits warn-once)
      3. ""                                           (neither exists; empty
                                                       string for caller's
                                                       missing-state semantic)

    Per plan #18 task 5 + task 9 design specs. Per locked DC-2: warn once
    per session per file. Warn-once state is module-level (session-scoped);
    test helpers can reset via `_reset_warn_state()`.

    Honors the effective state mode (per locked DC-2/DC-8): if mode is "local",
    the read targets `<project_root>/.harness/<filename>` directly — local mode
    is the configured home, not a legacy fallback, so it does *not* emit the
    migrate-to-vault warn. The mode is resolved via `_read_project_mode()` from
    two on-host layers (repo-local `.harness/.project-mode` marker → device-level
    `state_mode` in `.agentm-config.json`), so it is honored with no vault.
    """
    vault_p = resolution.get("vault_path")
    project_root = resolution.get("project_root") or Path.cwd()

    # Local mode (DC-2/DC-3): the repo-local `.harness/` is the canonical home.
    # Read it directly — vault-independent, no deprecation warning.
    if _read_project_mode(resolution) == "local":
        return _read_repo_local_state_file(project_root, filename)

    if vault_p:
        # Default: try the vault path.
        target = vault_p / "_harness" / filename
        if target.is_file():
            try:
                return target.read_text(encoding="utf-8")
            except OSError as exc:
                print(
                    f"[harness_memory] failed to read {target}: {exc}",
                    file=sys.stderr,
                )
                # Fall through to legacy as a last resort.

    # Legacy fallback — <project_root>/.harness/<filename> (emits warn-once).
    return _read_legacy_state_file(project_root, filename)


def _read_legacy_state_file(project_root: Path, filename: str) -> str:
    legacy = project_root / ".harness" / filename
    if legacy.is_file():
        warn_once(filename, "legacy")
        try:
            return legacy.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"[harness_memory] failed to read legacy {legacy}: {exc}",
                file=sys.stderr,
            )
            return ""
    return ""


def write_state_file(resolution: dict, filename: str, content: str) -> Path:
    """Write a project state file, vault-backed by default, repo-local in local mode.

    Creates <vault>/projects/<slug>/_harness/ if absent. Atomic write via
    `<path>.tmp` + `rename()`. Returns the absolute path written.

    Mode resolution (per locked DC-2/DC-8): `_read_project_mode()` honors two
    on-host layers — a per-repo `<project_root>/.harness/.project-mode` marker,
    then the device-level `state_mode` in `.agentm-config.json`. When the
    effective mode is "local", the write targets `<project_root>/.harness/<filename>`
    — the first-class single-repo write path, which succeeds **with no vault**.

    Raises ValueError only when the mode is *not* local **and** the resolution
    lacks a vault_path — i.e. a vault-mode project with no reachable vault. The
    error names the local-mode opt-in so the caller knows the escape hatch.
    """
    vault_p = resolution.get("vault_path")
    project_root = resolution.get("project_root") or Path.cwd()

    # Local mode (DC-2/DC-3): route to the repo-local home. Vault-independent —
    # this is what makes single-repo mode writable without a vault.
    if _read_project_mode(resolution) == "local":
        return _write_repo_local_state_file(project_root, filename, content)

    if vault_p is None:
        raise ValueError(
            f"cannot write {filename}: resolution lacks vault_path "
            f"(slug={resolution.get('slug')!r}, layout={resolution.get('layout')!r}). "
            f"Resolve the project first, set MEMORY_VAULT_PATH, or opt into "
            f"single-repo mode with a <repo>/.harness/.project-mode file "
            f"containing 'local'."
        )

    target = vault_p / "_harness" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    return target


def _write_repo_local_state_file(project_root: Path, filename: str, content: str) -> Path:
    """Local-mode write path: write to <project_root>/.harness/<filename>.

    Used when the effective `.project-mode` = "local" (DC-2/DC-3). Same
    atomic-write semantics as the vault path. Works with no vault configured.
    """
    target = Path(project_root) / ".harness" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    return target


# -----------------------------------------------------------------------------
# Concurrency primitives (V4 #26 task 4)
# -----------------------------------------------------------------------------

class ConcurrentModificationError(RuntimeError):
    """Raised by safe_write_replace_style when a file's mtime changed between
    read and write — indicates another agent / device modified the file.

    Caller is expected to re-read + re-apply changes + retry. Per plan #18
    task 8 (`08-concurrency.md` § "Replace-style files — cursor + last-modified
    check").
    """


def safe_write_replace_style(
    path: Path,
    new_content: str,
    *,
    expected_mtime: Optional[float] = None,
) -> Path:
    """Write `new_content` to `path` atomically with optional mtime-check.

    Modes:
      - `expected_mtime is None` (default): plain atomic write — no
        concurrent-modification check. Caller didn't observe prior mtime.
      - `expected_mtime is not None`: pre-write check — re-stat `path`; if
        its current mtime differs from expected, raise
        ConcurrentModificationError without writing.

    The mtime-check guards against device-A vs device-B concurrent edits
    when both have GDrive-synced views of the same vault file. Caller
    pattern:

        current_mtime = path.stat().st_mtime if path.exists() else None
        # ... compute new_content ...
        safe_write_replace_style(path, new_content, expected_mtime=current_mtime)

    Atomic via `<path>.tmp` + os.replace. Creates parent dir if absent.
    Returns the written path.

    Race window: between the mtime re-stat and the rename, another process
    could write. Documented limitation per plan #18 task 8 — bounded by
    cursor-tracked promotion + the size of the rename-window (sub-ms).
    Suitable for replace-style files (PLAN.md, _index.md, features.json,
    FOLLOWUPS.md); NOT suitable for append-only files (progress.md) — those
    use natural-merge via GDrive's append-handling.
    """
    path = Path(path)
    if expected_mtime is not None and path.exists():
        actual = path.stat().st_mtime
        if abs(actual - expected_mtime) > 1e-6:  # float-tolerance
            raise ConcurrentModificationError(
                f"{path} was modified since read (expected mtime={expected_mtime}, "
                f"actual={actual}). Another agent or device wrote to it. "
                f"Re-read and re-apply changes."
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_content, encoding="utf-8")
    tmp.replace(path)
    return path


# GDrive's conflict-file naming: "<basename> (conflicted copy YYYY-MM-DD) - <device>.<ext>"
# Heuristic substring match — robust to GDrive's variations (with/without
# trailing device name; with/without date hyphens; "from <device>" variants).
_CONFLICT_MARKER = "(conflicted copy"


def detect_conflict_files(vault_root: Path) -> list[dict]:
    """Walk `vault_root` for GDrive-induced conflict files.

    Returns a list of dicts:
        [{"conflict": <conflict-file-path>,
          "base": <inferred-base-file-path>,
          "rel": <relative-to-vault-root>}, ...]

    The `base` field is the operator-canonical filename the conflict pairs
    with — stripping the GDrive marker. Example:
        conflict = ".../PLAN (conflicted copy 2026-05-27) - Mac.md"
        base     = ".../PLAN.md"

    The dispatcher hook (task-4 conflict-merger-session-start) walks this
    list at SessionStart and surfaces an operator-confirm dialog per pair.

    Heuristic: matches `(conflicted copy` substring (case-insensitive).
    Limitations: GDrive may change the format without notice — re-audit if
    detection ratio drops. Detection is best-effort; false-negatives are
    acceptable (operator finds the conflict file in Obsidian).
    """
    vault_root = Path(vault_root)
    if not vault_root.is_dir():
        return []

    out: list[dict] = []
    # rglob is recursive; uses depth-first. Safe for vault sizes <10k files.
    for conflict in vault_root.rglob("*"):
        if not conflict.is_file():
            continue
        name_lower = conflict.name.lower()
        if _CONFLICT_MARKER.lower() not in name_lower:
            continue
        base = _infer_conflict_base_path(conflict)
        out.append({
            "conflict": conflict,
            "base": base,
            "rel": conflict.relative_to(vault_root),
        })
    return out


def _infer_conflict_base_path(conflict: Path) -> Path:
    """Strip GDrive conflict markers from a filename to derive the base path.

    Examples:
        "PLAN (conflicted copy 2026-05-27).md"          → "PLAN.md"
        "PLAN (conflicted copy 2026-05-27) - Mac.md"    → "PLAN.md"
        "PLAN (conflicted copy 2026-05-27 from iPad).md" → "PLAN.md"

    Heuristic: regex-strip ` (conflicted copy ...).<ext>` segment.
    Returns Path with the same parent + stripped basename.
    """
    import re
    pattern = re.compile(
        r"\s*\(conflicted copy[^)]*\)(\s*-\s*[^.]+)?",
        re.IGNORECASE,
    )
    cleaned = pattern.sub("", conflict.name)
    return conflict.parent / cleaned


def toolkit_scripts_dir() -> Optional[Path]:
    """Locate the memory skill scripts dir, or None if not installed.

    Resolution order (V4 #36 / v4.0.0+):
      1. HARNESS_MEMORY_TOOLKIT_PATH env (override, used by tests)
      2. <harness_repo>/harness/skills/memory/scripts/      (v4.0.0+ canonical:
                                                              memory skill
                                                              ships with agentm)
      3. <harness_repo>/../crickets/skills/memory/scripts/  (legacy v3.x sibling
                                                              clone — kept for
                                                              backward-compat
                                                              with operators on
                                                              old crickets
                                                              v1.x catalogs)
      4. ~/Antigravity/crickets/skills/memory/scripts/      (legacy v3.x
                                                              canonical install)
    """
    override = os.environ.get("HARNESS_MEMORY_TOOLKIT_PATH", "").strip()
    if override:
        p = Path(override).expanduser()
        return p if p.is_dir() else None

    # v4.0.0+ canonical: memory skill lives at harness/skills/memory/ in agentm.
    harness_root = _HERE.parent
    local = harness_root / "harness" / "skills" / "memory" / "scripts"
    if local.is_dir():
        return local

    # Legacy v3.x sibling clone (memory skill was in crickets).
    sibling = harness_root.parent / "crickets" / "skills" / "memory" / "scripts"
    if sibling.is_dir():
        return sibling

    # Legacy v3.x canonical install path.
    canonical = Path.home() / "Antigravity" / "crickets" / "skills" / "memory" / "scripts"
    if canonical.is_dir():
        return canonical

    return None


# -----------------------------------------------------------------------------
# recall
# -----------------------------------------------------------------------------

def phase_budget(phase: str, arg_budget: Optional[int] = None) -> int:
    """Resolve recall budget for `phase`.

    Order: --budget arg > HARNESS_RECALL_BUDGET_<PHASE> env > default.
    """
    if arg_budget is not None and arg_budget > 0:
        return arg_budget
    env_key = f"HARNESS_RECALL_BUDGET_{phase.upper()}"
    raw = os.environ.get(env_key, "").strip()
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_BUDGETS.get(phase, 4000)


def _approx_token_count(text: str) -> int:
    """Approximate token count (chars / 4). Deterministic, cheap, no model needed."""
    return max(1, len(text) // 4)


def _truncate_to_budget(parts: list[str], budget: int) -> list[str]:
    """Pop entries off the end until total approx-tokens ≤ budget."""
    total = sum(_approx_token_count(p) for p in parts)
    while parts and total > budget:
        dropped = parts.pop()
        total -= _approx_token_count(dropped)
    return parts


def _format_entry(label: str, body: str) -> str:
    return f"### {label}\n\n{body.rstrip()}\n"


def _load_always_load(vault: Path, permanent_only: bool) -> list[str]:
    """Return the list of always-load markdown bodies (one per file).

    Permanent-only is a no-op for always-load (they're always permanent by
    design — that's the whole point of the directory).
    """
    al = vault / _ALWAYS_LOAD_REL[0] / _ALWAYS_LOAD_REL[1]
    if not al.is_dir():
        return []
    out: list[str] = []
    for path in sorted(al.glob("*.md")):
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append(_format_entry(f"always-load: {path.stem}", body))
    return out


def _load_project_entries(
    vault: Path,
    project: str,
    phase: str,
    permanent_only: bool,
) -> list[str]:
    """Return per-project entries scoped to the phase.

    Prefers the new V4 #26 `<vault>/projects/<slug>/` layout; falls back to
    the legacy `<vault>/personal-projects/<slug>/` if the operator hasn't
    run the vault rename yet.
    """
    base = _vault_projects_dir(vault) / project
    if not base.is_dir():
        return []

    out: list[str] = []
    rels = _PHASE_PROJECT_DIRS.get(phase, ())
    for rel in rels:
        target = base / rel
        if target.is_file() and target.suffix == ".md":
            try:
                body = target.read_text(encoding="utf-8")
            except OSError:
                continue
            out.append(_format_entry(f"{project}/{rel}", body))
        elif target.is_dir():
            for path in sorted(target.glob("*.md")):
                # Permanent-only: skip files marked ephemeral via convention.
                # Convention: filenames under `daily/` or starting with `_inbox-`
                # are ephemeral. Per-phase dirs we walk here are by definition
                # permanent (decisions/, open-questions/, known-issues/), so
                # permanent_only is mostly a no-op at this stage — but we still
                # honor it for forward-compat.
                if permanent_only and path.name.startswith("_inbox-"):
                    continue
                try:
                    body = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                out.append(_format_entry(f"{project}/{rel}/{path.stem}", body))
    return out


def phase_recall(
    phase: str,
    project: Optional[str],
    *,
    budget: Optional[int] = None,
    permanent_only: bool = False,
    project_first: bool = False,
) -> str:
    """Return a markdown summary of recall context for `phase` + `project`.

    Empty string if vault unavailable (graceful-skip).

    `project_first` (V4 #35): when True, per-project entries (decisions, _index,
    …) are emitted BEFORE the operator-global always-load conventions, so they
    survive budget truncation (which drops from the end). The documenter phase
    sets this — its raison d'être is the project's settled decisions, which must
    not be the first thing cut when the convention set is large. Other phases
    keep always-load-first (their global conventions are the priority).
    """
    if phase not in _VALID_PHASES:
        raise ValueError(f"unknown phase: {phase!r}")
    v = vault_path()
    if v is None:
        return ""

    always = _load_always_load(v, permanent_only)
    project_parts = (
        _load_project_entries(v, project, phase, permanent_only) if project else []
    )
    parts: list[str] = []
    if project_first:
        parts.extend(project_parts)
        parts.extend(always)
    else:
        parts.extend(always)
        parts.extend(project_parts)

    if not parts:
        return ""

    cap = phase_budget(phase, budget)
    parts = _truncate_to_budget(parts, cap)

    header = (
        f"# Auto-context recall — phase: {phase}, project: {project or '(unknown)'}\n"
        f"(budget: ~{cap} tokens, entries: {len(parts)})\n\n"
    )
    return header + "\n".join(parts) + "\n"


# -----------------------------------------------------------------------------
# documenter-context (V4 #35)
# -----------------------------------------------------------------------------

def _load_md_dir(directory: Path) -> list[dict]:
    """Return `[{"name": <stem>, "body": <text>}]` for every readable `*.md`
    under `directory`, sorted by name. Empty list if the dir is absent.
    Unreadable files are skipped silently (graceful — never raises)."""
    out: list[dict] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.md")):
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append({"name": path.stem, "body": body})
    return out


def resolve_documenter_context(slug: str) -> Optional[dict]:
    """Structured documenter-time recall bundle for `slug` (V4 #35).

    The structured counterpart to ``phase_recall("documenter", ...)``: where
    `phase_recall` returns a flat markdown blob, this returns a typed dict so
    programmatic callers (the `documenter-context --format json` path, future
    primitives) can address each context source independently.

    Returned shape::

        {
            "slug":                 <slug>,
            "registered":           bool,   # projects/<slug>/ dir exists in vault
            "operator_conventions": [{"name": str, "body": str}, ...],  # _always-load/
            "project_decisions":    [{"name": str, "body": str}, ...],  # decisions/
            "project_anchor":       Optional[str],   # abs path to projects/<slug>/_index.md
            "wiki_style":           [{"name": str, "body": str}, ...],  # wiki-style/ (optional)
        }

    Graceful-skip contract:

    - **Vault unavailable** (``vault_path()`` is None) → returns ``None``. The
      caller detects this via ``is None`` and falls back to repo-local context.
    - **Slug not registered** (``projects/<slug>/`` dir absent) → returns the
      dict with ``registered=False``, empty ``project_decisions``/``wiki_style``,
      and ``project_anchor=None``. ``operator_conventions`` still loads, since
      ``_always-load/`` conventions apply globally regardless of project.

    Never raises on I/O errors — unreadable files are skipped.
    """
    v = vault_path()
    if v is None:
        return None

    bundle: dict = {
        "slug": slug,
        "registered": False,
        "operator_conventions": [],
        "project_decisions": [],
        "project_anchor": None,
        "wiki_style": [],
    }

    # Operator-global conventions — always loaded (apply across every project).
    bundle["operator_conventions"] = _load_md_dir(
        v / _ALWAYS_LOAD_REL[0] / _ALWAYS_LOAD_REL[1]
    )

    base = _vault_projects_dir(v) / slug
    if not base.is_dir():
        # Slug not registered — project-specific bundle stays empty.
        return bundle

    bundle["registered"] = True

    anchor = base / "_index.md"
    if anchor.is_file():
        bundle["project_anchor"] = str(anchor)

    bundle["project_decisions"] = _load_md_dir(base / "decisions")
    bundle["wiki_style"] = _load_md_dir(base / "wiki-style")
    return bundle


def documenter_context(slug: str, *, budget: Optional[int] = None, fmt: str = "text") -> tuple[str, int]:
    """Render the documenter-context bundle for `slug`; return `(output, exit_code)`.

    Exit-code contract (per V4 #35 plan task 2):

    - ``1`` — vault unavailable. Output is empty.
    - ``2`` — vault reachable but `slug` not registered. Output still carries any
      operator-global conventions (text via `phase_recall`, or the JSON bundle);
      the non-zero code tells the caller the *project-specific* context was absent
      so it can layer repo-local fallback on top.
    - ``0`` — success; bundle rendered.

    `fmt="text"` reuses `phase_recall("documenter", ...)` for the flat markdown
    rendering (so the documenter sub-agent gets the same shape every other phase
    emits). `fmt="json"` emits `resolve_documenter_context()`'s structured dict.
    """
    bundle = resolve_documenter_context(slug)
    if bundle is None:
        return "", 1

    if fmt == "json":
        output = json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"
    else:
        # project_first=True (V4 #35 task-5 dogfood fix): emit project decisions
        # before always-load so they survive the budget when conventions are many.
        output = phase_recall("documenter", slug, budget=budget, project_first=True)

    return output, (0 if bundle["registered"] else 2)


# -----------------------------------------------------------------------------
# offer-save
# -----------------------------------------------------------------------------

def confidence_threshold() -> float:
    """Resolve HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD env (default 0.8)."""
    raw = os.environ.get("HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD", "").strip()
    if not raw:
        return 0.8
    try:
        v = float(raw)
    except ValueError:
        return 0.8
    if not 0.0 <= v <= 1.0:
        return 0.8
    return v


def auto_save_mode() -> str:
    """Resolve HARNESS_AUTO_SAVE_MODE env (default 'ask'). Returns one of ask|silent|off."""
    raw = (os.environ.get("HARNESS_AUTO_SAVE_MODE") or "ask").strip().lower()
    if raw not in ("ask", "silent", "off"):
        return "ask"
    return raw


def should_prompt(
    confidence: Optional[float],
    *,
    mode: Optional[str] = None,
    threshold: Optional[float] = None,
) -> bool:
    """Decide whether `offer-save` should fire an interactive prompt.

    - mode == 'off' → save never happens (caller checks separately)
    - mode == 'silent' → never prompt (always save)
    - mode == 'ask' (default) → confidence-modulated:
        * confidence is None OR < threshold → prompt
        * confidence >= threshold → no prompt (silent save with stderr notice)
    """
    m = (mode or auto_save_mode()).lower()
    if m == "silent":
        return False
    if m == "off":
        # Caller should short-circuit before reaching here, but treat as no-prompt.
        return False
    # ask mode
    thr = threshold if threshold is not None else confidence_threshold()
    if confidence is None:
        return True
    return confidence < thr


def _invoke_toolkit_save(
    *,
    vault: Path,
    project: str,
    kind: str,
    slug: str,
    body: str,
) -> int:
    """Shell out to toolkit `save.py`. Returns the subprocess return code.

    Returns 127 (POSIX convention) if the toolkit is not installed.
    """
    tk = toolkit_scripts_dir()
    if tk is None:
        return 127
    save_py = tk / "save.py"
    if not save_py.is_file():
        return 127
    # Resolve which projects-dir segment to use (post-V4 #26 "projects" preferred;
    # falls back to legacy "personal-projects" if rename not yet run).
    projects_segment = _vault_projects_dir(vault).name
    group = f"{projects_segment}/{project}"
    cmd = [
        sys.executable,
        str(save_py),
        "--vault-path", str(vault),
        "--group", group,
        "--body-file", "-",
        kind,
        slug,
    ]
    try:
        result = subprocess.run(
            cmd,
            input=body,
            text=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[harness_memory] toolkit save invocation failed: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        # Surface toolkit-side error to operator.
        if result.stderr:
            sys.stderr.write(result.stderr)
    return result.returncode


def offer_save(
    *,
    phase: str,
    project: str,
    kind: str,
    slug: str,
    body: str,
    confidence: Optional[float] = None,
    confidence_reason: Optional[str] = None,
    stdin=None,
    stdout=None,
    stderr=None,
) -> int:
    """Implement `offer-save`. Returns 0 always on graceful-skip / op-skipped /
    success; non-zero only on a toolkit-save error.
    """
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr

    v = vault_path()
    if v is None:
        return 0  # graceful-skip

    mode = auto_save_mode()
    if mode == "off":
        print(f"[harness_memory] HARNESS_AUTO_SAVE_MODE=off — skip save: {kind}/{slug}", file=stderr)
        return 0

    if should_prompt(confidence, mode=mode):
        # Preview + prompt
        thr = confidence_threshold()
        projects_segment = _vault_projects_dir(v).name
        preview_lines = [
            f"--- offer-save preview ({phase} → {projects_segment}/{project}) ---",
            f"kind: {kind}",
            f"slug: {slug}",
        ]
        if confidence is not None:
            preview_lines.append(f"confidence: {confidence:.2f} (threshold: {thr:.2f})")
            if confidence_reason:
                preview_lines.append(f"reason: {confidence_reason}")
        preview_lines.append("body:")
        preview_lines.append("---")
        preview_lines.append(body.rstrip())
        preview_lines.append("---")
        preview_lines.append("")
        for line in preview_lines:
            print(line, file=stdout)

        # Non-TTY default: skip (never-silent-action contract).
        if not stdin.isatty():
            print(
                f"[harness_memory] non-TTY stdin — defaulting to SKIP for {kind}/{slug}",
                file=stderr,
            )
            return 0
        try:
            print("save this entry? [y/N] ", end="", file=stdout, flush=True)
            answer = stdin.readline().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(f"[harness_memory] skipped {kind}/{slug}", file=stderr)
            return 0
        if answer not in ("y", "yes"):
            print(f"[harness_memory] skipped {kind}/{slug}", file=stderr)
            return 0
    else:
        # High-confidence or silent mode — proceed without prompt.
        if confidence is not None and mode == "ask":
            thr = confidence_threshold()
            print(
                f"[auto-saved high-confidence] {kind}/{slug} "
                f"(confidence={confidence:.2f} ≥ threshold={thr:.2f})",
                file=stderr,
            )
        else:
            print(f"[harness_memory] silent save: {kind}/{slug}", file=stderr)

    rc = _invoke_toolkit_save(
        vault=v, project=project, kind=kind, slug=slug, body=body,
    )
    if rc == 127:
        # Toolkit absent — treat like graceful-skip on the save path.
        print(
            f"[harness_memory] toolkit not installed — recorded intent only "
            f"for {kind}/{slug}",
            file=stderr,
        )
        return 0
    return rc


# -----------------------------------------------------------------------------
# plan-done-promotion
# -----------------------------------------------------------------------------

def _cursor_path(project_root: Path) -> Path:
    return project_root / _CURSOR_REL[0] / _CURSOR_REL[1]


def _progress_path(project_root: Path) -> Path:
    return project_root / _PROGRESS_REL[0] / _PROGRESS_REL[1]


def read_cursor(project_root: Path) -> int:
    cp = _cursor_path(project_root)
    if not cp.is_file():
        return 0
    try:
        return int(cp.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def write_cursor(project_root: Path, offset: int) -> None:
    cp = _cursor_path(project_root)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_bytes((str(int(offset)) + "\n").encode("utf-8"))


def plan_done_promotion(
    project_root: Path,
    *,
    advance_cursor: bool = True,
) -> str:
    """Return the unpromoted tail of progress.md (past the cursor).

    Advances the cursor on success (unless advance_cursor=False, for dry-run).
    Graceful-skip on vault absent OR progress.md absent → returns empty string.

    Idempotent: re-invoking after a full read returns empty string (cursor
    at end-of-file).
    """
    if vault_path() is None:
        return ""
    prog = _progress_path(project_root)
    if not prog.is_file():
        return ""
    try:
        raw = prog.read_bytes()
    except OSError:
        return ""
    cursor = read_cursor(project_root)
    if cursor >= len(raw):
        return ""  # idempotent: already promoted up to end-of-file
    tail = raw[cursor:]
    if advance_cursor:
        write_cursor(project_root, len(raw))
    try:
        return tail.decode("utf-8")
    except UnicodeDecodeError:
        return tail.decode("utf-8", errors="replace")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness_memory",
        description=(
            "Auto-context dispatcher for harness phases. Graceful-skips when "
            "MemoryVault is not installed (MEMORY_VAULT_PATH unset or missing)."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # recall
    p_recall = sub.add_parser(
        "recall",
        help="emit phase-specific recall context to stdout",
    )
    p_recall.add_argument("--phase", required=True, choices=list(_VALID_PHASES))
    p_recall.add_argument("--project", required=False, default=None)
    p_recall.add_argument("--budget", type=int, default=None)
    p_recall.add_argument("--permanent-only", action="store_true")

    # offer-save
    p_save = sub.add_parser(
        "offer-save",
        help="preview + ask + save (self-modulating by --confidence)",
    )
    p_save.add_argument("--phase", required=True, choices=list(_VALID_PHASES))
    p_save.add_argument("--project", required=True)
    p_save.add_argument("--kind", required=True)
    p_save.add_argument("--slug", required=True)
    p_save.add_argument("--content-file", required=True,
                        help="path to file containing the entry body, or '-' for stdin")
    p_save.add_argument("--confidence", type=float, default=None)
    p_save.add_argument("--confidence-reason", type=str, default=None)

    # plan-done-promotion
    p_pdp = sub.add_parser(
        "plan-done-promotion",
        help="dump progress.md tail past cursor; advance cursor",
    )
    p_pdp.add_argument("--project-root", default=None,
                       help="path to project root (default: cwd)")
    p_pdp.add_argument("--dry-run", action="store_true",
                       help="emit tail without advancing the cursor")

    # available
    sub.add_parser("available", help="exit 0 iff vault is accessible")

    # V4 #23 task 5: phase-integration auto-dispatch. Shells out to the
    # auto-orchestration phase dispatcher (orchestration_phase.py). Always
    # exits 0 (graceful-skip / non-blocking) so a phase never wedges on it.
    p_phase = sub.add_parser(
        "phase-dispatch",
        help="auto-orchestration phase dispatch (post-work reflect / post-release refresh)",
    )
    p_phase.add_argument("phase", choices=["post-work", "post-release"],
                         help="which phase boundary fired")
    p_phase.add_argument("--project-root", default=None,
                         help="path to project root (default: cwd)")
    p_phase.add_argument("--dry-run", action="store_true",
                         help="print the resolved dispatch plan without executing")

    # V4 #37 task 7: dispatcher CLI for state-file reads/writes/path lookups.
    # Phase specs + bugfix pipeline invoke these instead of bare `Read .harness/<file>`
    # so the workflow actually uses the vault canonical path post-V4 #26.
    p_read_state = sub.add_parser(
        "read-state",
        help="read a project state file via the resolver (vault-first, legacy fallback)",
    )
    p_read_state.add_argument("filename", help="state file shortname (e.g. PLAN.md)")
    p_read_state.add_argument(
        "--project-root", default=None,
        help="path to project root (default: cwd); resolver auto-detects slug from here",
    )

    p_write_state = sub.add_parser(
        "write-state",
        help="write a project state file via the resolver (vault-only unless .project-mode=local)",
    )
    p_write_state.add_argument("filename", help="state file shortname (e.g. PLAN.md)")
    p_write_state.add_argument(
        "--project-root", default=None,
        help="path to project root (default: cwd)",
    )
    p_write_state.add_argument(
        "--content-file", default="-",
        help="path to file containing new content, or '-' for stdin (default)",
    )

    p_vsp = sub.add_parser(
        "vault-state-path",
        help="emit the resolved on-disk path for a state file",
    )
    p_vsp.add_argument("filename", help="state file shortname (e.g. PLAN.md)")
    p_vsp.add_argument(
        "--project-root", default=None,
        help="path to project root (default: cwd)",
    )

    # documenter-context (V4 #35): doc-write-time recall bundle for the
    # documenter sub-agent + wiki-author/diataxis-author skills. Composes
    # `recall` under the hood with phase=documenter; one subcommand for all
    # three primitives (locked design call #2 — not three separate verbs).
    p_dc = sub.add_parser(
        "documenter-context",
        help="emit doc-write-time recall bundle (operator conventions + project decisions)",
    )
    p_dc.add_argument("--slug", required=True,
                      help="project slug whose context to load")
    p_dc.add_argument("--budget", type=int, default=None,
                      help="recall budget in tokens (default: "
                           "HARNESS_RECALL_BUDGET_DOCUMENTER env or 4000)")
    p_dc.add_argument("--format", dest="fmt", choices=("text", "json"), default="text",
                      help="output format: flat markdown (text, default) or "
                           "structured JSON for programmatic callers")

    return parser


def phase_dispatch(*, phase: str, project_root: Optional[str], dry_run: bool) -> int:
    """Shell out to the auto-orchestration phase dispatcher (V4 #23 task 5).

    `phase` is 'post-work' (reflect the just-finished session, dedup-guarded vs
    the Stop hook) or 'post-release' (index-skills + discover-skills refresh).
    Always returns 0 — graceful-skip when the vault is unavailable, the toolkit
    isn't installed, or the dispatcher errors. NEVER blocks a phase.
    """
    v = vault_path()
    if v is None:
        return 0  # graceful-skip: no vault configured
    tk = toolkit_scripts_dir()
    if tk is None:
        return 0  # graceful-skip: memory toolkit not installed
    disp = tk / "orchestration_phase.py"
    if not disp.is_file():
        return 0
    cmd = [
        sys.executable, str(disp),
        "--vault-path", str(v),
        "--project-root", project_root or ".",
    ]
    if dry_run:
        cmd.append("--dry-run")
    cmd.append(phase)
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[harness_memory] phase-dispatch invocation failed: {exc}", file=sys.stderr)
        return 0  # non-blocking
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.returncode != 0 and result.stderr:
        sys.stderr.write(result.stderr)
    return 0  # always non-blocking


def _read_content_file(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.cmd == "available":
        return 0 if is_available() else 1

    if args.cmd == "phase-dispatch":
        return phase_dispatch(
            phase=args.phase,
            project_root=args.project_root,
            dry_run=args.dry_run,
        )

    if args.cmd == "recall":
        out = phase_recall(
            args.phase,
            args.project,
            budget=args.budget,
            permanent_only=args.permanent_only,
        )
        if out:
            sys.stdout.write(out)
        return 0

    if args.cmd == "offer-save":
        try:
            body = _read_content_file(args.content_file)
        except OSError as exc:
            print(f"[harness_memory] cannot read --content-file: {exc}", file=sys.stderr)
            return 2
        return offer_save(
            phase=args.phase,
            project=args.project,
            kind=args.kind,
            slug=args.slug,
            body=body,
            confidence=args.confidence,
            confidence_reason=args.confidence_reason,
        )

    if args.cmd == "plan-done-promotion":
        root = Path(args.project_root).expanduser() if args.project_root else Path.cwd()
        tail = plan_done_promotion(root, advance_cursor=not args.dry_run)
        if tail:
            sys.stdout.write(tail)
        return 0

    # V4 #37 task 7: dispatcher CLI subcommands. Phase specs invoke these
    # explicitly so the workflow uses the post-V4 #26 vault path via the
    # resolver chain (with legacy fallback + .project-mode=local opt-out).

    if args.cmd == "read-state":
        root = Path(args.project_root).expanduser() if args.project_root else Path.cwd()
        resolution = resolve_project({"cwd": root})
        content = read_state_file(resolution, args.filename)
        sys.stdout.write(content)
        return 0

    if args.cmd == "write-state":
        root = Path(args.project_root).expanduser() if args.project_root else Path.cwd()
        resolution = resolve_project({"cwd": root})
        try:
            content = _read_content_file(args.content_file)
        except OSError as exc:
            print(f"[harness_memory] cannot read --content-file: {exc}", file=sys.stderr)
            return 2
        try:
            path = write_state_file(resolution, args.filename, content)
        except ValueError as exc:
            # Resolver lacks vault_path (no slug, no vault) — surface error.
            print(f"[harness_memory] {exc}", file=sys.stderr)
            return 2
        print(str(path))
        return 0

    if args.cmd == "vault-state-path":
        root = Path(args.project_root).expanduser() if args.project_root else Path.cwd()
        resolution = resolve_project({"cwd": root})
        path = vault_state_path(resolution, args.filename)
        if path is None:
            # No vault_path — emit empty + non-zero exit (caller can graceful-skip).
            print("", end="")
            return 1
        print(str(path))
        return 0

    if args.cmd == "documenter-context":
        # V4 #35: rc 0 = bundle; rc 1 = vault unavailable; rc 2 = slug not registered.
        output, rc = documenter_context(args.slug, budget=args.budget, fmt=args.fmt)
        if output:
            sys.stdout.write(output)
        return rc

    # argparse should prevent this branch.
    return 2


if __name__ == "__main__":
    sys.exit(main())
