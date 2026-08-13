#!/usr/bin/env python3
"""check-crickets-plugin-refs.py -- catch references to a crickets plugin that
cannot resolve.

crickets renames plugins (`wiki-maintenance` -> `wiki`,
`developer-workflows` -> `development-lifecycle`,
`releasing-conventions` -> `conventions`). agentm points at those plugins from
prose, links, and dispatch instructions, and nothing verified any of it -- so a
rename left agentm quietly wrong until someone followed a dead link. That is
what happened: two 404 links and six unresolvable dispatch instructions shipped
for weeks (fixed 2026-08-12).

**This gate checks the two shapes that actually break, and only those.**

  1. **Dead source links.** A GitHub URL into
     `alexherrero/crickets/{tree,blob}/main/src/<name>/...` where
     `src/<name>/` does not exist in the crickets checkout. A renamed plugin
     takes its directory with it, so every such link 404s.

  2. **Unresolvable plugin-qualified dispatch.** A `<name>:<primitive>`
     instruction (e.g. `wiki-maintenance:documenter`) where `<name>` is not a
     current crickets plugin. Hosts namespace a plugin's primitives by the
     PLUGIN name, so a stale qualifier names something no host can resolve --
     and the graceful-skip these surfaces promise degrades into a hard miss.

**Deliberately NOT checked: a bare old plugin name in prose.** crickets keeps
old names alive as declared capability *aliases* (`development-lifecycle`
declares both `developer-workflows` and `development-lifecycle`; `wiki` declares
`wiki-maintenance`; `conventions` declares `releasing-conventions`), so every
capability probe against an old name still resolves. Flagging bare names would
fight that deliberate backward-compatibility mechanism and bury the two real
failures in noise. This gate is not an old-name-usage linter -- it mirrors the
scope crickets' own `check-no-dangling-name.py` sets for itself: a reference is
a violation only when it resolves to **nothing**.

Graceful-skip when no crickets checkout is reachable (CI without the sibling, a
bare agentm clone): both checks need crickets' real plugin set as ground truth,
and guessing it from a hardcoded list is the drift this gate exists to prevent.
Resolution mirrors check-slop.py: $CRICKETS_REPO_ROOT, else the worktree-aware
sibling layout root via sibling_repo_root.

Run: `python3 scripts/check-crickets-plugin-refs.py`
Stdlib-only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import sibling_repo_root  # noqa: E402

ROOT = _HERE.parent
SELF = Path(__file__).resolve()

# Append-only historical records legitimately naming what a path once was.
EXCLUDED_FILES = {"CHANGELOG.md"}
TEXT_SUFFIXES = {".md", ".py", ".sh", ".ps1", ".json", ".toml", ".yml", ".yaml"}

SRC_URL_RE = re.compile(
    r"github\.com/alexherrero/crickets/(?:tree|blob)/[^/\s]+/src/([A-Za-z0-9_-]+)"
)
# `<plugin>:<primitive>` — plugin slugs are lowercase-hyphen by convention.
# Bounded on the left so a URL path segment or a python dict key can't match.
QUALIFIED_RE = re.compile(r"(?<![\w:/.-])([a-z][a-z0-9-]{2,}):([a-z][a-z0-9-]{2,})(?![\w/])")


def find_crickets_root() -> Path | None:
    """The crickets checkout, or None. Mirrors check-slop.py's ladder."""
    env_dir = os.environ.get("CRICKETS_REPO_ROOT", "").strip()
    candidates = []
    if env_dir:
        candidates.append(Path(os.path.expanduser(env_dir)))
    layout_root = sibling_repo_root.sibling_layout_root(_HERE)
    if layout_root is not None:
        candidates.append(layout_root / "crickets")
    for c in candidates:
        if (c / "src").is_dir() and (c / ".claude-plugin" / "marketplace.json").is_file():
            return c
    return None


def crickets_plugin_names(crickets: Path) -> set[str]:
    """Plugin names crickets currently offers, from its marketplace."""
    data = json.loads(
        (crickets / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    return {p["name"] for p in data.get("plugins", []) if isinstance(p, dict)}


def git_tracked(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return [p for p in out.stdout.split("\0") if p]


def iter_text_files(root: Path | None = None):
    # Resolved at call time, not bound as a default: the tests scan a synthetic
    # repo, and a `root: Path = ROOT` default would freeze the real repo in at
    # import time and silently ignore the argument.
    root = root if root is not None else ROOT
    for rel in git_tracked(root):
        path = root / rel
        if not path.is_file() or path.resolve() == SELF:
            continue
        if path.name in EXCLUDED_FILES or path.suffix not in TEXT_SUFFIXES:
            continue
        yield rel, path


def scan(crickets: Path, root: Path | None = None) -> list[str]:
    plugins = crickets_plugin_names(crickets)
    src_dir = crickets / "src"
    findings: list[str] = []
    for rel, path in iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name in SRC_URL_RE.findall(line):
                if not (src_dir / name).is_dir():
                    findings.append(
                        f"{rel}:{lineno}: dead crickets source link — "
                        f"src/{name}/ does not exist"
                    )
            for qualifier, primitive in QUALIFIED_RE.findall(line):
                # Only judge qualifiers that LOOK like a crickets plugin
                # reference: one that is not a current plugin but IS a
                # directory crickets used to ship is the rename case. A
                # qualifier crickets never had is someone else's namespace
                # (a URL scheme, a YAML key) — not this gate's business.
                if qualifier in plugins:
                    continue
                if qualifier in KNOWN_FORMER_PLUGINS:
                    findings.append(
                        f"{rel}:{lineno}: unresolvable dispatch "
                        f"'{qualifier}:{primitive}' — '{qualifier}' is not a "
                        f"current crickets plugin (renamed; hosts namespace by "
                        f"plugin name)"
                    )
    return findings


# Plugin names crickets has shipped and renamed away from. A stale qualifier
# built on one of these is the rename failure this gate exists to catch. Kept
# explicit rather than inferred: crickets deletes the old directory on rename,
# so there is nothing left on disk to infer it from.
KNOWN_FORMER_PLUGINS = {
    "developer-workflows",      # -> development-lifecycle
    "wiki-maintenance",         # -> wiki
    "releasing-conventions",    # -> conventions
    "documenting-conventions",  # -> conventions
}


def main() -> int:
    crickets = find_crickets_root()
    if crickets is None:
        print(
            "check-crickets-plugin-refs: no crickets checkout reachable "
            "($CRICKETS_REPO_ROOT or the sibling layout) — skipping. This gate "
            "needs crickets' real plugin set as ground truth."
        )
        return 0
    findings = scan(crickets)
    if findings:
        print(
            "check-crickets-plugin-refs: FAIL — reference(s) to a crickets "
            "plugin that cannot resolve:",
            file=sys.stderr,
        )
        for f in findings:
            print(f"  {f}", file=sys.stderr)
        print(
            "\n  A renamed plugin takes its src/ directory and its host "
            "namespace with it. Point these at the current name.",
            file=sys.stderr,
        )
        return 1
    print(
        f"check-crickets-plugin-refs: clean (against {len(crickets_plugin_names(crickets))} "
        f"crickets plugins)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
