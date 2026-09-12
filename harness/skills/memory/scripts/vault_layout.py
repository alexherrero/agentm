#!/usr/bin/env python3
"""vault_layout.py — where the memory root's non-class files live, across the
memory-root trims (agentm-vault plan 05, 2026-09-11).

The trims moved four families of files out of the memory root:

  the always-load pen   memory/_always-load/        -> <vault>/standards/
  the voice library     Projects/_global/wiki-style/ -> <vault>/standards/voice/
  a feature's state     memory/_watchlist/ (+ the skill watchlist and the
                        three settings files)        -> Projects/agentm/
  the engine's files    .heat.json .lifecycle.json
                        _meta/repos.json _dream/     -> the engine state dir

Every reader and writer of one of those files resolves it here, the same
way for both: the newest home first, the retired one as the fallback, and
the newest home when neither exists yet. Reads and writes going through one
function is what keeps a move from splitting the corpus — a writer that
still spelled the retired path would recreate it under the reader's feet the
first time it ran (the hazard named in the plan), and a reader pinned to the
new path would miss everything a not-yet-migrated vault still holds.

`root` is always the MEMORY root (`<vault>/Agent` on the shipped layout, the
vault itself on a flat one). `standards/` and `Projects/` sit beside it at
the vault root, so the sibling probe (`root.parent`) comes first and the
flat probe (`root`) second — the two-probe order recall.py's loader already
uses for standards/. The sibling is only believed when the parent looks like
an Obsidian vault (`.obsidian/` there, none at the root itself); a flat
vault's parent is the operator's home, where a `Projects` folder is common
and is not the vault's.

Stdlib-only, same-dir convention: the memory scripts import this beside
engine_state.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import engine_state  # noqa: E402

# The project every feature's working state files under. The watchlists and
# the settings files are agentm's own feature state, not memory (design
# § memory/ holds only the classes); you edit them in Obsidian, which is why
# they live in the vault's project space rather than the engine directory.
FEATURE_PROJECT = "agentm"
STANDARDS_DIRNAME = "standards"
VOICE_DIRNAME = "voice"
PROJECTS_DIRNAME = "Projects"

LEGACY_PEN_REL = ("memory", "_always-load")
LEGACY_VOICE_REL = ("_global", "wiki-style")  # under the projects space


def _sibling_witnessed(root: Path) -> bool:
    """Whether `root.parent` may be probed as the vault root at all: an
    Obsidian vault there (`.obsidian/` at the parent, none at the root), or
    a `standards/` directory beside the root — the loader's own contract,
    which read `<root>/../standards` before this module existed."""
    if (root / ".obsidian").is_dir():
        return False
    parent = root.parent
    return (parent / ".obsidian").is_dir() or (parent / STANDARDS_DIRNAME).is_dir()


def vault_root_candidates(root) -> list[Path]:
    """The vault root's two spellings, newest first: the sibling parent when
    the root is nested inside an Obsidian vault, then the root itself (a flat
    vault is both roots at once)."""
    r = Path(root)
    out = []
    if _sibling_witnessed(r):
        out.append(r.parent)
    out.append(r)
    return out


def _first_existing(cands: list[Path]) -> Path | None:
    for c in cands:
        if c.exists():
            return c
    return None


def _resolve(cands: list[Path]) -> Path:
    """The first candidate that exists, else the first (the current home)."""
    found = _first_existing(cands)
    return found if found is not None else cands[0]


# ── standards/ — the always-load tier ───────────────────────────────────────

def standards_dir_candidates(root) -> list[Path]:
    return [v / STANDARDS_DIRNAME for v in vault_root_candidates(root)]


def standards_dir(root) -> Path:
    """`<vault>/standards/` — the operator's rule files, loaded every session."""
    return _resolve(standards_dir_candidates(root))


def always_load_dirs(root) -> list[Path]:
    """Every directory the always-load tier is read from, in injection order:
    `standards/` first (the surface of record), then the retired pen while it
    still holds anything. The loader reads them in union and dedupes by stem,
    standards winning."""
    out: list[Path] = []
    s = _first_existing(standards_dir_candidates(root))
    if s is not None:
        out.append(s)
    pen = Path(root).joinpath(*LEGACY_PEN_REL)
    if pen.is_dir():
        out.append(pen)
    return out


def legacy_pen_dir(root) -> Path:
    """`memory/_always-load/`, the retired pen. Returned whether or not it
    exists; callers that would CREATE it must check first — nothing recreates
    the pen after the trims."""
    return Path(root).joinpath(*LEGACY_PEN_REL)


# ── standards/voice/ — the on-demand voice library ──────────────────────────

def voice_dir_candidates(root) -> list[Path]:
    cands = [s / VOICE_DIRNAME for s in standards_dir_candidates(root)]
    for p in projects_dir_candidates(root):
        cands.append(p.joinpath(*LEGACY_VOICE_REL))
    return cands


def voice_dir(root) -> Path:
    """`<vault>/standards/voice/`, falling back to the retired
    `Projects/_global/wiki-style/` while that is where the rules still are."""
    return _resolve(voice_dir_candidates(root))


# ── Projects/agentm/ — a feature's working state ────────────────────────────

def projects_dir_candidates(root) -> list[Path]:
    return [v / PROJECTS_DIRNAME for v in vault_root_candidates(root)]


def feature_state_candidates(root, name: str) -> list[Path]:
    """`Projects/agentm/<name>` first, then the retired `memory/<name>`."""
    cands = [p / FEATURE_PROJECT / name for p in projects_dir_candidates(root)]
    cands.append(Path(root) / "memory" / name)
    return cands


def feature_state_path(root, name: str) -> Path:
    """Where a feature's state file or directory lives — `_watchlist`,
    `_skill-watchlist`, `auto-orchestration-config.md`,
    `skill-discovery-sources.md`, `trusted-sources.md`,
    `forward-learning-sources.json`."""
    return _resolve(feature_state_candidates(root, name))


def feature_state_dir(root) -> Path:
    """The directory new feature state is created in: `Projects/agentm/`."""
    return projects_dir_candidates(root)[0] / FEATURE_PROJECT


# ── the engine directory — sidecars, the registry, the dream exhaust ────────

def sidecar_candidates(root, name: str) -> list[Path]:
    """The engine state dir first, then the memory root the sidecar sat at."""
    return [engine_state.engine_state_dir() / name, Path(root) / name]


def sidecar_path(root, name: str) -> Path:
    """`.heat.json` / `.lifecycle.json`: read and written where they are, and
    created in the engine directory when they are nowhere yet."""
    return _resolve(sidecar_candidates(root, name))


def registry_candidates(root) -> list[Path]:
    return [engine_state.engine_state_dir() / "repos.json",
            Path(root) / "_meta" / "repos.json"]


def dream_insights_dir() -> Path:
    """Where `_dream/insights/` went: the engine directory already held the
    same layer's `dream-insights/`."""
    return engine_state.engine_state_dir() / "dream-insights"


def env_memory_root() -> Path | None:
    """`$MEMORY_ROOT`, else its deprecated alias, as a Path or None."""
    raw = (os.environ.get("MEMORY_ROOT") or os.environ.get("MEMORY_VAULT_PATH", "")).strip()
    return Path(os.path.expanduser(raw)) if raw else None
