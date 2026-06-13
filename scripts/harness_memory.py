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
import re
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

# V5-0: the vault-write protocol lives in vault_lock (the Phase-0 concurrency
# floor). harness_memory routes its writes through it and re-exports the error
# vocabulary so the codebase keeps ONE ConcurrentModificationError.
from vault_lock import (  # noqa: E402
    ConcurrentModificationError,
    atomic_write,
    content_hash,
    vault_mutex,
)


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
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
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
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
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


def harness_state_dir(resolution: dict) -> Optional[Path]:
    """Return the `_harness/` directory to enumerate for this project, honoring
    state mode — the directory companion to `vault_state_path` (which returns a
    single file path).

    - **local mode** (`.project-mode` = "local") → `<project_root>/.harness/`.
    - **vault mode** (default) → `<vault_path>/_harness/`.
    - returns None when neither resolves (a vault-mode project with no vault_path).

    Used by `queue_status_lite` and named-plan-aware session-start discovery to
    glob every `PLAN*.md`. Pure path-construction; does not check existence.
    """
    if _read_project_mode(resolution) == "local":
        root = resolution.get("project_root") or Path.cwd()
        return Path(root) / ".harness"
    vault_p = resolution.get("vault_path")
    return Path(vault_p) / "_harness" if vault_p else None


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
    # V5-0: shared-vault write — acquire the one per-vault advisory mutex, then
    # land atomically (temp→fsync→rename) via the canonical writer. The mutex
    # serializes concurrent writers so two atomic_write calls to the same target
    # never collide on the shared `<target>.tmp` path (R4 rule 2 + DC-2).
    with vault_mutex(vault_p):
        atomic_write(target, content)
    return target


def _write_repo_local_state_file(project_root: Path, filename: str, content: str) -> Path:
    """Local-mode write path: write to <project_root>/.harness/<filename>.

    Used when the effective `.project-mode` = "local" (DC-2/DC-3). Same
    atomic-write semantics as the vault path. Works with no vault configured.
    """
    target = Path(project_root) / ".harness" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    # V5-0: repo-local path (not the shared GDrive vault) — partitioned by
    # construction (each checkout owns its own .harness/), so NO vault mutex.
    # Still route through atomic_write to gain the fsync + bytes-mode LF the
    # bare write_text lacked (DC-2: torn-write safety without needless locking).
    return atomic_write(target, content)


# -----------------------------------------------------------------------------
# Active-plan resolution (V5-10 part 1: named multi-plan state)
# -----------------------------------------------------------------------------

# A worker session works exactly one plan. With named plans (`PLAN-<name>.md` /
# `progress-<name>.md`, flat in the shared vault `_harness/`), *which* plan a
# session owns is resolved here: explicit arg → a sticky worktree-local marker →
# the legacy singleton. The marker is WRITTEN by the worktree-spawn helper (V5-10
# component 2, a later slice); this is the READER plus its loud-error contract.

_PLAN_PREFIX = "PLAN-"
_PROGRESS_PREFIX = "progress-"
_SINGLETON_PLAN = ("PLAN.md", "progress.md")


class ActivePlanError(RuntimeError):
    """The `.harness/active-plan` marker exists but does not resolve to a present,
    non-empty `PLAN-<name>.md`.

    Raised instead of silently falling back to the singleton `PLAN.md`. A worktree
    bound to plan "foo" whose plan file vanished must NOT quietly run whatever
    `PLAN.md` happens to be in `_harness/` — that is exactly the worker→plan
    mis-binding the V5-10 design calls out (Risk #7). Fail loud so the operator
    fixes the binding rather than letting two workers stomp one plan.
    """


def _read_active_plan_marker(project_root: Path) -> tuple[bool, Optional[str]]:
    """Read `<project_root>/.harness/active-plan`.

    Returns `(present, raw)`:
      - `(False, None)` — the marker file is absent (→ singleton default).
      - `(True, "<text>")` — the file exists; `raw` is its stripped contents
        ("" if blank). **Case-preserving** (unlike `_read_mode_marker`, which
        lowercases for `.project-mode`) — plan slugs may be case-sensitive.

    The present-vs-absent distinction is load-bearing: an *absent* marker is the
    back-compat singleton path, but a *present-but-blank* marker is a dangling
    binding the caller must treat as an error, not as "no marker".
    """
    path = Path(project_root) / ".harness" / "active-plan"
    if not path.is_file():
        return (False, None)
    try:
        return (True, path.read_text(encoding="utf-8").strip())
    except OSError as exc:
        raise ActivePlanError(
            f"cannot read {path}: {exc}. Refusing to fall back to the singleton "
            f"PLAN.md while an active-plan marker is present."
        ) from exc


def _normalize_plan_name(raw: str) -> Optional[str]:
    """Reduce a plan reference to its bare slug.

    Accepts the slug ("foo"), the filename ("PLAN-foo.md"), or the stem
    ("PLAN-foo") — all yield "foo". Returns None for an empty string or an
    explicit singleton reference ("PLAN" / "PLAN.md"), signalling "no named plan"
    so the caller falls through to the unnamed default.
    """
    s = (raw or "").strip()
    if s.endswith(".md"):
        s = s[: -len(".md")]
    if s.startswith(_PLAN_PREFIX):
        s = s[len(_PLAN_PREFIX):]
    if not s or s == "PLAN":
        return None
    return s


def _is_safe_plan_slug(slug: str) -> bool:
    """A plan slug must be a single path component so it can't escape `_harness/`
    when interpolated into `PLAN-<slug>.md`. Rejects separators and parent refs."""
    return (
        slug not in (".", "..")
        and "/" not in slug
        and "\\" not in slug
        and "\x00" not in slug
    )


def _plan_pair(slug: Optional[str]) -> tuple[str, str]:
    """Map a normalized slug to its `(plan_filename, progress_filename)` pair.
    `None` → the singleton; "foo" → ("PLAN-foo.md", "progress-foo.md")."""
    if slug is None:
        return _SINGLETON_PLAN
    return (f"{_PLAN_PREFIX}{slug}.md", f"{_PROGRESS_PREFIX}{slug}.md")


def resolve_active_plan(
    resolution: dict, *, plan_arg: Optional[str] = None
) -> tuple[str, str]:
    """Resolve which `(plan_filename, progress_filename)` pair this session owns.

    Precedence — first hit wins:

      1. **explicit `plan_arg`** — the caller named a plan (e.g. `/work foo`).
         Normalized, so "foo" / "PLAN-foo.md" / "PLAN-foo" all map to the same
         pair; an arg that normalizes to the singleton ("PLAN" / "PLAN.md" / "")
         yields the unnamed pair. No existence check — naming a plan explicitly
         is the caller's deliberate choice. Raises ``ValueError`` on a slug that
         is not a single path component (traversal guard).
      2. **worktree-local `<project_root>/.harness/active-plan`** — the sticky
         binding written by the worktree-spawn helper (V5-10 component 2). If the
         file is **present**, it MUST resolve to a present, non-empty
         `PLAN-<name>.md` in the resolved `_harness/`; otherwise ``ActivePlanError``.
         A present-but-blank, malformed, or dangling marker never degrades to the
         singleton (Risk #7).
      3. **legacy singleton** — no arg, no marker file → ``("PLAN.md", "progress.md")``.

    Reader only: never writes the marker (component 2 owns the writer). Returns a
    `(plan, progress)` tuple of bare filenames — resolve to a path with
    ``vault_state_path(resolution, plan)`` or read with ``read_state_file``.
    """
    # 1. Explicit arg — highest precedence, the caller's deliberate choice.
    if plan_arg is not None:
        slug = _normalize_plan_name(plan_arg)
        if slug is not None and not _is_safe_plan_slug(slug):
            raise ValueError(
                f"unsafe plan name {plan_arg!r}: a plan slug must be a single "
                f"path component (no '/', '\\', or '..')."
            )
        return _plan_pair(slug)

    # 2. Worktree-local sticky binding. Present ⇒ must resolve, else raise loud.
    project_root = Path(resolution.get("project_root") or Path.cwd())
    present, raw = _read_active_plan_marker(project_root)
    if present:
        slug = _normalize_plan_name(raw or "")
        if slug is None or not _is_safe_plan_slug(slug):
            raise ActivePlanError(
                f".harness/active-plan exists in {project_root} but is blank or "
                f"names no usable plan (raw={raw!r}). A present-but-dangling "
                f"binding must not silently fall back to the singleton PLAN.md "
                f"(V5-10 Risk #7). Write a plan name into the marker, or remove "
                f"it to use PLAN.md."
            )
        plan_name, progress_name = _plan_pair(slug)
        if not read_state_file(resolution, plan_name).strip():
            raise ActivePlanError(
                f".harness/active-plan binds this session to {plan_name!r}, but "
                f"that plan is absent or empty in the resolved _harness/ "
                f"(slug={resolution.get('slug')!r}). Refusing to fall back to the "
                f"singleton PLAN.md — that would mis-bind this worker to another "
                f"plan. Restore {plan_name!r} or fix the marker."
            )
        return (plan_name, progress_name)

    # 3. Legacy singleton default — no arg, no marker.
    return _SINGLETON_PLAN


# -----------------------------------------------------------------------------
# Concurrency primitives (V4 #26 task 4; V5-0 content-hash CAS + atomic_write)
# -----------------------------------------------------------------------------

# `ConcurrentModificationError` is imported from vault_lock (above) — the
# canonical home since V5-0. Re-exported here so existing callers that catch
# `harness_memory.ConcurrentModificationError` keep working unchanged.


def safe_write_replace_style(
    path: Path,
    new_content: str,
    *,
    expected_hash: Optional[str] = None,
    expected_mtime: Optional[float] = None,
) -> Path:
    """Write `new_content` to `path` atomically with an optional CAS check.

    Compare-and-swap modes (mutually exclusive — `expected_hash` wins if both
    are given):
      - `expected_hash is not None` (V5-0, preferred): pre-write check — re-read
        `path`, sha256 it, and raise ConcurrentModificationError if it differs
        from `expected_hash` (or if the file vanished since read). This is the
        R4 rule-4 currency: content hash does not lie, whereas mtime is weak on
        a GDrive-synced vault (re-downloads rewrite mtimes). Caller pattern:

            current_hash = content_hash(path.read_bytes()) if path.exists() else None
            # ... compute new_content ...
            safe_write_replace_style(path, new_content, expected_hash=current_hash)

      - `expected_mtime is not None` (DEPRECATED, retained for back-compat):
        pre-write check — re-stat `path`; raise if its mtime differs from
        expected. Superseded by `expected_hash`; kept working so any
        out-of-tree caller has a deprecation path.
      - neither set (default): plain atomic write, no concurrent-mod check.

    The write itself goes through `vault_lock.atomic_write` — bytes-mode
    temp→fsync→rename (LF preserved; gains the fsync the V4 inline writer
    lacked). Creates parent dir if absent. Returns the written path.

    Race window: between the CAS re-read and the rename another process could
    write. In V5-0 the per-vault `vault_mutex` closes that window for callers
    that hold it; standalone, the window is the rename interval (sub-ms).
    Suitable for replace-style files (PLAN.md, _index.md, features.json,
    FOLLOWUPS.md); NOT for append-only files (progress.md) — those use
    natural-merge via GDrive's append-handling.
    """
    path = Path(path)
    if expected_hash is not None:
        if path.exists():
            actual = content_hash(path.read_bytes())
            if actual != expected_hash:
                raise ConcurrentModificationError(
                    f"{path} was modified since read (expected hash={expected_hash[:12]}…, "
                    f"actual={actual[:12]}…). Another agent or device wrote to it. "
                    f"Re-read and re-apply changes."
                )
        else:
            # We held a content hash (file existed at read) but it's gone now —
            # a concurrent deletion is itself a conflict.
            raise ConcurrentModificationError(
                f"{path} was deleted since read (expected hash={expected_hash[:12]}…). "
                f"Another agent or device removed it. Re-read and re-apply changes."
            )
    elif expected_mtime is not None and path.exists():
        actual_mtime = path.stat().st_mtime
        if abs(actual_mtime - expected_mtime) > 1e-6:  # float-tolerance
            raise ConcurrentModificationError(
                f"{path} was modified since read (expected mtime={expected_mtime}, "
                f"actual={actual_mtime}). Another agent or device wrote to it. "
                f"Re-read and re-apply changes."
            )
    return atomic_write(path, new_content)


# Conflict / duplicate file naming families (V4 #26 → broadened V5-0 task 4).
#
# GDrive + DriveFS produce several distinct "this file collided" naming styles.
# The janitor sweep surfaces every one for operator triage — it never auto-
# deletes, so over-detection is cheap (the operator ignores a false hit) and
# under-detection is the costly failure (a real conflict goes unseen). The four
# families we recognize (all case-insensitive, tolerant of extra spaces):
#
#   1. "(conflicted copy …)" — GDrive's classic cross-device conflict marker,
#                              with/without a trailing "- <device>" / "from
#                              <device>" segment.
#         e.g.  PLAN (conflicted copy 2026-05-27) - Mac.md
#   2. "[Conflict]" / "[Conflict N]" — bracketed conflict marker.
#         e.g.  PLAN [Conflict].md , PLAN [Conflict 2].md
#   3. "Copy of <name>"      — GDrive "make a copy" / duplicate-on-collision.
#         e.g.  Copy of PLAN.md
#   4. "<name> (N).<ext>"    — numbered duplicate (DriveFS appends (1),(2),… when
#                              a same-named file already exists). Guarded against
#                              year-like false-positives ("budget (2026).xlsx")
#                              by only flagging when the de-numbered base co-exists
#                              in the same directory — exactly the signal DriveFS
#                              creates a "(1)" for.
#         e.g.  PLAN (1).md   (only when PLAN.md is present alongside)
#
# Plus an opt-in scan of the DriveFS `lost_and_found/` dump (orphaned files Drive
# could not re-home — it never notifies). Opt-in (the caller passes the root) so
# the function stays hermetic for unit tests and callers that only care about the
# in-vault sweep.
_CONFLICT_MARKER = "(conflicted copy"  # retained: the family-1 substring


def _conflict_family(name: str) -> Optional[str]:
    """Classify `name` into one of the four conflict/duplicate marker families,
    or None for a clean (non-colliding) name. Name-only — the co-existence guard
    for the numbered family is applied by the caller (it needs the directory)."""
    low = name.lower()
    if _CONFLICT_MARKER in low:
        return "conflicted-copy"
    if "[conflict" in low:
        return "bracket-conflict"
    if low.startswith("copy of "):
        return "copy-of"
    if re.search(r"\s+\(\d+\)(\.[^.]+)?$", name):
        return "numbered"
    return None


def _infer_conflict_base_path(conflict: Path) -> Path:
    """Strip whichever conflict/duplicate marker(s) a filename carries to derive
    the operator-canonical base path (same parent, cleaned basename).

    Examples:
        "PLAN (conflicted copy 2026-05-27).md"           → "PLAN.md"
        "PLAN (conflicted copy 2026-05-27) - Mac.md"     → "PLAN.md"
        "PLAN (conflicted copy 2026-05-27 from iPad).md" → "PLAN.md"
        "PLAN [Conflict].md"                             → "PLAN.md"
        "PLAN [Conflict 2].md"                           → "PLAN.md"
        "Copy of PLAN.md"                                → "PLAN.md"
        "PLAN (1).md"                                    → "PLAN.md"

    Each strip is a no-op when its marker is absent, so the rules compose:
    "Copy of PLAN (1).md" reduces to "PLAN.md". Best-effort — a name carrying no
    recognizable marker returns unchanged.
    """
    name = conflict.name
    # 1. GDrive "(conflicted copy …)" + optional "- <device>" tail.
    name = re.sub(
        r"\s*\(conflicted copy[^)]*\)(\s*-\s*[^.]+)?", "", name, flags=re.IGNORECASE,
    )
    # 2. Bracketed "[Conflict]" / "[Conflict N]" marker.
    name = re.sub(r"\s*\[conflict[^\]]*\]", "", name, flags=re.IGNORECASE)
    # 3. Trailing " (N)" numbered-duplicate, immediately before the extension.
    name = re.sub(r"\s+\(\d+\)(?=(\.[^.]+)?$)", "", name)
    # 4. Leading "Copy of ".
    name = re.sub(r"^copy of\s+", "", name, flags=re.IGNORECASE)
    return conflict.parent / name


def default_lost_and_found_root() -> Optional[Path]:
    """The platform DriveFS `lost_and_found/` directory, or None if absent.

    DriveFS dumps files it could not re-home into a `lost_and_found/` folder and
    raises no notification. The folder lives under the platform's app-data root:

        macOS    ~/Library/Application Support/Google/DriveFS/lost_and_found/
        Windows  %LOCALAPPDATA%\\Google\\DriveFS\\lost_and_found\\

    Both candidates are probed (they are mutually exclusive on a real machine)
    and the first that exists is returned, so the operator's dual macOS+Windows
    setup gets the sweep on *either* OS rather than macOS only (audit ML1). A
    machine with no DriveFS folder — Linux, or DriveFS not installed — gets None
    and simply no lost_and_found sweep. macOS resolves via `Path.home()` (honors
    `$HOME`) and Windows via `%LOCALAPPDATA%` (falling back to `~/AppData/Local`),
    so a redirected-env hook test stays hermetic against the real machine.
    """
    home = Path.home()
    candidates = [
        # macOS app-support tree.
        home / "Library" / "Application Support" / "Google" / "DriveFS" / "lost_and_found",
    ]
    # Windows: DriveFS lives under %LOCALAPPDATA% (normally USERPROFILE\AppData\
    # Local). Honor the env var for hermeticity; fall back to the conventional
    # location when it is unset.
    local_appdata = os.environ.get("LOCALAPPDATA")
    win_base = Path(local_appdata) if local_appdata else home / "AppData" / "Local"
    candidates.append(win_base / "Google" / "DriveFS" / "lost_and_found")

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def detect_conflict_files(
    vault_root: Path, *, lost_and_found_root: Optional[Path] = None,
) -> list[dict]:
    """Walk `vault_root` (and optionally a DriveFS `lost_and_found/` root) for
    conflict/duplicate files across all four marker families.

    Returns a list of dicts:
        [{"conflict": <conflict-file-path>,
          "base":     <inferred-base-file-path>,   # best-effort
          "rel":      <path relative to its own scan root>,
          "source":   "vault" | "lost_and_found"}, ...]

    The `base` field is the operator-canonical filename the conflict pairs with
    (marker stripped). For `lost_and_found` entries base inference is best-effort
    (the file is already orphaned out of its home directory).

    `lost_and_found_root` is opt-in: pass `default_lost_and_found_root()` (the
    hook does) to include the DriveFS dump, or leave it None to sweep only the
    vault. Keeping it opt-in keeps unit tests hermetic — they never touch the
    real `~/Library/.../lost_and_found`.

    The dispatcher hook (conflict-merger-session-start) walks this list at
    SessionStart and surfaces an operator-facing notice per entry.

    Detection is best-effort by design: GDrive may change a format without
    notice, so false-negatives are acceptable (the operator still finds the file
    in Obsidian). The numbered "(N)" family is guarded against year-like false-
    positives by requiring the de-numbered base to co-exist.
    """
    out: list[dict] = []

    vault_root = Path(vault_root)
    if vault_root.is_dir():
        # rglob is recursive, depth-first. Safe for vault sizes <10k files.
        for conflict in vault_root.rglob("*"):
            if not conflict.is_file():
                continue
            family = _conflict_family(conflict.name)
            if family is None:
                continue
            base = _infer_conflict_base_path(conflict)
            # Numbered "(N)" duplicates are real collisions only when the de-
            # numbered base co-exists — otherwise "report (2026).md" would be
            # flagged as a phantom conflict.
            if family == "numbered" and not base.exists():
                continue
            out.append({
                "conflict": conflict,
                "base": base,
                "rel": conflict.relative_to(vault_root),
                "source": "vault",
            })

    if lost_and_found_root is not None:
        laf = Path(lost_and_found_root)
        if laf.is_dir():
            for orphan in laf.rglob("*"):
                if not orphan.is_file():
                    continue
                # Every file DriveFS dumps here is orphaned — surface them all
                # for triage (no marker filter). Base inference is best-effort.
                out.append({
                    "conflict": orphan,
                    "base": _infer_conflict_base_path(orphan),
                    "rel": orphan.relative_to(laf),
                    "source": "lost_and_found",
                })

    return out


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
            "global_wiki_style":    [{"name": str, "body": str}, ...],  # projects/_global/wiki-style/
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
        "global_wiki_style": [],
        "project_decisions": [],
        "project_anchor": None,
        "wiki_style": [],
    }

    # Operator-global conventions — always loaded (apply across every project).
    bundle["operator_conventions"] = _load_md_dir(
        v / _ALWAYS_LOAD_REL[0] / _ALWAYS_LOAD_REL[1]
    )

    # Global ON-DEMAND wiki conventions — the reserved `_global` pseudo-project
    # under the top-level `projects/` root (NOT under personal-private/). Read
    # here so the relocation of global wiki conventions OUT of _always-load into
    # `_global` (crickets wiki-maintenance part 3) keeps surfacing to documenter-
    # time callers. Additive: the `_always-load` read above remains the transition
    # fallback for any un-relocated entries. Like `operator_conventions`, this is
    # slug-independent, so it loads even when the slug is unregistered. See ADR
    # 0010 (vault internal taxonomy) for why `_global` lives under `projects/`.
    bundle["global_wiki_style"] = _load_md_dir(
        _vault_projects_dir(v) / "_global" / "wiki-style"
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
    # V5-0: the promotion cursor is repo-local (<project_root>/.harness/), never
    # in the shared GDrive vault — partitioned by construction, so NO vault
    # mutex (mirrors _write_repo_local_state_file; DC-2). atomic_write gives the
    # fsync + atomic rename a bare write_bytes lacked.
    atomic_write(cp, str(int(offset)) + "\n")


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

    # resolve-active-plan (V5-10 part 1): emit the active (PLAN, progress) path
    # pair so the crickets developer-workflows phase specs can target named
    # plans without reimplementing resolution — they shell to this verb (the
    # function `resolve_active_plan` itself is not otherwise reachable from a
    # bash spec). Precedence is owned here: explicit --plan → worktree
    # active-plan marker → singleton.
    p_rap = sub.add_parser(
        "resolve-active-plan",
        help="emit the active (PLAN, progress) on-disk path pair for a named "
             "or singleton plan (V5-10 part 1)",
    )
    p_rap.add_argument(
        "--plan", default=None,
        help="explicit plan name/slug ('foo', 'PLAN-foo', 'PLAN-foo.md'); omit "
             "to resolve via the .harness/active-plan marker, else the singleton",
    )
    p_rap.add_argument(
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

    if args.cmd == "resolve-active-plan":
        # V5-10 part 1: emit the active (plan, progress) on-disk path pair as a
        # single tab-separated line — "<plan_path>\t<progress_path>". Honors the
        # effective state mode (vault vs local) via harness_state_dir, the same
        # dir read_state_file / write_state_file target. Exit codes:
        #   0 — resolved; pair printed.
        #   1 — no resolvable _harness/ dir (vault-mode project, no vault):
        #       graceful-skip signal, same convention as vault-state-path.
        #   2 — LOUD error: a dangling .harness/active-plan marker (Risk #7) or
        #       an unsafe plan slug. Never a silent singleton fallback.
        root = Path(args.project_root).expanduser() if args.project_root else Path.cwd()
        resolution = resolve_project({"cwd": root})
        try:
            plan_name, progress_name = resolve_active_plan(
                resolution, plan_arg=args.plan
            )
        except (ActivePlanError, ValueError) as exc:
            print(f"[harness_memory] {exc}", file=sys.stderr)
            return 2
        state_dir = harness_state_dir(resolution)
        if state_dir is None:
            print("", end="")
            return 1
        print(f"{state_dir / plan_name}\t{state_dir / progress_name}")
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
