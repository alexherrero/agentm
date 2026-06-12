#!/usr/bin/env python3
# evolve.py — canonical /memory evolve primitive.
#
# Archives an existing entry + writes a new entry in its place (in-place
# or with --new-slug rename). Both files cross-link via supersedes /
# superseded_by frontmatter so the supersession graph is queryable.
# Recall filters skip status: superseded entries by default.
#
# Used by:
#   - Claude Code hooks (plan #7a part 3 reflection sidecar — the
#     tri-modal review's "supersede existing entry X" option)
#   - Operator-debug (manual `python3 evolve.py ...` invocation)
#   - Smoke install fixture tests
#
# The agent-driven `/memory evolve` skill body (see SKILL.md) uses the
# Write tool directly to produce byte-identical files; this script is
# the parallel Python implementation that hooks + tests use.
#
# v0.9.0+ — gemini-cli host removed per ROADMAP item #15.
# Vec-index update deferred to plan #7a part 1 task 4.

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# vault_lock.py is a byte-identical vendored sibling in THIS scripts/ dir
# (DC-9): top-level scripts/vault_lock.py is NOT on sys.path in a real install,
# so the memory skill carries its own copy; scripts/check-vault-lock-parity.sh
# enforces byte-identity between the two. Inject this dir so the sibling import
# resolves however evolve.py is invoked (subprocess or imported-by-hook).
# Mirrors recall.py's vec_index/embed sys.path injection.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from vault_lock import atomic_write, vault_mutex  # noqa: E402

try:
    import yaml  # PyYAML is already a toolkit dep via validate-manifests.py
except ImportError:  # pragma: no cover
    print(
        "ERROR: PyYAML not installed. Run `pip install pyyaml`.",
        file=sys.stderr,
    )
    raise

_KEBAB_SEGMENT = re.compile(r"^[a-z0-9-]+$")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _today_iso() -> str:
    """Today's date in YYYY-MM-DD (ISO 8601) for frontmatter created/updated fields."""
    return date.today().isoformat()


def _today_compact() -> str:
    """Today's date in YYYYMMDD (no hyphens) for archive filename suffix.

    Per the SKILL body's archive path spec: <original-path>.YYYYMMDD.md
    — hyphens omitted to keep filenames readable + match the locked
    convention from PLAN.md task 3.
    """
    return date.today().strftime("%Y%m%d")


def _now_iso_utc() -> str:
    """ISO-8601 UTC timestamp with second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from entry content. Returns (frontmatter, body).

    Raises ValueError if frontmatter is missing or unparseable.
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        raise ValueError("frontmatter missing or malformed (expected leading --- ... --- block)")
    fm_text = m.group(1)
    body = content[m.end():]
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise ValueError(f"frontmatter YAML parse failed: {e}") from e
    if not isinstance(fm, dict):
        raise ValueError(f"frontmatter is not a mapping (got: {type(fm).__name__})")
    return fm, body


def _dump_frontmatter(fm: dict) -> str:
    """Dump a frontmatter dict to YAML in the locked field order.

    Field order (mirrors save.py + the SKILL body's worked example):
      kind / status / created / updated / tags / group / slug / always_load /
      supersedes (if present) / superseded_by (if present) /
      superseded_at (if present) / superseded_reason (if present)
    """
    field_order = [
        "kind", "status", "created", "updated", "tags", "group", "slug",
        "always_load", "supersedes", "superseded_by", "superseded_at",
        "superseded_reason",
    ]
    lines = []
    seen = set()
    for k in field_order:
        if k in fm:
            lines.append(_format_field(k, fm[k]))
            seen.add(k)
    # Preserve any unknown fields in alphabetical order for determinism.
    for k in sorted(fm.keys()):
        if k not in seen:
            lines.append(_format_field(k, fm[k]))
    return "\n".join(lines) + "\n"


def _format_field(key: str, value) -> str:
    """Format a single frontmatter field. Inline lists for `tags` etc."""
    if isinstance(value, list):
        if not value:
            return f"{key}: []"
        return f"{key}: [{', '.join(str(v) for v in value)}]"
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    # Quote strings that contain colons, # comments, or YAML-sensitive chars.
    if isinstance(value, str) and any(c in value for c in [":", "#", "'", '"', "\n"]):
        # Use single-quote YAML escape (double single-quote inside).
        escaped = value.replace("'", "''")
        return f"{key}: '{escaped}'"
    return f"{key}: {value}"


def _compose_entry(fm: dict, body: str) -> str:
    """Compose entry content with YAML frontmatter + body."""
    fm_yaml = _dump_frontmatter(fm)
    body_stripped = body.lstrip("\n").rstrip("\n")
    return f"---\n{fm_yaml}---\n\n{body_stripped}\n"


def _compute_archive_path(vault: Path, old_relative: Path, today: str) -> Path:
    """Compute archive path; append -N suffix if collision (same-day re-evolve)."""
    archive_base = vault / "personal-private" / "_archive" / old_relative
    candidate = archive_base.with_suffix(f"{archive_base.suffix}.{today}.md")
    suffix_n = 2
    while candidate.exists():
        candidate = archive_base.with_suffix(f"{archive_base.suffix}.{today}-{suffix_n}.md")
        suffix_n += 1
        if suffix_n > 99:  # pragma: no cover
            raise FileExistsError(
                f"Archive collision: {candidate} already exists "
                f"and -2 through -99 all taken. Manual cleanup needed."
            )
    return candidate


def evolve_entry(
    vault_path: Path | str,
    old_path: Path | str,
    new_body: str,
    reason: str,
    *,
    new_slug: str | None = None,
) -> tuple[Path, Path]:
    """Evolve an existing entry: archive old + write new in its place.

    Args:
        vault_path: MemoryVault root.
        old_path: Path to old entry (absolute, or relative to vault root).
        new_body: New entry body content.
        reason: Free-text rationale for the evolution.
        new_slug: If set, new entry lands at <old-parent>/<new-slug>.md
            (renamed evolution). If None, new entry takes old's slot.

    Returns:
        Tuple of (new_entry_path, archive_path), both absolute.

    Raises:
        FileNotFoundError: if vault_path or old_path missing.
        ValueError: validation failure (empty reason, status≠active, malformed
            frontmatter, _always-load entry with --new-slug, invalid slug).
        FileExistsError: archive collision after max retries (rare).
    """
    vault = Path(vault_path)
    if not vault.exists() or not vault.is_dir():
        raise FileNotFoundError(f"vault path does not exist or is not a directory: {vault}")

    # Resolve old_path relative to vault if not absolute.
    old = Path(old_path)
    if not old.is_absolute():
        old = vault / old
    if not old.exists():
        raise FileNotFoundError(f"old entry does not exist: {old}")
    if not old.is_file():
        raise ValueError(f"old path is not a file: {old}")

    # Validate reason.
    reason = reason.strip()
    if not reason:
        raise ValueError("reason must be non-empty (captures WHY this evolution happened)")

    # Validate new_slug if provided.
    if new_slug is not None:
        if not _KEBAB_SEGMENT.match(new_slug):
            raise ValueError(f"new_slug {new_slug!r}: must be kebab-case (^[a-z0-9-]+$)")
        # _always-load entries can't be renamed via --new-slug.
        old_relative = old.relative_to(vault)
        if "_always-load" in old_relative.parts:
            raise ValueError(
                "cannot rename _always-load/ entries via --new-slug; "
                "evolve in place only (omit --new-slug)"
            )

    # Read + parse old entry.
    old_content = old.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(old_content)

    # Refuse to evolve non-active entries.
    status = fm.get("status")
    if status != "active":
        raise ValueError(
            f"old entry status is {status!r}, not 'active'. "
            f"Cannot evolve a non-active entry. Manual fix path: edit status "
            f"field if you really want to evolve it."
        )

    # Compute paths.
    old_relative = old.relative_to(vault)
    today = _today_iso()
    today_compact = _today_compact()
    archive_path = _compute_archive_path(vault, old_relative, today_compact)

    if new_slug is not None:
        new_path = old.parent / f"{new_slug}.md"
    else:
        new_path = old  # in-place

    # Build archive content (old frontmatter + superseded fields; body unchanged).
    archive_fm = dict(fm)
    archive_fm["status"] = "superseded"
    new_relative = new_path.relative_to(vault) if new_path.is_absolute() else (vault / new_path).relative_to(vault)
    archive_fm["superseded_by"] = str(new_relative).replace(os.sep, "/")
    archive_fm["superseded_at"] = _now_iso_utc()
    archive_fm["superseded_reason"] = reason
    archive_content = _compose_entry(archive_fm, body)

    # Build new entry content.
    archive_relative = archive_path.relative_to(vault)
    new_fm = {
        "kind": fm.get("kind"),
        "status": "active",
        "created": today,
        "updated": today,
        "tags": fm.get("tags", []),
        "group": fm.get("group", "personal-private"),
        "slug": new_slug if new_slug else fm.get("slug"),
        "always_load": fm.get("always_load", False),
        "supersedes": str(archive_relative).replace(os.sep, "/"),
    }
    new_content = _compose_entry(new_fm, new_body)

    # V5-0: serialize the WHOLE archive→new-entry(→unlink) sequence under ONE
    # per-vault advisory mutex so a concurrent writer can't interleave between
    # the archive write and the new-entry write (a single acquisition, not two).
    # Every target is per-slug / create-style — the archive path is collision-
    # unique (see _compute_archive_path) and the entry is this slug's own file —
    # so mutex-only, NO CAS (DC-2: per-slug entry files are partitioned by
    # ownership). atomic_write creates parent dirs + lands each file via
    # temp(same dir)→fsync→rename; bytes-mode keeps LF byte-exact.
    with vault_mutex(vault):
        # Archive first (additive), then the new entry.
        atomic_write(archive_path, archive_content)
        if new_path == old:
            # In-place: overwrite the old path with the new entry.
            atomic_write(new_path, new_content)
        else:
            # Renamed: write the new path, then delete the old.
            atomic_write(new_path, new_content)
            old.unlink()

    # Enqueue async vec-index ops (task 4):
    #   - delete old entry's index row (if path changed; or upsert if in-place)
    #   - upsert new entry's index row
    #   - upsert archive entry's index row (archive is searchable too, but
    #     recall filters skip status:superseded by default — index entry
    #     exists for `/memory search --include-superseded` future use)
    try:
        import vec_index  # type: ignore
        new_rel = str(new_path.relative_to(vault)).replace(os.sep, "/")
        archive_rel = str(archive_path.relative_to(vault)).replace(os.sep, "/")
        old_rel = str(old.relative_to(vault)).replace(os.sep, "/") if new_path != old else None

        new_tags = new_fm.get("tags") or []
        tag_str = ", ".join(str(t) for t in new_tags) if new_tags else ""
        new_first_para = new_body[:500]
        new_slug_val = new_fm.get("slug", "")
        new_embed_text = f"{new_slug_val} [{tag_str}]\n\n{new_first_para}"
        vec_index.enqueue(vault, new_rel, "upsert", text=new_embed_text)

        old_first_para = body[:500]
        old_tags = archive_fm.get("tags") or []
        old_tag_str = ", ".join(str(t) for t in old_tags) if old_tags else ""
        old_slug_val = archive_fm.get("slug", "")
        archive_embed_text = f"{old_slug_val} [{old_tag_str}]\n\n{old_first_para}"
        vec_index.enqueue(vault, archive_rel, "upsert", text=archive_embed_text)

        if old_rel is not None:
            # Rename case: delete old index row (the file is gone from old_rel).
            vec_index.enqueue(vault, old_rel, "delete")
    except Exception as e:  # pragma: no cover
        print(f"warning: queue append failed: {e}", file=sys.stderr)

    return (new_path, archive_path)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-evolve",
        description=(
            "Archive an existing entry + write a new entry in its place. "
            "Canonical Python implementation behind /memory evolve (see SKILL.md)."
        ),
    )
    parser.add_argument("old_path", help="path to entry being superseded (relative to vault root, or absolute)")
    parser.add_argument("reason", help="rationale for the evolution (captured in archive's superseded_reason frontmatter)")
    parser.add_argument(
        "--new-slug",
        default=None,
        help="if set, new entry lands at <old-parent>/<new-slug>.md (renamed); else in-place",
    )
    parser.add_argument(
        "--body-file",
        default="-",
        help="path to file with new entry body, or '-' for stdin (default: stdin)",
    )
    parser.add_argument(
        "--vault-path",
        required=False,
        help="path to MemoryVault root (overrides MEMORY_VAULT_PATH env var)",
    )
    return parser.parse_args(argv)


def _resolve_vault_path(arg_vault_path: str | None) -> Path:
    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    raise FileNotFoundError(
        "No vault path resolved. Set --vault-path or the MEMORY_VAULT_PATH "
        "environment variable."
    )


def _read_body(body_file: str) -> str:
    if body_file == "-":
        return sys.stdin.read()
    return Path(body_file).expanduser().read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        vault = _resolve_vault_path(args.vault_path)
        new_body = _read_body(args.body_file)
        new_path, archive_path = evolve_entry(
            vault_path=vault,
            old_path=args.old_path,
            new_body=new_body,
            reason=args.reason,
            new_slug=args.new_slug,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    # Stdout: <new-path>\t<archive-path> (tab-separated; script-pipeable).
    print(f"{new_path}\t{archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
