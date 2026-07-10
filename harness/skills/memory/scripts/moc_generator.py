#!/usr/bin/env python3
# moc_generator.py — V6-18 browse-first Maps of Content.
#
# Reads the vault (read-only, same walk roots vec_index.py's full_sync
# uses — personal/, projects/, _idea-incubator/), groups notes by their
# kind: frontmatter value (via kind_registry's known/unrecognized labeling),
# and writes one MOC page per kind under <vault>/_moc/<kind>.md — a bullet
# list of [[slug]] wikilinks, newest-first by `created`. Idempotent: a
# regenerate overwrites only the _moc/*.md pages this module itself owns;
# it never touches any source note.

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from kind_registry import is_kebab, is_known  # noqa: E402

# Mirrors vec_index.py's full_sync walk roots exactly (agentm-memory-index.md's
# build-from-source path) — deliberately includes _idea-incubator/, unlike
# frontmatter_validator.py's DC-4-exempt walk. Browse-first MOCs should cover
# every kind the memory engine actually indexes, incubator included.
_WALK_SUBDIRS = ("personal", "projects", "_idea-incubator")

_OUTPUT_DIRNAME = "_moc"


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    """Minimal key: raw-value frontmatter extraction. Mirrors the same
    stdlib-only contract kind_registry.py and frontmatter_validator.py each
    keep their own copy of (a deliberate, standalone-module convention in
    this scripts/ dir — see graph.py's own _frontmatter_text precedent)."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    fm: dict[str, str] = {}
    for line in text[4:end].split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key:
            fm[key] = value.strip()
    return fm


def _walk_notes(vault: Path):
    """Yield (rel_path, frontmatter dict) for every walkable note. Never
    yields anything under the output dir this module owns, so a
    regeneration never tries to fold its own prior output back in."""
    for subdir in _WALK_SUBDIRS:
        root = vault / subdir
        if not root.is_dir():
            continue
        for md in sorted(root.rglob("*.md")):
            if any(p == "_archive" or p == _OUTPUT_DIRNAME for p in md.parts):
                continue
            if md.name.startswith("PLAN.archive."):
                continue
            try:
                text = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm = _parse_frontmatter(text)
            if fm is None or "kind" not in fm:
                continue
            rel = md.relative_to(vault)
            yield rel, fm


def _wikilink_target(rel_path: Path, fm: dict[str, str]) -> str:
    """The bare slug a wikilink resolves against, matching the real vault's
    own MOC convention (personal/preferences/_index.md): `[[slug]]`, not a
    full relative path. Falls back to the file's stem when `slug:` is
    absent (shouldn't happen on a conformant entry, but never crash on one
    that isn't)."""
    return fm.get("slug") or rel_path.stem


def build_kind_groups(vault_path: Path | str) -> dict[str, list[tuple[str, str, dict]]]:
    """Read-only scan. Returns {kind: [(rel_path_str, created, fm), ...]}
    sorted newest-first by `created` within each kind group. `kind` here is
    the raw frontmatter value, including unrecognized/malformed ones — this
    function does not filter, only groups; `generate()` decides what to
    render.
    """
    vault = Path(vault_path)
    groups: dict[str, list[tuple[str, str, dict]]] = {}
    if not vault.is_dir():
        return groups
    for rel_path, fm in _walk_notes(vault):
        kind = fm["kind"]
        groups.setdefault(kind, []).append((str(rel_path).replace("\\", "/"), fm.get("created", ""), fm))
    for kind in groups:
        groups[kind].sort(key=lambda entry: entry[1], reverse=True)
    return groups


def _render_moc(kind: str, entries: list[tuple[str, str, dict]]) -> str:
    label = kind if is_known(kind) else f"{kind} (unrecognized kind)"
    lines = [f"# MOC — {label}", "", f"{len(entries)} entries, newest-first by `created`.", ""]
    for rel_path, _created, fm in entries:
        lines.append(f"- [[{_wikilink_target(Path(rel_path), fm)}]]")
    return "\n".join(lines) + "\n"


def generate(vault_path: Path | str) -> list[str]:
    """Write one MOC page per kind under <vault>/_moc/<kind>.md. Returns the
    list of kind values a page was written for. Malformed (non-kebab) kind
    values are skipped entirely — a MOC filename must itself be a legal
    kebab-case name, and a malformed kind has no other legitimate slot to
    file under (kind_registry.py's audit(), not this module, is where a
    malformed value gets flagged for a human to fix).

    Idempotent + narrowly scoped to overwrite: only files this call is about
    to (re)write are touched; nothing else under _moc/ or the source tree is
    read, deleted, or modified.
    """
    vault = Path(vault_path)
    groups = build_kind_groups(vault)
    output_dir = vault / _OUTPUT_DIRNAME
    written: list[str] = []
    for kind, entries in sorted(groups.items()):
        if not is_kebab(kind):
            continue
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{kind}.md").write_text(_render_moc(kind, entries), encoding="utf-8")
        written.append(kind)
    return written


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V6-18 browse-first MOC generator")
    parser.add_argument("--vault", required=True, help="path to the vault root")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    written = generate(args.vault)
    print(f"wrote {len(written)} MOC page(s) under {Path(args.vault) / _OUTPUT_DIRNAME}")
    for kind in written:
        print(f"  {kind}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
