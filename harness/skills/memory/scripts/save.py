#!/usr/bin/env python3
# save.py — canonical /memory save primitive.
#
# Writes a markdown entry to MemoryVault with YAML frontmatter.
# Used by:
#   - Claude Code hooks (plan #7a part 3 reflection sidecar)
#   - Operator-debug (manual `python3 save.py ...` invocation)
#   - Smoke install fixture tests
#
# The agent-driven `/memory save` skill body (see SKILL.md) uses the
# Write tool directly to produce byte-identical entry files; this
# script is the parallel Python implementation that hooks + tests use.
#
# v0.9.0+ — gemini-cli host removed per ROADMAP item #15.
# Embedding integration deferred to plan #7a part 1 task 4.

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

# vault_lock.py is a byte-identical vendored sibling in THIS scripts/ dir
# (DC-9): top-level scripts/vault_lock.py is NOT on sys.path in a real install,
# so the memory skill carries its own copy; scripts/check-vault-lock-parity.sh
# enforces byte-identity between the two. Inject this dir so the sibling import
# resolves however save.py is invoked (subprocess or imported-by-hook). Mirrors
# recall.py's vec_index/embed sys.path injection.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from vault_lock import atomic_write, vault_mutex  # noqa: E402

# Validation regexes (must match the skill body's documented contracts).
_KEBAB_SEGMENT = re.compile(r"^[a-z0-9-]+$")
# Group is a vault subdirectory path: one or more kebab segments joined by `/`.
# (Widened V4 #33: the live vault uses deep groups like
# `projects/<slug>/decisions` — the prior single-sub-segment regex was behind
# reality. Backward-compatible: 1- and 2-segment groups still match.)
_GROUP_SEGMENT = re.compile(r"^[a-z0-9-]+(/[a-z0-9-]+)*$")

# Locked frontmatter field order — the schema source of truth shared with
# `vault_lint.py` (V4 #33 DC-2: the lint reuses this so the two can't drift).
# `_build_frontmatter` below emits fields in this exact order; a test pins them.
FRONTMATTER_FIELD_ORDER: tuple[str, ...] = (
    "kind", "status", "created", "updated", "tags", "group", "slug",
    "always_load", "supersedes", "heat_pin",
)
# Required fields = every field except the optional ones.
_OPTIONAL_FIELDS = frozenset({"supersedes", "heat_pin"})
REQUIRED_FRONTMATTER_FIELDS: tuple[str, ...] = tuple(
    f for f in FRONTMATTER_FIELD_ORDER if f not in _OPTIONAL_FIELDS
)


def _today_iso() -> str:
    """Today's date in YYYY-MM-DD UTC."""
    return date.today().isoformat()


def _validate_kebab(value: str, arg_name: str) -> None:
    """Raise ValueError if `value` is not kebab-case (^[a-z0-9-]+$)."""
    if not _KEBAB_SEGMENT.match(value):
        raise ValueError(
            f"{arg_name} {value!r}: must be kebab-case (^[a-z0-9-]+$)"
        )


def _validate_group(value: str) -> None:
    """Raise ValueError if `value` is not a valid group path."""
    if not _GROUP_SEGMENT.match(value):
        raise ValueError(
            f"group {value!r}: must be one or more kebab-case segments joined "
            f"by / (^[a-z0-9-]+(/[a-z0-9-]+)*$)"
        )


def _validate_tags(tags: list[str]) -> None:
    """Raise ValueError if any tag is not kebab-case."""
    for t in tags:
        if not _KEBAB_SEGMENT.match(t):
            raise ValueError(
                f"tag {t!r}: must be kebab-case (^[a-z0-9-]+$)"
            )


def _build_frontmatter(
    *,
    kind: str,
    group: str,
    slug: str,
    tags: list[str],
    always_load: bool,
    supersedes: str | None,
) -> str:
    """Build the locked-order YAML frontmatter for a memory entry.

    Field order is locked for deterministic diffs:
      kind / status / created / updated / tags / group / slug / always_load /
      supersedes (omitted if None).
    """
    today = _today_iso()
    # Build the tags list inline (`[]` if empty, `[a, b, c]` otherwise).
    tags_yaml = "[]" if not tags else "[" + ", ".join(tags) + "]"
    lines = [
        "---",
        f"kind: {kind}",
        "status: active",
        f"created: {today}",
        f"updated: {today}",
        f"tags: {tags_yaml}",
        f"group: {group}",
        f"slug: {slug}",
        f"always_load: {'true' if always_load else 'false'}",
    ]
    if supersedes:
        lines.append(f"supersedes: {supersedes}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def save_entry(
    vault_path: Path | str,
    kind: str,
    slug: str,
    body: str,
    *,
    group: str = "personal-private",
    always_load: bool = False,
    tags: list[str] | None = None,
    supersedes: str | None = None,
) -> Path:
    """Write a memory entry to the vault. Returns the absolute path written.

    Raises:
        FileNotFoundError: if `vault_path` doesn't exist or isn't a directory.
        ValueError: if kind / slug / group / tags fail validation.
        FileExistsError: if the target path already exists (use /memory evolve
            to supersede; never overwrite from save).
    """
    vault = Path(vault_path)
    if not vault.exists():
        raise FileNotFoundError(f"vault path does not exist: {vault}")
    if not vault.is_dir():
        raise FileNotFoundError(f"vault path is not a directory: {vault}")

    _validate_kebab(kind, "kind")
    _validate_kebab(slug, "slug")
    _validate_group(group)
    tags = tags or []
    _validate_tags(tags)

    # Compute target path. --always-load overrides --group: routes to
    # personal-private/_always-load/<slug>.md regardless of group.
    if always_load:
        target = vault / "personal-private" / "_always-load" / f"{slug}.md"
    else:
        target = vault / group / kind / f"{slug}.md"

    if target.exists():
        raise FileExistsError(
            f"entry already exists at {target}: use /memory evolve to "
            f"supersede the existing entry, or pick a different slug"
        )

    # Create parent dirs.
    target.parent.mkdir(parents=True, exist_ok=True)

    # Build content.
    fm = _build_frontmatter(
        kind=kind,
        group=group,
        slug=slug,
        tags=tags,
        always_load=always_load,
        supersedes=supersedes,
    )
    # Ensure body ends with single trailing newline.
    body_stripped = body.rstrip("\n")
    content = fm + "\n" + body_stripped + "\n"

    # V5-0: route the per-slug entry write through the one per-vault advisory
    # mutex + the canonical atomic writer. The mutex gives torn-write safety
    # when two writers race the same target's <name>.tmp path; atomic_write
    # lands the bytes via temp(same dir)→fsync→rename. Bytes-mode (str encoded
    # utf-8, no newline translation) keeps LF-only line endings byte-exact
    # across Mac/Linux/Windows — the V4 Windows-CI fix the bare write_bytes
    # here used to own, now owned by atomic_write. This is a per-slug CREATE
    # (the FileExistsError guard above forbids overwrite), so mutex-only — no
    # CAS (DC-2: per-slug entry files are partitioned by ownership).
    with vault_mutex(vault):
        atomic_write(target, content)

    # Enqueue async embedding + vec-index upsert (task 4).
    # File write is complete; queueing is fast + synchronous + never raises
    # on missing deps (queue is JSONL append; sqlite-vec required only at
    # drain time). Operators run `python3 vec_index.py drain` (or future
    # idle-time hook) to actually process the queue.
    try:
        import vec_index  # type: ignore
        # Embed text = title-frontmatter-tags + body's first paragraph
        # (per parent design's Infrastructure section). For v1 we use
        # the slug + tags + first 500 chars of body — captures enough
        # semantic content for recall without huge embedding inputs.
        first_para = body[:500]
        tag_str = ", ".join(tags) if tags else ""
        embed_text = f"{slug} [{tag_str}]\n\n{first_para}"
        rel_path = str(target.relative_to(vault)).replace(os.sep, "/")
        vec_index.enqueue(vault, rel_path, "upsert", text=embed_text)
    except Exception as e:  # pragma: no cover
        # Queueing should never fail in practice, but if it does (e.g.
        # vault filesystem read-only), log + continue. File write succeeded.
        print(f"warning: queue append failed: {e}", file=sys.stderr)

    return target


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-save",
        description=(
            "Save a memory entry to MemoryVault. "
            "Canonical Python implementation behind /memory save (see SKILL.md)."
        ),
    )
    parser.add_argument("kind", help="entry kind (kebab-case)")
    parser.add_argument("slug", help="entry slug (kebab-case; filename stem)")
    parser.add_argument(
        "--vault-path",
        required=False,
        help="path to MemoryVault root (overrides MEMORY_VAULT_PATH env var)",
    )
    parser.add_argument(
        "--group",
        default="personal-private",
        help="memory group (default: personal-private)",
    )
    parser.add_argument(
        "--always-load",
        action="store_true",
        help=(
            "route to personal-private/_always-load/ + set always_load: true. "
            "Overrides --group."
        ),
    )
    parser.add_argument(
        "--tags",
        default="",
        help="comma-separated tags (kebab-case each)",
    )
    parser.add_argument(
        "--supersedes",
        default=None,
        help="path to entry this one supersedes (sets supersedes: frontmatter)",
    )
    parser.add_argument(
        "--body-file",
        default="-",
        help=(
            "path to file containing the entry body, or '-' to read from stdin "
            "(default: stdin)"
        ),
    )
    return parser.parse_args(argv)


def _resolve_vault_path(arg_vault_path: str | None) -> Path:
    """Resolve vault path per the documented chain.

    Order: --vault-path arg > MEMORY_VAULT_PATH env > error.
    (The third level — ~/.config/crickets/memory.yml — is deferred to a
    future task; documented in SKILL.md.)
    """
    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    raise FileNotFoundError(
        "No vault path resolved. Set --vault-path or the MEMORY_VAULT_PATH "
        "environment variable. (Config-file resolution path "
        "~/.config/crickets/memory.yml is documented but not yet "
        "implemented as of v0.9.0; tracked for a future task.)"
    )


def _read_body(body_file: str) -> str:
    """Read entry body from a file or stdin (when body_file == '-')."""
    if body_file == "-":
        return sys.stdin.read()
    return Path(body_file).expanduser().read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        vault = _resolve_vault_path(args.vault_path)
        body = _read_body(args.body_file)
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        target = save_entry(
            vault_path=vault,
            kind=args.kind,
            slug=args.slug,
            body=body,
            group=args.group,
            always_load=args.always_load,
            tags=tags,
            supersedes=args.supersedes,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    # Stdout: just the absolute path written (script-pipeable).
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
