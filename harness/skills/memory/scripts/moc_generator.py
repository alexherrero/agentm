#!/usr/bin/env python3
# moc_generator.py — two generated indexes outside the maps' class directory:
# `standards/moc-standards.md`, the always-load tier's map, and the arc-index
# pages under `Projects/<project>/arcs/`. Neither touches a source note.
#
# The per-kind pages it once wrote under `<vault>/_moc/` retired at the
# 2026-08-11 rehoming pass, and the function that wrote them, with its
# `[[Home]]` backlink, went in agentm-vault plan 07: the dreaming binary's
# mocs job writes the maps, under `memory/mocs/`.

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# Filing-v2 2b: the newest project-space generation is the vault-root
# `Projects/`, a SIBLING of the memory root this module is handed. During the
# merge window both it and `desk/projects/` exist and either may hold projects,
# so walkers take the union. A root-space path cannot be keyed relative to the
# memory root; it is keyed relative to the vault root ("Projects/<slug>/…").
_ROOT_PROJECTS_DIRNAME = "Projects"


def _root_projects_dir(vault):
    """The vault-root `Projects/` space, discovered never conjured (filing-v2
    2b). Flat layout: `<memory-root>/Projects`. Nested layout — the memory
    root sits inside an Obsidian vault, witnessed by `.obsidian/` at the
    parent and none at the memory root itself: the sibling
    `<vault-root>/Projects`. A memory root at the top of its own vault has no
    sibling, whatever directory named `Projects` sits beside it (its parent
    is the operator's home or a sync folder, where one is common and is not
    the vault's). None when no root space exists. Both rungs match the
    directory's exact case."""
    vault = Path(vault)
    flat = vault / "Projects"
    if _is_dir_exact(flat):
        return flat
    parent = vault.parent
    if (parent / ".obsidian").is_dir() and not (vault / ".obsidian").is_dir():
        sibling = parent / "Projects"
        if _is_dir_exact(sibling):
            return sibling
    return None


def _is_dir_exact(path):
    """`path` is a directory whose name matches exactly — on a case-insensitive
    filesystem `Projects/` would otherwise answer for the V4-era `projects/`."""
    try:
        return path.is_dir() and any(p.name == path.name for p in path.parent.iterdir())
    except OSError:
        return False


def _project_home(vault: Path, project: str) -> Path:
    """The project's tree on whichever generation holds it: the vault-root
    sibling first, else desk/projects (also the create target on a flat vault)."""
    space = _root_projects_dir(vault)
    if space is not None and (space / project).is_dir():
        return space / project
    return vault / "desk/projects" / project


def _project_group(vault: Path, project: str) -> str:
    """The group value matching _project_home: `projects` for the root space
    (save.py maps it onto Projects/), `desk/projects` otherwise."""
    space = _root_projects_dir(vault)
    return "projects" if space is not None and (space / project).is_dir() else "desk/projects"


def _project_space_notes(vault: Path):
    """Yield (space_root, note) across every project space this vault has."""
    for root in (vault / "desk/projects", _root_projects_dir(vault)):
        if root is None or not root.is_dir():
            continue
        for md in sorted(root.rglob("*.md")):
            yield root, md

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


def _wikilink_target(rel_path: Path, fm: dict[str, str]) -> str:
    """The bare slug a wikilink resolves against, matching the real vault's
    own MOC convention (personal/preferences/_index.md): `[[slug]]`, not a
    full relative path. Falls back to the file's stem when `slug:` is
    absent (shouldn't happen on a conformant entry, but never crash on one
    that isn't)."""
    return fm.get("slug") or rel_path.stem


# -----------------------------------------------------------------------------
# The standards map (agentm-vault plan 05) — `standards/moc-standards.md`, a
# generated map of the always-load tier: the rule files at the top of
# standards/ and the voice library under standards/voice/. The loader skips
# every `moc-*` file (generated navigation carries no standing instruction),
# so the map is for you and the chat surfaces, never for the injected tier.
# It is the one file besides the two drafted documents this plan writes into
# standards/, and it is fully regenerated: nothing hand-written survives here.
# -----------------------------------------------------------------------------

_STANDARDS_MOC_NAME = "moc-standards.md"


def _standards_dir(vault: Path) -> Path:
    import vault_layout  # noqa: E402 — same-dir convention
    return vault_layout.standards_dir(vault)


def render_standards_moc(standards: Path) -> str:
    """The map's text for a standards directory: the rule files, then the
    voice library, each as a wikilink by stem with its `title:` or first
    heading when the file carries one."""
    rules = sorted(p for p in standards.glob("*.md") if not p.stem.startswith("moc-"))
    voice_dir = standards / "voice"
    voice = sorted(voice_dir.glob("*.md")) if voice_dir.is_dir() else []
    lines = [
        "---",
        "kind: moc",
        "status: active",
        "generated_by: moc_generator.py",
        "---",
        "",
        "# MOC — standards",
        "",
        "The always-load tier. Every file at the top of `standards/` loads into",
        "every session; the voice library under `standards/voice/` is read on",
        "demand, by genre. Generated — edit the files, not this map.",
        "",
        f"## Rules ({len(rules)})",
        "",
    ]
    for p in rules:
        lines.append(f"- [[{p.stem}]] — {_title_of(p)}")
    lines += ["", f"## Voice library ({len(voice)})", ""]
    for p in voice:
        lines.append(f"- [[{p.stem}]] — {_title_of(p)}")
    return "\n".join(lines) + "\n"


def _title_of(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return path.stem
    fm = _parse_frontmatter(text) or {}
    for key in ("title", "trigger", "description"):
        v = fm.get(key)
        if v:
            return str(v).strip().strip("'\"")
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def generate_standards_moc(vault_path: Path | str) -> Path:
    """Write `standards/moc-standards.md` and return its path."""
    standards = _standards_dir(Path(vault_path))
    standards.mkdir(parents=True, exist_ok=True)
    out = standards / _STANDARDS_MOC_NAME
    out.write_text(render_standards_moc(standards), encoding="utf-8")
    return out


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
    for root, md in _project_space_notes(vault):
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


def _new_arc_index_frontmatter(vault: Path, project: str, arc: str, today: str) -> str:
    return (
        "---\n"
        "kind: arc-index\n"
        "status: active\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        "tags: []\n"
        f"arc: {arc}\n"
        f"group: {_project_group(vault, project)}/{project}/arcs\n"
        f"slug: {arc}\n"
        "always_load: false\n"
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
        target = _project_home(vault, project) / "arcs" / f"{arc}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        generated = _render_arc_links(project, arc, entries, other)
        if target.is_file():
            existing = target.read_text(encoding="utf-8")
            idx = existing.find(_ARC_MARKER)
            header = existing[:idx] if idx != -1 else existing.rstrip("\n") + "\n\n"
            new_text = header + generated
        else:
            new_text = _new_arc_index_frontmatter(vault, project, arc, today) + generated
        target.write_text(new_text, encoding="utf-8")
        written.append(f"{project}/{arc}")
    return written


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="the standards map and the arc-index pages")
    parser.add_argument("--vault", required=True, help="path to the vault root")
    parser.add_argument("--arcs", action="store_true",
                         help="(re)generate projects/<project>/arcs/<arc>.md arc-index pages")
    parser.add_argument("--standards", action="store_true",
                         help="(re)generate standards/moc-standards.md, the always-load tier's map")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    from datetime import date
    args = _parse_args(argv)
    if not (args.standards or args.arcs):
        print("nothing to generate: pass --standards, --arcs or both", file=sys.stderr)
        return 2
    if args.standards:
        print(f"wrote {generate_standards_moc(args.vault)}")
    if args.arcs:
        arc_written = generate_arc_indexes(args.vault, today=date.today().isoformat())
        print(f"wrote/updated {len(arc_written)} arc-index page(s)")
        for key in arc_written:
            print(f"  projects/{key.split('/')[0]}/arcs/{key.split('/')[1]}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
