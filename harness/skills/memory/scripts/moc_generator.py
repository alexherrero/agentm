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


_HOME_BACKLINK_URL = "https://github.com/alexherrero/agentm/wiki/Home"


def _render_moc(kind: str, entries: list[tuple[str, str, dict]]) -> str:
    label = kind if is_known(kind) else f"{kind} (unrecognized kind)"
    lines = [
        f"# MOC — {label}",
        "",
        # Two distinct backlinks, not a duplicate: [[Home]] is the vault's
        # own navigational root (Obsidian wikilink, resolves in-vault by
        # filename regardless of path) -- added 2026-07-11 (Consolidation
        # arc exit-gate follow-up) after E5's vault-connectivity review
        # found Home.md and _moc/ never cross-referenced each other at all,
        # in either direction. The wiki-Home link (CONS-1, 2026-07-10) is a
        # separate, still-valid pointer to the *project's* docs entry point
        # on GitHub -- a different destination for a different orientation
        # need, not something this replaces.
        "[[Home]]",
        f"[← wiki Home]({_HOME_BACKLINK_URL})",
        "",
        f"{len(entries)} entries, newest-first by `created`.",
        "",
    ]
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


# -----------------------------------------------------------------------------
# Arc-index pages (2026-07-18 arc-as-metadata convention) — one real `kind:
# arc-index` entry per (project, arc), at `projects/<project>/arcs/<arc>.md`.
# Unlike the fully-generated `_moc/<kind>.md` pages above, an arc-index is a
# real memory entry a human may add a header to, so regeneration only owns
# everything from `_ARC_MARKER` down — content above it survives untouched.
# -----------------------------------------------------------------------------

_ARC_MARKER = "<!-- BEGIN GENERATED ARC LINKS (moc_generator.py — do not edit below) -->"


def build_arc_groups(vault_path: Path | str) -> dict[tuple[str, str], list[tuple[str, str, dict]]]:
    """Read-only scan. Returns {(project, arc): [(rel_path_str, created, fm), …]}
    sorted newest-first by `created`, for every entry under `projects/<project>/`
    carrying a (kebab-case) `arc:` frontmatter field. Arc only ever appears on a
    project-scoped entry (decisions/designs), never on personal/ or
    _idea-incubator/ content, so this walks `projects/` alone."""
    vault = Path(vault_path)
    groups: dict[tuple[str, str], list[tuple[str, str, dict]]] = {}
    root = vault / "projects"
    if not root.is_dir():
        return groups
    for md in sorted(root.rglob("*.md")):
        if any(p == "_archive" or p == _OUTPUT_DIRNAME or p == "_harness" for p in md.parts):
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = _parse_frontmatter(text)
        if fm is None or "arc" not in fm:
            continue
        arc = fm["arc"].strip()
        if not arc:
            continue
        rel = md.relative_to(root)
        project = rel.parts[0]
        groups.setdefault((project, arc), []).append(
            (str(rel).replace("\\", "/"), fm.get("created", ""), fm)
        )
    for key in groups:
        groups[key].sort(key=lambda entry: entry[1], reverse=True)
    return groups


def _render_arc_links(project: str, arc: str, entries: list[tuple[str, str, dict]],
                       other_projects: list[str]) -> str:
    lines = [_ARC_MARKER, ""]
    if other_projects:
        pointers = ", ".join(f"`{p}`" for p in sorted(other_projects))
        lines.append(f"Also stamped `arc: {arc}` in: {pointers}.")
        lines.append("")
    lines.append(f"{len(entries)} entries in `{project}`, newest-first by `created`.")
    lines.append("")
    for rel_path, _created, fm in entries:
        lines.append(f"- [[{_wikilink_target(Path(rel_path), fm)}]]")
    return "\n".join(lines) + "\n"


def _new_arc_index_frontmatter(project: str, arc: str, today: str) -> str:
    return (
        "---\n"
        "kind: arc-index\n"
        "status: active\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        "tags: []\n"
        f"arc: {arc}\n"
        f"group: projects/{project}/arcs\n"
        f"slug: {arc}\n"
        "---\n\n"
        f"# {arc} — arc index\n\n"
    )


def generate_arc_indexes(vault_path: Path | str, *, today: str) -> list[str]:
    """Write/update `projects/<project>/arcs/<arc>.md` for every (project, arc)
    pair with at least one `arc:`-stamped entry. A new file gets a locked
    `kind: arc-index` frontmatter block + a bare `# <arc> — arc index` header;
    an existing file keeps everything above `_ARC_MARKER` untouched (a human's
    hand-seeded header survives) and only the generated link-list below it is
    replaced. Returns the list of `project/arc` keys written.

    Cross-repo arcs (the same arc stamped in more than one project) get a full
    link list in EACH project that has entries — the canonical-vs-pointer
    distinction the design names is an editorial call layered on by hand; this
    generator's mechanical contribution is the per-project list plus an "also
    stamped in" cross-reference line so the sibling is discoverable.
    """
    vault = Path(vault_path)
    groups = build_arc_groups(vault)
    arcs_to_projects: dict[str, set[str]] = {}
    for (project, arc) in groups:
        arcs_to_projects.setdefault(arc, set()).add(project)

    written: list[str] = []
    for (project, arc), entries in sorted(groups.items()):
        other = sorted(arcs_to_projects[arc] - {project})
        target = vault / "projects" / project / "arcs" / f"{arc}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        generated = _render_arc_links(project, arc, entries, other)
        if target.is_file():
            existing = target.read_text(encoding="utf-8")
            idx = existing.find(_ARC_MARKER)
            header = existing[:idx] if idx != -1 else existing.rstrip("\n") + "\n\n"
            new_text = header + generated
        else:
            new_text = _new_arc_index_frontmatter(project, arc, today) + generated
        target.write_text(new_text, encoding="utf-8")
        written.append(f"{project}/{arc}")
    return written


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V6-18 browse-first MOC generator")
    parser.add_argument("--vault", required=True, help="path to the vault root")
    parser.add_argument("--arcs", action="store_true",
                         help="also (re)generate projects/<project>/arcs/<arc>.md arc-index pages")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    from datetime import date
    args = _parse_args(argv)
    written = generate(args.vault)
    print(f"wrote {len(written)} MOC page(s) under {Path(args.vault) / _OUTPUT_DIRNAME}")
    for kind in written:
        print(f"  {kind}.md")
    if args.arcs:
        arc_written = generate_arc_indexes(args.vault, today=date.today().isoformat())
        print(f"wrote/updated {len(arc_written)} arc-index page(s)")
        for key in arc_written:
            print(f"  projects/{key.split('/')[0]}/arcs/{key.split('/')[1]}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
