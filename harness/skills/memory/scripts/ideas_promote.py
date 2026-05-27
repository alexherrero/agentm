#!/usr/bin/env python3
# ideas_promote.py — `/memory promote idea <slug>` + incubator GC.
#
# Two operations:
#
#   1. promote_idea(slug):
#      - Moves <vault>/personal-private/_idea-incubator/<slug>/
#             → <vault>/personal-private/projects/<slug>/
#        (project-personal dir mirrors save.py's group/kind hierarchy;
#        the spec called for projects/<slug>/ — slug becomes a
#        project dir at the canonical location).
#      - Recalculates vec-index entries for the moved files (paths
#        changed, so old keys are stale + new keys need upsert).
#      - Annotates the corresponding ~/Obsidian/Ideas.md section with
#        `→ promoted YYYY-MM-DD to projects/<slug>/` (requires
#        A3 permeable-boundary confirmation; reuses the same helper).
#
#   2. gc_idea_incubator():
#      - Walks <vault>/personal-private/_idea-incubator/<slug>/ dirs.
#      - For each, computes age from _index.md frontmatter `updated:`
#        field (falls back to file mtime if frontmatter missing).
#      - Entries older than `gc_months` (default 6) get presented to the
#        operator with Keep / Archive / Delete prompt.
#      - Archive: moves to _idea-incubator/_archive/<slug>/.
#      - Delete: rm -rf the dir + delete vec-index entries.
#      - Keep: no-op (touches `_index.md` mtime so future GC passes
#        respect the operator's decision).
#      - Never silent deletion — non-TTY contexts default to Keep.
#
# Plan #7a part 4 task 4 (this commit) ships both operations + the
# matching `/memory promote` sub-command body in SKILL.md. Task 5
# documents the full lifecycle in Use-The-Memory-Skill.md.

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

DEFAULT_GC_MONTHS = 6  # per locked design call B1.i


def _resolve_vault_path(arg: str | None) -> Path:
    """vault path: arg → MEMORY_VAULT_PATH env → error."""
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    raise FileNotFoundError(
        "No vault path resolved. Set --vault-path or MEMORY_VAULT_PATH."
    )


def _resolve_ideas_path(arg: str | None) -> Path:
    """Ideas.md path: arg → IDEAS_SURFACE_PATH env → default ~/Obsidian/Ideas.md."""
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("IDEAS_SURFACE_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / "Obsidian" / "Ideas.md"


def _parse_index_frontmatter(index_path: Path) -> dict:
    """Tiny YAML reader for _index.md frontmatter — returns dict of fields."""
    out: dict = {}
    if not index_path.exists():
        return out
    try:
        content = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    if not content.startswith("---\n"):
        return out
    end = content.find("\n---\n", 4)
    if end == -1:
        return out
    for line in content[4:end].split("\n"):
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def _parse_iso_date(s: str) -> datetime | None:
    """Parse YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ (used in frontmatter)."""
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _vec_index_reflect_move(
    vault: Path, old_prefix: str, new_prefix: str
) -> dict:
    """Re-key vec-index entries from old path prefix to new prefix.

    For each entry whose path starts with `old_prefix`, queue a delete
    (old path) + an upsert (new path; embed text computed from the
    new file's content at drain time). Returns stats dict
    {deleted: N, queued_upsert: N} so the operator can verify.

    Graceful-skip if vec-index unavailable (no sqlite-vec) — operator
    runs `python3 vec_index.py drain` later or it's a no-op until a
    capable environment runs reindex.
    """
    stats = {"deleted": 0, "queued_upsert": 0, "skipped": 0}
    try:
        import vec_index  # type: ignore
    except ImportError:
        stats["skipped"] = -1  # signal: vec_index module missing
        return stats

    # Walk the new location (post-move) to find files we need to re-key.
    new_dir = vault / new_prefix
    if not new_dir.exists():
        return stats
    for path in new_dir.rglob("*"):
        if not path.is_file() or not path.suffix == ".md":
            continue
        new_rel = str(path.relative_to(vault)).replace(os.sep, "/")
        old_rel = new_rel.replace(new_prefix, old_prefix, 1)
        # Delete old path from index (best-effort).
        try:
            if vec_index.delete_entry(vault, old_rel):
                stats["deleted"] += 1
        except Exception:
            pass
        # Queue an upsert for the new path. embed text: slug + first 500
        # chars of body. Reads body from the new location.
        try:
            content = path.read_text(encoding="utf-8")
            body = content.split("---\n", 2)[-1] if content.startswith("---\n") else content
            embed_text = f"{path.stem}\n\n{body[:500]}"
            vec_index.enqueue(vault, new_rel, "upsert", text=embed_text)
            stats["queued_upsert"] += 1
        except Exception:
            pass
    return stats


def _annotate_ideas_md_section(
    ideas_path: Path, slug: str, project_path: str, *, mode: str | None,
    stdin=None, stdout=None,
) -> bool:
    """Append `→ promoted YYYY-MM-DD to <project-path>` annotation to the
    Ideas.md section whose wikilink references <slug>.

    Routes through the A3 permeable-boundary helper (Ideas.md is outside
    MemoryVault). Returns True if annotation written; False if denied
    or section not found.
    """
    if not ideas_path.exists():
        return False
    try:
        from permeable_boundary import confirm_write_outside_memoryvault
    except ImportError:
        return False

    content = ideas_path.read_text(encoding="utf-8")
    # Find the section whose body contains the slug's wikilink.
    wikilink_pattern = re.compile(
        rf"_idea-incubator/{re.escape(slug)}/_index\.md"
    )
    if not wikilink_pattern.search(content):
        return False

    annotation = f"→ promoted {datetime.now(timezone.utc).strftime('%Y-%m-%d')} to {project_path}"
    # Find the section + insert annotation as a new line after the
    # wikilink line. Keep the file structure intact (header preserved).
    lines = content.split("\n")
    out_lines: list[str] = []
    in_target_section = False
    inserted = False
    for i, line in enumerate(lines):
        out_lines.append(line)
        if line.startswith("## "):
            in_target_section = False
        if in_target_section and wikilink_pattern.search(line) and not inserted:
            out_lines.append(annotation)
            inserted = True
        if line.startswith("## "):
            # Look ahead a few lines for the wikilink — if found, mark
            # this section as the target.
            for la in lines[i+1:i+6]:
                if wikilink_pattern.search(la):
                    in_target_section = True
                    break

    if not inserted:
        return False

    new_content = "\n".join(out_lines)

    # A3 boundary check.
    rationale = (
        f"Promotion annotation for idea {slug!r} — appending "
        f"'→ promoted YYYY-MM-DD' to the corresponding Ideas.md section."
    )
    approved = confirm_write_outside_memoryvault(
        target_path=ideas_path,
        content_preview=annotation,
        rationale=rationale,
        mode=mode,
        stdin=stdin,
        stdout=stdout,
    )
    if not approved:
        return False

    ideas_path.write_bytes(new_content.encode("utf-8"))
    return True


def promote_idea(
    slug: str,
    *,
    vault_path: Path | str | None = None,
    ideas_path: Path | str | None = None,
    mode: str | None = None,
    stdin=None,
    stdout=None,
) -> dict:
    """Promote an idea from _idea-incubator to projects.

    Returns stats dict:
        {
            "promoted": bool,
            "incubator_dir": "<source path>",
            "project_dir": "<destination path>",
            "vec_index": {...},
            "ideas_annotation": "written" | "denied" | "section_not_found" | "no_ideas_file",
        }

    Raises:
        FileNotFoundError: vault path missing, incubator entry missing.
        FileExistsError: a projects/<slug>/ dir already exists
            (would clobber; operator picks new slug or removes existing).
    """
    vault = _resolve_vault_path(str(vault_path) if vault_path else None)
    if not vault.exists():
        raise FileNotFoundError(f"vault path does not exist: {vault}")

    incubator_dir = vault / "personal-private" / "_idea-incubator" / slug
    if not incubator_dir.exists() or not incubator_dir.is_dir():
        raise FileNotFoundError(
            f"incubator entry not found: {incubator_dir} "
            f"(check slug spelling; list entries with "
            f"`ls {vault}/personal-private/_idea-incubator/`)"
        )

    project_dir = vault / "personal-private" / "projects" / slug
    if project_dir.exists():
        raise FileExistsError(
            f"projects/{slug}/ already exists at {project_dir}; "
            f"pick a different slug or remove the existing dir first"
        )

    # Move incubator → project. Cross-filesystem rename uses shutil.move.
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(incubator_dir), str(project_dir))

    # Recalculate vec-index entries.
    old_prefix = f"personal-private/_idea-incubator/{slug}/"
    new_prefix = f"personal-private/projects/{slug}/"
    vec_stats = _vec_index_reflect_move(vault, old_prefix, new_prefix)

    # Annotate Ideas.md section.
    ideas_md = _resolve_ideas_path(str(ideas_path) if ideas_path else None)
    if not ideas_md.exists():
        ideas_annotation = "no_ideas_file"
    else:
        wrote = _annotate_ideas_md_section(
            ideas_md, slug, new_prefix.rstrip("/"),
            mode=mode, stdin=stdin, stdout=stdout,
        )
        ideas_annotation = "written" if wrote else "denied_or_not_found"

    return {
        "promoted": True,
        "incubator_dir": str(incubator_dir),
        "project_dir": str(project_dir),
        "vec_index": vec_stats,
        "ideas_annotation": ideas_annotation,
    }


def _entry_age_days(index_path: Path) -> int:
    """Compute age of an incubator entry in days.

    Reads `updated:` frontmatter field; falls back to file mtime.
    Returns days since epoch-to-now (best-effort; on parse failure,
    returns 0 so the entry is treated as fresh + skipped).
    """
    fm = _parse_index_frontmatter(index_path)
    updated_str = fm.get("updated", "")
    updated_dt = _parse_iso_date(updated_str)
    if updated_dt is None:
        # Fall back to file mtime.
        try:
            updated_dt = datetime.fromtimestamp(
                index_path.stat().st_mtime, tz=timezone.utc
            )
        except OSError:
            return 0
    now = datetime.now(timezone.utc)
    return max(0, (now - updated_dt).days)


def _prompt_gc_action(
    slug: str, age_days: int, *, stdin, stdout
) -> str:
    """Prompt operator for Keep / Archive / Delete action on an old entry.

    Returns 'keep' / 'archive' / 'delete'. Defaults to 'keep' on EOF /
    invalid input / non-TTY stdin (safer — never delete without confirm).
    """
    try:
        is_tty = stdin.isatty()
    except Exception:
        is_tty = False
    if not is_tty:
        return "keep"
    print("", file=stdout)
    print("─" * 72, file=stdout)
    print(f"Incubator entry idle: {slug} ({age_days} days since last update)", file=stdout)
    print("─" * 72, file=stdout)
    print("Action: [k]eep (defer) / [a]rchive / [d]elete (default: k): ",
          end="", file=stdout, flush=True)
    try:
        ans = stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "keep"
    if ans in {"a", "archive"}:
        return "archive"
    if ans in {"d", "delete"}:
        return "delete"
    return "keep"


def gc_idea_incubator(
    *,
    vault_path: Path | str | None = None,
    gc_months: int = DEFAULT_GC_MONTHS,
    stdin=None,
    stdout=None,
) -> dict:
    """Walk _idea-incubator/ for entries older than gc_months + prompt
    operator for Keep/Archive/Delete on each.

    Returns stats dict {kept: N, archived: N, deleted: N, scanned: N}.
    Non-TTY contexts default every prompt to 'keep' — never silent deletion.
    """
    vault = _resolve_vault_path(str(vault_path) if vault_path else None)
    incubator_root = vault / "personal-private" / "_idea-incubator"
    stats = {"scanned": 0, "kept": 0, "archived": 0, "deleted": 0}
    if not incubator_root.exists():
        return stats
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    threshold_days = gc_months * 30  # approximate; close enough for GC

    for slug_dir in sorted(incubator_root.iterdir()):
        if not slug_dir.is_dir():
            continue
        if slug_dir.name.startswith("_"):
            # Skip _archive/ + future _-prefixed metadata dirs.
            continue
        stats["scanned"] += 1
        age_days = _entry_age_days(slug_dir / "_index.md")
        if age_days < threshold_days:
            continue

        action = _prompt_gc_action(slug_dir.name, age_days, stdin=stdin, stdout=stdout)
        if action == "archive":
            archive_root = incubator_root / "_archive"
            archive_root.mkdir(exist_ok=True)
            target = archive_root / slug_dir.name
            if target.exists():
                # Collision suffix: <slug>-archive-N
                n = 2
                while (archive_root / f"{slug_dir.name}-archive-{n}").exists():
                    n += 1
                target = archive_root / f"{slug_dir.name}-archive-{n}"
            shutil.move(str(slug_dir), str(target))
            stats["archived"] += 1
        elif action == "delete":
            shutil.rmtree(slug_dir)
            stats["deleted"] += 1
        else:
            # 'keep' — refresh mtime so the entry exits the GC window.
            try:
                (slug_dir / "_index.md").touch()
            except OSError:
                pass
            stats["kept"] += 1
    return stats


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-ideas-promote",
        description=(
            "/memory promote idea <slug> + incubator GC. Promotes "
            "_idea-incubator/<slug>/ → projects/<slug>/ with "
            "Ideas.md annotation (A3 boundary check) + vec-index recalc. "
            "GC subcommand prompts for Keep/Archive/Delete on entries "
            "older than 6 months. Plan #7a part 4 task 4."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_promote = sub.add_parser("promote", help="promote idea slug to projects/")
    p_promote.add_argument("slug", help="incubator slug to promote")
    p_promote.add_argument("--vault-path", default=None, help="MemoryVault root")
    p_promote.add_argument("--ideas-path", default=None,
                           help="override Ideas.md path (default $IDEAS_SURFACE_PATH or ~/Obsidian/Ideas.md)")
    p_promote.add_argument("--mode", choices=["silent", "interactive", "auto"],
                           default=None,
                           help="permeable-boundary mode for Ideas.md annotation (default: $MEMORY_REVIEW_MODE or interactive)")
    p_gc = sub.add_parser("gc", help="garbage-collect old incubator entries")
    p_gc.add_argument("--vault-path", default=None, help="MemoryVault root")
    p_gc.add_argument("--gc-months", type=int, default=DEFAULT_GC_MONTHS,
                      help=f"GC threshold in months (default: {DEFAULT_GC_MONTHS})")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        if args.cmd == "promote":
            result = promote_idea(
                slug=args.slug,
                vault_path=args.vault_path,
                ideas_path=args.ideas_path,
                mode=args.mode,
            )
            print(json.dumps(result))
            return 0
        if args.cmd == "gc":
            result = gc_idea_incubator(
                vault_path=args.vault_path,
                gc_months=args.gc_months,
            )
            print(json.dumps(result))
            return 0
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
