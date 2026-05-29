#!/usr/bin/env python3
"""install_symlinks — symlink customizations from source clones into install prefix.

Source mode of V4 #30 plan #22 task 4. When the operator has source-clone
canonical paths for agentm + crickets, install symlinks the customizations
subset (per locked DC-7) so edits to the clone propagate live without
re-running the installer.

Symlinkable subset (DC-7):
- Skill dirs       (crickets/skills/<name>/  + agentm/adapters/claude-code/skills/<name>/)
- Agent .md files  (crickets/agents/<name>.md + agentm/harness/agents/<name>.md +
                    agentm/adapters/claude-code/agents/<name>.md)
- Command .md files (agentm/adapters/claude-code/commands/<name>.md)
- Hook bundles     (crickets/hooks/<name>/ — each hook is a dir bundle)
- User-scope helper scripts (agentm/templates/scripts/telemetry.sh →
                    <prefix>/scripts/telemetry.sh). telemetry roots across
                    multiple projects (`--all` walks ~/Antigravity etc.) so
                    a single user-scope copy is the right shape; pre-V4 #26
                    per-project copies in <project>/.harness/scripts/ are
                    legacy.

NOT symlinked (DC-8 — these merge with operator-edited content):
- settings.json fragments
- pre-push template

Conflict policy:
- Target absent          → create symlink ("created")
- Target IS symlink:
    - same target         → no-op ("skipped")
    - different target    → repoint ("repointed")
    - broken (target gone)→ repoint ("repointed")
- Target is real file/dir → conflict ("conflicts"); --force replaces (rm + symlink),
                            else skip with warn

Cross-platform:
- Unix: os.symlink (always works)
- Windows: os.symlink succeeds if developer mode is on (Win11 default) OR admin.
           If it fails on Windows + the target is a directory, fall back to
           junction points via `cmd /c mklink /J <link> <target>` (subprocess).
           Junctions are local-only (no cross-volume); fine for `<install-prefix>`
           which always sits under user home.

Idempotent. Re-running on already-symlinked target is a no-op.

Stdlib-only (ADR 0001). Per V4 #30 plan #22 task 4.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional


# -----------------------------------------------------------------------------
# Symlink mapping (per DC-7 + DC-8)
# -----------------------------------------------------------------------------

def symlink_targets_for_clone(
    clone_slug: str,
    clone_root: Path,
) -> list[tuple[Path, str, bool]]:
    """Return the list of symlink targets shipped by a given source clone.

    Each entry: (source_path, install_relative_path, is_dir)
      - source_path:           file/dir under the source clone
      - install_relative_path: where it should land under install_prefix
                               (e.g. "skills/pii-scrubber" or "agents/foo.md")
      - is_dir:                True for directory symlinks; False for files

    Returns [] for unknown slugs (defensive).

    **Single source of truth** for the install-prefix ↔ source-clone mapping
    (V4 #30 plan 3 of 3 task 3). Consumed by:
      - install_symlinks.symlink_customizations  — forward direction (create
        symlinks under <install-prefix>/ pointing at source-clone paths).
      - install_migrate.inverse_mapping_for_clones — inverse direction (given
        a file at <target>/.claude/<rel>, find the source-clone canonical
        path it should map to for SHA256 compare).

    Inversion happens at call time in install_migrate; no separate inverse
    table is maintained. This guarantees the two directions can never drift.
    """
    out: list[tuple[Path, str, bool]] = []

    if clone_slug == "crickets":
        # crickets/skills/<name>/  → skills/<name>
        skills_dir = clone_root / "skills"
        if skills_dir.is_dir():
            for child in sorted(skills_dir.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    out.append((child, f"skills/{child.name}", True))
        # crickets/hooks/<name>/   → hooks/<name>
        hooks_dir = clone_root / "hooks"
        if hooks_dir.is_dir():
            for child in sorted(hooks_dir.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    out.append((child, f"hooks/{child.name}", True))
        # crickets/agents/<name>.md → agents/<name>.md
        agents_dir = clone_root / "agents"
        if agents_dir.is_dir():
            for child in sorted(agents_dir.iterdir()):
                if child.is_file() and child.suffix == ".md":
                    out.append((child, f"agents/{child.name}", False))
        # crickets/commands/<name>.md → commands/<name>.md
        commands_dir = clone_root / "commands"
        if commands_dir.is_dir():
            for child in sorted(commands_dir.iterdir()):
                if child.is_file() and child.suffix == ".md":
                    out.append((child, f"commands/{child.name}", False))

    elif clone_slug == "agentm":
        # agentm/harness/agents/<name>.md → agents/<name>.md
        harness_agents = clone_root / "harness" / "agents"
        if harness_agents.is_dir():
            for child in sorted(harness_agents.iterdir()):
                if child.is_file() and child.suffix == ".md":
                    out.append((child, f"agents/{child.name}", False))
        # agentm/harness/skills/  — mixed dir-bundles + single-file .md skills
        #   <name>/   → skills/<name>/   (dir symlinks)
        #   <name>.md → skills/<name>.md (file symlinks)
        harness_skills = clone_root / "harness" / "skills"
        if harness_skills.is_dir():
            for child in sorted(harness_skills.iterdir()):
                if child.name.startswith("."):
                    continue
                if child.is_dir():
                    out.append((child, f"skills/{child.name}", True))
                elif child.is_file() and child.suffix == ".md":
                    out.append((child, f"skills/{child.name}", False))
        # agentm/harness/hooks/<name>/ → hooks/<name>/ (dir bundles only)
        harness_hooks = clone_root / "harness" / "hooks"
        if harness_hooks.is_dir():
            for child in sorted(harness_hooks.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    out.append((child, f"hooks/{child.name}", True))
        # agentm/templates/scripts/telemetry.sh → scripts/telemetry.sh
        # User-scope helper (multi-project scan); doctor checks <prefix>/scripts/.
        telemetry_src = clone_root / "templates" / "scripts" / "telemetry.sh"
        if telemetry_src.is_file():
            out.append((telemetry_src, "scripts/telemetry.sh", False))
        # agentm/adapters/claude-code/{skills,agents,commands}/
        ac_root = clone_root / "adapters" / "claude-code"
        for subdir, is_dir_kind in (("skills", True), ("agents", False), ("commands", False)):
            d = ac_root / subdir
            if not d.is_dir():
                continue
            for child in sorted(d.iterdir()):
                if is_dir_kind:
                    if child.is_dir() and not child.name.startswith("."):
                        out.append((child, f"{subdir}/{child.name}", True))
                else:
                    if child.is_file() and child.suffix == ".md":
                        out.append((child, f"{subdir}/{child.name}", False))

    return out


# -----------------------------------------------------------------------------
# Symlink primitive (cross-platform)
# -----------------------------------------------------------------------------

def _create_symlink(source: Path, link: Path, is_dir: bool) -> None:
    """Create a symlink from `link` to `source`.

    On Windows, falls back to junction points (`mklink /J`) for directories
    when os.symlink fails (typical when developer mode is off + not admin).

    Raises OSError on unrecoverable failure.
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(source, link, target_is_directory=is_dir)
    except OSError:
        if platform.system() == "Windows" and is_dir:
            # Fallback: junction. Note junctions are LOCAL-volume only; fine
            # for <install-prefix> which sits under user home.
            res = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(source)],
                capture_output=True, text=True, check=False,
            )
            if res.returncode != 0:
                raise OSError(
                    f"mklink /J failed: {res.stderr.strip() or res.stdout.strip()}"
                )
        else:
            raise


def _classify_existing(link: Path, expected_source: Path) -> str:
    """Classify the state of an existing target path at `link`.

    Returns one of:
      - "absent"            — link doesn't exist
      - "symlink-correct"   — link is a symlink pointing at expected_source
      - "symlink-wrong"     — link is a symlink pointing elsewhere
      - "symlink-broken"    — link is a symlink to a missing target
      - "real-conflict"     — link is a real file or directory (not a symlink)
    """
    # Use lstat to detect the symlink itself (not its target)
    if not link.exists() and not link.is_symlink():
        return "absent"
    if link.is_symlink():
        try:
            current = Path(os.readlink(link))
        except OSError:
            return "symlink-broken"
        # Resolve relative-to-link if necessary
        if not current.is_absolute():
            current = (link.parent / current).resolve()
        # Compare via os.path.samefile when both targets exist — handles
        # Windows UNC-prefix differences (//?/C:/... vs C:/...) that break
        # naive Path.resolve() equality. Falls back to resolve-compare if
        # samefile errors (one side absent → broken symlink).
        try:
            if current.exists() and expected_source.exists():
                if os.path.samefile(current, expected_source):
                    return "symlink-correct"
                return "symlink-wrong"
            if current.resolve() == expected_source.resolve():
                return "symlink-correct"
        except (OSError, FileNotFoundError):
            return "symlink-broken"
        # Pointing somewhere else
        return "symlink-wrong" if current.exists() else "symlink-broken"
    return "real-conflict"


# -----------------------------------------------------------------------------
# High-level operation
# -----------------------------------------------------------------------------

# Managed subdirs under install_prefix that this primitive owns. Orphan
# reaping (below) walks these and only these — anything outside is
# operator-owned and out of scope.
_MANAGED_SUBDIRS = ("agents", "commands", "skills", "hooks", "scripts")


def _normalize_path_str(s: str) -> str:
    """Normalize a path string for cross-platform parent/child comparison.

    Handles three Windows-specific quirks that break naive `startswith`:

    1. Extended-path prefix asymmetry — `os.readlink` and `Path.resolve` may
       emit `\\\\?\\C:\\...` on one side and `C:\\...` on the other (typically
       `resolve()` adds the prefix for paths that exist; `resolve(strict=False)`
       doesn't for paths that don't). Strip it so both sides compare equal.
    2. Case-insensitive filesystems (Windows, macOS HFS+ in default config) —
       `os.path.normcase` lowercases on Windows, no-op on POSIX.
    3. Separator normalization (mixed `\\` and `/`) — `os.path.normpath`
       collapses to the platform-native form.

    No-op on POSIX (no `\\\\?\\` prefix in real paths; normcase is identity).
    Test coverage in `scripts/test_install_symlinks.py::NormalizePathStrTests`
    exercises both platform shapes against raw strings (doesn't require
    Windows to verify).
    """
    if s.startswith("\\\\?\\"):
        s = s[4:]
    return os.path.normcase(os.path.normpath(s))


def _path_under(child: Path, root: Path) -> bool:
    """True if `child` is at or under `root`, robust to Windows path quirks.

    Used by `_reap_orphan_symlinks` to decide whether a broken-symlink target
    points into one of the source clones (and is thus our responsibility to
    reap) vs. an operator-placed symlink (which must be left alone).
    """
    c = _normalize_path_str(os.fspath(child))
    r = _normalize_path_str(os.fspath(root))
    if c == r:
        return True
    return c.startswith(r + os.sep)


def _reap_orphan_symlinks(
    install_prefix: Path,
    source_clones: dict[str, str],
    expected_links: set[Path],
) -> list[str]:
    """Unlink dead symlinks under install_prefix that point into a source clone.

    "Orphan" = a symlink whose target path is under one of source_clones
    AND no longer exists on disk. Typically arises when a skill / agent /
    command / hook file is deleted from the source clone; the prior install
    left a symlink that now dangles.

    Safety rules (intentionally narrow — never delete operator-placed files):
      - Only touches entries that are symlinks (`is_symlink()`). Real files
        and real dirs are operator-owned (DC-8) and left untouched.
      - Only reaps symlinks whose readlink target is under one of the
        source_clones roots. Symlinks pointing elsewhere (e.g. operator's own
        manual symlinks) are out of scope.
      - Only reaps when the target is genuinely absent. A live symlink whose
        target still exists is left alone (the caller's create loop already
        classified it as "skipped" or "repointed").
      - Skips entries already in `expected_links` (set of Paths the caller's
        current install just created/touched). Defense-in-depth against a
        race where the create loop wrote a symlink and reaping then tried
        to unlink it.

    Returns the list of install-relative paths that were unlinked.
    """
    clone_roots = [Path(p).resolve() for p in source_clones.values() if Path(p).is_dir()]
    if not clone_roots:
        return []

    reaped: list[str] = []
    for sub in _MANAGED_SUBDIRS:
        sub_dir = install_prefix / sub
        if not sub_dir.is_dir():
            continue
        for child in sorted(sub_dir.iterdir()):
            if child in expected_links:
                continue
            if not child.is_symlink():
                continue
            try:
                target = Path(os.readlink(child))
            except OSError:
                continue
            if not target.is_absolute():
                target = (child.parent / target)
            # Only reap symlinks pointing into one of the source clones.
            # Uses _path_under (NOT naive str.startswith) so the comparison
            # survives Windows path quirks: extended-path prefix asymmetry
            # (`\\?\C:\…` vs `C:\…`), case-insensitivity, and separator mixing.
            # Pre-fix the naive comparison silently no-op'd on Windows because
            # `clone_root.resolve()` added the `\\?\` prefix (clone exists) while
            # `target.resolve(strict=False)` did not (symlink target deleted) —
            # so startswith returned False and no orphan was ever reaped.
            try:
                target_resolved = target.resolve(strict=False)
            except (OSError, RuntimeError):
                continue
            in_clone = any(_path_under(target_resolved, r) for r in clone_roots)
            if not in_clone:
                continue
            # Only reap if the target is genuinely absent (broken link).
            if target.exists():
                continue
            try:
                child.unlink()
            except OSError:
                continue
            reaped.append(f"{sub}/{child.name}")
    return reaped


def symlink_customizations(
    source_clones: dict[str, str],
    install_prefix: Path | str,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Symlink customizations from detected source clones into install_prefix.

    Returns `{created, repointed, skipped, conflicts, reaped}` lists of
    install-relative paths. Caller can surface counts or per-entry detail.

    Idempotent — re-running on already-symlinked target classifies as
    "skipped" + does nothing.

    `force=True` replaces real files/dirs at conflict paths (rm + symlink).
    Defaults to False — operator sees warnings + can re-run with --force.

    Reaping: after the create/repoint loop, dead symlinks pointing into a
    source clone (whose target was deleted upstream) are unlinked. Operator-
    placed real files/symlinks are never touched. See `_reap_orphan_symlinks`.
    """
    prefix = Path(install_prefix)
    prefix.mkdir(parents=True, exist_ok=True)

    out: dict[str, list[str]] = {
        "created": [],
        "repointed": [],
        "skipped": [],
        "conflicts": [],
        "reaped": [],
    }
    expected_links: set[Path] = set()

    for slug, clone_root_str in source_clones.items():
        clone_root = Path(clone_root_str)
        if not clone_root.is_dir():
            continue
        for source_path, rel_install, is_dir in symlink_targets_for_clone(slug, clone_root):
            link = prefix / rel_install
            expected_links.add(link)
            state = _classify_existing(link, source_path)

            if state == "absent":
                _create_symlink(source_path, link, is_dir)
                out["created"].append(rel_install)
            elif state == "symlink-correct":
                out["skipped"].append(rel_install)
            elif state in ("symlink-wrong", "symlink-broken"):
                # Repoint: unlink existing symlink + create new
                link.unlink()
                _create_symlink(source_path, link, is_dir)
                out["repointed"].append(rel_install)
            elif state == "real-conflict":
                if force:
                    # Remove real file/dir, then symlink
                    if link.is_dir() and not link.is_symlink():
                        import shutil
                        shutil.rmtree(link)
                    else:
                        link.unlink()
                    _create_symlink(source_path, link, is_dir)
                    out["repointed"].append(rel_install)
                else:
                    out["conflicts"].append(rel_install)

    out["reaped"] = _reap_orphan_symlinks(prefix, source_clones, expected_links)
    return out


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Symlink customizations from source clones into install prefix.",
    )
    parser.add_argument("install_prefix", help="install destination (e.g. ~/.claude)")
    parser.add_argument(
        "--agentm", default=None,
        help="path to agentm source clone (omit if not present)",
    )
    parser.add_argument(
        "--crickets", default=None,
        help="path to crickets source clone (omit if not present)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="replace real files/dirs at target paths (default: skip with warn)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    source_clones: dict[str, str] = {}
    if args.agentm:
        source_clones["agentm"] = str(Path(os.path.expanduser(args.agentm)))
    if args.crickets:
        source_clones["crickets"] = str(Path(os.path.expanduser(args.crickets)))

    if not source_clones:
        print(
            "[install_symlinks] no source clones provided — nothing to symlink",
            file=sys.stderr,
        )
        return 1

    prefix = Path(os.path.expanduser(args.install_prefix))
    try:
        result = symlink_customizations(source_clones, prefix, force=args.force)
    except OSError as exc:
        print(f"[install_symlinks] symlink failed: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    if result["conflicts"]:
        print(
            f"[install_symlinks] {len(result['conflicts'])} conflict(s) skipped; "
            f"re-run with --force to replace real files/dirs",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
