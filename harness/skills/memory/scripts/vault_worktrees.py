#!/usr/bin/env python3
"""Whether a git worktree has got into the vault.

The vault-worktree-guard hook refuses the tool calls that would make one. Some
routes reach no hook: a desktop session opened on a vault folder with the
worktree option on, `claude --worktree`, a subagent whose definition sets
worktree isolation, and a scheduled task with its worktree toggle on. This
module notices what got through, so the doctor and the morning note can say so
the same day, and before the daemon's next restart rather than after it.

THREE SIGNS, ANY ONE OF WHICH IS ENOUGH.

- **The vault repository lists a worktree besides its main working tree.**
  `git worktree list` shows it wherever it sits, under the vault or not.
- **A `.claude/worktrees/` folder under the vault root holds something.** This is
  where Claude Code and the desktop app put their worktrees. It catches one that
  belongs to some other repository but sits where Drive uploads it. The empty
  folder at the vault root, left from 2026-08-16, is not a sign.
- **The vault repository's config carries an `extensions.` key.** The desktop
  app sets `extensions.worktreeConfig` when it makes a worktree, and a raw
  `git worktree remove` leaves it behind. The daemon's git library refuses any
  extension it doesn't know, and so the daemon stops committing on its next
  restart (agentm issue #859). Of the three, this one does harm on its own.

Read-only. A registered worktree whose directory is gone is reported as
prunable; removing its record is the daemon's job, not a reader's.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

GIT_TIMEOUT = 10  # seconds, per git call


@dataclass(frozen=True)
class Report:
    vault: Path
    worktrees: tuple = ()      # (path, prunable) for each worktree besides the main one
    folders: tuple = ()        # non-empty `.claude/worktrees` folders under the vault root
    extensions: tuple = ()     # `extensions.<key>=<value>` lines from the repository's config
    unverified: tuple = field(default_factory=tuple)  # checks that could not run, and why

    @property
    def found(self) -> bool:
        return bool(self.worktrees or self.folders or self.extensions)


def _git_env() -> dict:
    return {k: v for k, v in os.environ.items()
            if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")}


def _git(where: Path, *args: str) -> "tuple[Optional[int], str]":
    try:
        proc = subprocess.run(["git", "-C", str(where), *args], env=_git_env(),
                              capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    return proc.returncode, proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)


def vault_root_of(memory_root: Path) -> Path:
    """The vault root for a memory root: the git working tree that holds it, or
    the memory root itself when it is in none."""
    rc, out = _git(Path(memory_root), "rev-parse", "--show-toplevel")
    if rc == 0 and out.strip():
        return Path(os.path.realpath(out.strip()))
    return Path(os.path.realpath(memory_root))


def _registered_worktrees(vault: Path) -> "tuple[Optional[tuple], str]":
    rc, out = _git(vault, "worktree", "list", "--porcelain")
    if rc != 0:
        return None, f"`git worktree list` failed: {out.strip() or 'no output'}"
    blocks = [b for b in out.strip().split("\n\n") if b.strip()]
    extra = []
    for block in blocks[1:]:  # the first block is always the main working tree
        lines = block.splitlines()
        path = next((ln.split(" ", 1)[1] for ln in lines if ln.startswith("worktree ")), "?")
        # git prints forward slashes on every platform; name it the way the OS does.
        extra.append((os.path.normpath(path), any(ln.startswith("prunable") for ln in lines)))
    return tuple(extra), ""


def _worktree_folders(vault: Path) -> tuple:
    found = []
    for dirpath, dirnames, _files in os.walk(vault):
        here = Path(dirpath)
        if here.name == "worktrees" and here.parent.name == ".claude":
            try:
                with os.scandir(here) as entries:
                    holds = any(True for _ in entries)
            except OSError:
                holds = False
            if holds:
                found.append(here)
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d != ".git"]
    return tuple(sorted(found))


def _config_extensions(vault: Path) -> "tuple[Optional[tuple], str]":
    rc, out = _git(vault, "config", "--local", "--get-regexp", r"^extensions\.")
    if rc == 1:
        return (), ""  # git's answer for "no such key"
    if rc != 0:
        return None, f"`git config` failed: {out.strip() or 'no output'}"
    return tuple(ln.strip().replace(" ", "=", 1) for ln in out.splitlines() if ln.strip()), ""


def inspect(vault: Path) -> Report:
    vault = Path(os.path.realpath(vault))
    unverified = []
    worktrees, why = _registered_worktrees(vault)
    if worktrees is None:
        unverified.append(why)
        worktrees = ()
    extensions, why = _config_extensions(vault)
    if extensions is None:
        unverified.append(why)
        extensions = ()
    return Report(vault=vault, worktrees=worktrees, folders=_worktree_folders(vault),
                  extensions=extensions, unverified=tuple(unverified))


def _rel(path, vault: Path) -> str:
    """A path inside the vault the way a vault link reads it; any other path in full."""
    try:
        return Path(path).relative_to(vault).as_posix()
    except ValueError:
        return str(path)


def describe(report: Report) -> list:
    """One phrase per sign found, in the order they matter."""
    out = []
    if report.extensions:
        out.append(f"its git config carries {', '.join(f'`{e}`' for e in report.extensions)}, which stops the "
                   "daemon committing on its next restart (#859)")
    for path, prunable in report.worktrees:
        out.append(f"git lists a worktree at `{_rel(path, report.vault)}`"
                   + (" whose directory is gone (prunable)" if prunable else ""))
    for folder in report.folders:
        out.append(f"`{_rel(folder, report.vault)}/` holds a worktree")
    return out
