#!/usr/bin/env python3
"""sweep_junk_preferences — archive the R0.3 junk-slug preference cohort.

Pre-fix, `reflect.py`'s bare always/never HIGH pattern auto-saved any
mid-sentence "always"/"never" occurrence in any user-role transcript message
(including machine-generated subagent/workflow transcripts) as if it were an
operator preference statement. That polluted `<group>/preferences/` with
"always-*"/"never-*" junk slugs whose body is the mined excerpt, not an
actual preference.

Now that the source (reflect.py's `_discover_transcripts` filter + the
always/never confidence demotion) is fixed, this is a one-shot cleanup for
the cohort that already landed: move each identified junk entry to
`<group>/_archive/preferences/<filename>` (the same archive convention
evolve.py uses for superseded entries) so it's out of active recall / heat
scans but still available for manual review, never destructively deleted.

A file is identified as junk iff ALL of:
  - its parent directory is named `preferences`
  - its filename starts with `always-` or `never-`
  - its frontmatter `kind` is `preferences`
  - its body starts with `User stated:` (the reflect.py auto-save template —
    distinguishes mined junk from a genuine preference the operator or a
    prior /memory save wrote by hand under a coincidentally-similar slug)

Usage:
    python3 sweep_junk_preferences.py --vault-path <path> [--apply]

Dry-run (the default) lists what would move + exits 0 without touching
anything. --apply performs the moves. Collision-safe: if the archive
destination already exists, appends the same `-N` suffix evolve.py uses.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from recall import _parse_frontmatter  # noqa: E402


def is_junk_preference(path: Path) -> bool:
    """True iff `path` matches the R0.3 junk-slug signature (see module docstring)."""
    if "_archive" in path.parts:
        return False  # already archived — never re-flag (avoids nested _archive/_archive/)
    if path.parent.name != "preferences":
        return False
    name = path.name
    if not (name.startswith("always-") or name.startswith("never-")):
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    fm, body = _parse_frontmatter(content)
    if fm.get("kind") != "preferences":
        return False
    return body.lstrip().startswith("User stated:")


def find_junk_preferences(vault: Path) -> list[Path]:
    """Every junk preference file under `vault`, sorted for deterministic output."""
    found = [p for p in vault.rglob("*.md") if p.is_file() and is_junk_preference(p)]
    return sorted(found)


def _archive_destination(vault: Path, junk_path: Path) -> Path:
    """<group>/_archive/preferences/<filename>, preserving the source group.

    `junk_path` is `<vault>/<group>/preferences/<filename>.md` (group is
    `personal` or `personal-private`). Collision-safe: appends `-2`, `-3`, ...
    before the suffix if the destination is already occupied (mirrors
    evolve.py's `_compute_archive_path` collision handling).
    """
    group = junk_path.parent.parent.name
    dest_dir = vault / group / "_archive" / "preferences"
    candidate = dest_dir / junk_path.name
    if not candidate.exists():
        return candidate
    stem, suffix = junk_path.stem, junk_path.suffix
    n = 2
    while True:
        candidate = dest_dir / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1
        if n > 99:  # pragma: no cover
            raise FileExistsError(f"Archive collision: {dest_dir / junk_path.name} through -99 all taken")


def sweep(vault: Path, *, apply: bool, stdout=sys.stdout) -> int:
    """List (or, with apply=True, perform) the junk-preference archive sweep.

    Returns the number of files identified (moved, if apply=True).
    """
    junk = find_junk_preferences(vault)
    if not junk:
        print("sweep_junk_preferences: no junk preference files found.", file=stdout)
        return 0
    for path in junk:
        dest = _archive_destination(vault, path)
        rel_src = path.relative_to(vault)
        rel_dest = dest.relative_to(vault)
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, dest)
            print(f"archived  {rel_src} -> {rel_dest}", file=stdout)
        else:
            print(f"would archive  {rel_src} -> {rel_dest}", file=stdout)
    verb = "archived" if apply else "would archive"
    print(f"sweep_junk_preferences: {verb} {len(junk)} junk preference file(s).", file=stdout)
    return len(junk)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sweep_junk_preferences",
        description="Archive the R0.3 junk-slug preference cohort (dry-run by default).",
    )
    parser.add_argument("--vault-path", required=False,
                         help="path to MemoryVault root (overrides MEMORY_VAULT_PATH env var)")
    parser.add_argument("--apply", action="store_true",
                         help="perform the moves (default: dry-run, list only)")
    return parser.parse_args(argv)


def _resolve_vault_path(arg_vault_path: str | None) -> Path:
    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    raise FileNotFoundError(
        "No vault path resolved. Set --vault-path or the MEMORY_VAULT_PATH env var."
    )


def main(argv: list[str]) -> int:
    ns = _parse_args(argv)
    vault = _resolve_vault_path(ns.vault_path)
    if not vault.is_dir():
        print(f"sweep_junk_preferences: vault path does not exist: {vault}", file=sys.stderr)
        return 2
    sweep(vault, apply=ns.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
