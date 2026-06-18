#!/usr/bin/env python3
# watchlist_review.py — /memory watchlist review command (plan #7b task 5).
#
# Walks `<vault>/personal/_skill-watchlist/<source-slug>/<pattern-slug>.md`
# entries; presents each to the operator with promote / dismiss / defer
# options. Pattern mirrors `ideas_promote.py`'s GC flow (keep / archive /
# delete) — same interactive-prompt + non-TTY-default-to-keep semantics.
#
# Action semantics:
#   - promote → frontmatter `status: promoted` + `promoted_at` timestamp.
#     Entry stays in place but is marked "ready for operator's manual
#     authoring of crickets/skills/<x>/" (the adapt-don't-import
#     contract: only the operator forks, never the agent).
#   - dismiss → entry moves to `_skill-watchlist/_archive/<source-slug>/`
#     with collision-safe naming; `dismissed_at` timestamp added.
#     Preserves audit trail without cluttering active watchlist.
#   - defer → frontmatter `status: deferred` + `deferred_until: <iso-date>`
#     + `defer_reason` field. Entry stays in place; future list operations
#     can filter `deferred_until` to surface only re-eligible entries.
#
# Locked design calls:
#   - Never deletes outright (matches `ideas_promote.py gc`'s never-silent-
#     deletion contract). Dismiss = archive, not rm.
#   - Non-TTY stdin → default to skip (no action taken; entry stays in
#     watchlist). Batch dismissals require explicit --batch-action.
#   - Promote is an annotation-only operation. The actual fork to
#     `crickets/skills/<x>/` is the operator's manual work outside
#     this script (adapt-don't-import architectural rule).

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


_VALID_ACTIONS = {"promote", "dismiss", "defer", "skip"}


def _resolve_vault_path(arg_path: str | None) -> Path:
    if arg_path:
        return Path(arg_path).expanduser()
    env = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    raise ValueError(
        "vault path required: pass --vault-path or set MEMORY_VAULT_PATH"
    )


def _watchlist_root(vault: Path) -> Path:
    return vault / "personal" / "_skill-watchlist"


def _archive_root(vault: Path) -> Path:
    return _watchlist_root(vault) / "_archive"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _today_iso() -> str:
    return date.today().isoformat()


def _parse_frontmatter(path: Path) -> dict[str, str]:
    """Return a flat dict of frontmatter fields (string values only)."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}
    out: dict[str, str] = {}
    for line in lines[1:end_idx]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        out[key.strip()] = val.strip()
    return out


def list_watchlist_entries(vault: Path) -> list[dict]:
    """Walk _skill-watchlist/<source-slug>/*.md (excluding _archive/).

    Returns sorted list of {path, source_slug, pattern_slug, frontmatter}.
    """
    root = _watchlist_root(vault)
    if not root.exists():
        return []
    out: list[dict] = []
    for source_dir in sorted(root.iterdir()):
        if not source_dir.is_dir() or source_dir.name == "_archive":
            continue
        for entry in sorted(source_dir.glob("*.md")):
            if not entry.is_file():
                continue
            fm = _parse_frontmatter(entry)
            out.append({
                "path": str(entry),
                "source_slug": source_dir.name,
                "pattern_slug": entry.stem,
                "frontmatter": fm,
            })
    return out


def _rewrite_frontmatter(
    path: Path, updates: dict[str, str], *, removes: set[str] | None = None,
) -> None:
    """Update frontmatter fields in-place; preserve body byte-for-byte.

    Adds or modifies the fields in `updates`; deletes any field listed in
    `removes`. Field order preserved for existing keys; new keys appended.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return
    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return
    fm_lines = lines[1:end_idx]
    body_lines = lines[end_idx + 1:]
    removes = removes or set()
    seen_keys: set[str] = set()
    new_fm: list[str] = []
    for line in fm_lines:
        stripped = line.rstrip("\n")
        if ":" not in stripped or stripped.strip().startswith("#"):
            new_fm.append(line)
            continue
        key = stripped.split(":", 1)[0].strip()
        if key in removes:
            continue
        if key in updates:
            seen_keys.add(key)
            new_fm.append(f"{key}: {updates[key]}\n")
        else:
            new_fm.append(line)
    for key, val in updates.items():
        if key not in seen_keys:
            new_fm.append(f"{key}: {val}\n")
    # Rebuild file: front-matter wrappers + new_fm + body.
    out: list[str] = ["---\n"]
    out.extend(new_fm)
    out.append("---\n")
    out.extend(body_lines)
    # Write as bytes for LF-only line endings (Windows portability — same
    # pattern as save.py / ideas_surface.py / adapt_skills.py).
    path.write_bytes("".join(out).encode("utf-8"))


def promote_entry(entry_path: Path) -> dict:
    """Mark entry as promoted (annotation only). Returns status dict."""
    if not entry_path.exists():
        return {"action": "error", "reason": f"entry not found: {entry_path}"}
    _rewrite_frontmatter(
        entry_path,
        updates={
            "status": "promoted",
            "promoted_at": _utcnow_iso(),
            "updated": _today_iso(),
        },
        removes={"deferred_until", "defer_reason", "dismissed_at"},
    )
    return {"action": "promoted", "path": str(entry_path)}


def dismiss_entry(vault: Path, entry_path: Path) -> dict:
    """Move entry to _archive/<source-slug>/ with collision-safe naming."""
    if not entry_path.exists():
        return {"action": "error", "reason": f"entry not found: {entry_path}"}
    source_slug = entry_path.parent.name
    archive_dir = _archive_root(vault) / source_slug
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / entry_path.name
    n = 1
    while target.exists():
        target = archive_dir / f"{entry_path.stem}-{n}.md"
        n += 1
    # First annotate the entry with dismissed_at, then move.
    _rewrite_frontmatter(
        entry_path,
        updates={
            "status": "dismissed",
            "dismissed_at": _utcnow_iso(),
            "updated": _today_iso(),
        },
        removes={"deferred_until", "defer_reason", "promoted_at"},
    )
    shutil.move(str(entry_path), str(target))
    return {"action": "dismissed", "archived_to": str(target)}


def defer_entry(
    entry_path: Path, *, until_date: str, reason: str | None = None,
) -> dict:
    """Mark entry as deferred until <until_date>."""
    if not entry_path.exists():
        return {"action": "error", "reason": f"entry not found: {entry_path}"}
    updates = {
        "status": "deferred",
        "deferred_until": until_date,
        "updated": _today_iso(),
    }
    if reason:
        updates["defer_reason"] = json.dumps(reason)  # JSON-quote in case of colons
    _rewrite_frontmatter(
        entry_path,
        updates=updates,
        removes={"dismissed_at", "promoted_at"},
    )
    return {"action": "deferred", "path": str(entry_path), "until": until_date}


def _prompt_action(
    entry: dict, *, stdin=sys.stdin, stdout=sys.stdout,
) -> str:
    """Display entry + prompt for action. Returns one of _VALID_ACTIONS."""
    fm = entry["frontmatter"]
    print("", file=stdout)
    print("─" * 72, file=stdout)
    print(f"Watchlist entry: {entry['source_slug']}/{entry['pattern_slug']}", file=stdout)
    print(f"  status:        {fm.get('status', '?')}", file=stdout)
    print(f"  classification: {fm.get('evaluator_classification', fm.get('rubric_confidence', '?'))}", file=stdout)
    print(f"  source_url:    {fm.get('source_url', '?')}", file=stdout)
    print(f"  github_stars:  {fm.get('github_stars', '?')}", file=stdout)
    print(f"  trusted_org:   {fm.get('trust_from_trusted_org', '?')}", file=stdout)
    print(f"  rubric_score:  {fm.get('rubric_score', '?')}", file=stdout)
    print("─" * 72, file=stdout)
    print("Action: [p]romote / [d]ismiss / [f] defer (default: skip)", file=stdout)
    stdout.flush()
    try:
        choice = stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "skip"
    if not choice:
        return "skip"
    if choice in ("p", "promote"):
        return "promote"
    if choice in ("d", "dismiss"):
        return "dismiss"
    if choice in ("f", "defer"):
        return "defer"
    if choice in ("s", "skip"):
        return "skip"
    print(f"  (unknown choice {choice!r}; defaulting to skip)", file=stdout)
    return "skip"


def _prompt_defer_date(
    *, stdin=sys.stdin, stdout=sys.stdout, default_days: int = 30,
) -> str:
    """Prompt for defer until-date. Returns ISO date string."""
    from datetime import timedelta
    default_dt = date.today() + timedelta(days=default_days)
    default_iso = default_dt.isoformat()
    print(
        f"  defer until (YYYY-MM-DD; blank = default {default_iso}): ",
        end="", file=stdout,
    )
    stdout.flush()
    try:
        choice = stdin.readline().strip()
    except (EOFError, KeyboardInterrupt):
        return default_iso
    if not choice:
        return default_iso
    # Validate.
    try:
        date.fromisoformat(choice)
        return choice
    except ValueError:
        print(f"  (invalid date {choice!r}; using default {default_iso})", file=stdout)
        return default_iso


def review_watchlist(
    vault: Path,
    *,
    interactive: bool = True,
    stdin=sys.stdin,
    stdout=sys.stdout,
    stderr=sys.stderr,
) -> dict:
    """Walk watchlist entries; prompt for action; perform updates.

    Non-TTY stdin → defaults every prompt to skip (never silent action).
    """
    entries = list_watchlist_entries(vault)
    stats = {"total": len(entries), "promoted": 0, "dismissed": 0, "deferred": 0, "skipped": 0, "errors": 0}
    if not entries:
        print("[watchlist] no pending entries", file=stderr)
        return stats
    if interactive and not stdin.isatty():
        print(
            "[watchlist] interactive mode requested but stdin is not a TTY; "
            "defaulting all prompts to skip (never silent action)",
            file=stderr,
        )
        interactive = False
    for entry in entries:
        if interactive:
            action = _prompt_action(entry, stdin=stdin, stdout=stdout)
        else:
            action = "skip"
        path = Path(entry["path"])
        if action == "promote":
            result = promote_entry(path)
            if result["action"] == "promoted":
                stats["promoted"] += 1
            else:
                stats["errors"] += 1
        elif action == "dismiss":
            result = dismiss_entry(vault, path)
            if result["action"] == "dismissed":
                stats["dismissed"] += 1
            else:
                stats["errors"] += 1
        elif action == "defer":
            until = _prompt_defer_date(stdin=stdin, stdout=stdout)
            result = defer_entry(path, until_date=until)
            if result["action"] == "deferred":
                stats["deferred"] += 1
            else:
                stats["errors"] += 1
        else:
            stats["skipped"] += 1
    return stats


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-watchlist",
        description=(
            "Review pending entries in _skill-watchlist/ — promote (mark "
            "ready for operator's manual fork to crickets/skills/), "
            "dismiss (archive), defer (snooze for N days). Non-TTY stdin "
            "defaults to skip (never silent action; same contract as "
            "ideas_promote.py gc)."
        ),
    )
    sub = parser.add_subparsers(dest="cmd")

    sub_list = sub.add_parser("list", help="list pending watchlist entries (JSON)")
    sub_list.add_argument("--vault-path", default=None)

    sub_review = sub.add_parser("review", help="interactive review (default)")
    sub_review.add_argument("--vault-path", default=None)

    sub_promote = sub.add_parser("promote", help="promote a specific entry by slug")
    sub_promote.add_argument("source_slug")
    sub_promote.add_argument("pattern_slug")
    sub_promote.add_argument("--vault-path", default=None)

    sub_dismiss = sub.add_parser("dismiss", help="dismiss a specific entry by slug")
    sub_dismiss.add_argument("source_slug")
    sub_dismiss.add_argument("pattern_slug")
    sub_dismiss.add_argument("--vault-path", default=None)

    sub_defer = sub.add_parser("defer", help="defer a specific entry by slug")
    sub_defer.add_argument("source_slug")
    sub_defer.add_argument("pattern_slug")
    sub_defer.add_argument("--until", required=True, help="ISO date YYYY-MM-DD")
    sub_defer.add_argument("--reason", default=None)
    sub_defer.add_argument("--vault-path", default=None)

    return parser.parse_args(argv)


def _entry_path_from_slugs(
    vault: Path, source_slug: str, pattern_slug: str,
) -> Path:
    return _watchlist_root(vault) / source_slug / f"{pattern_slug}.md"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        vault = _resolve_vault_path(args.vault_path)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not vault.exists() or not vault.is_dir():
        print(
            f"ERROR: vault path does not exist or is not a directory: {vault}",
            file=sys.stderr,
        )
        return 1
    cmd = args.cmd or "review"
    if cmd == "list":
        entries = list_watchlist_entries(vault)
        print(json.dumps([
            {
                "source_slug": e["source_slug"],
                "pattern_slug": e["pattern_slug"],
                "path": e["path"],
                "status": e["frontmatter"].get("status", "?"),
                "classification": e["frontmatter"].get(
                    "evaluator_classification",
                    e["frontmatter"].get("rubric_confidence", "?"),
                ),
            }
            for e in entries
        ], indent=2))
        return 0
    if cmd == "promote":
        path = _entry_path_from_slugs(vault, args.source_slug, args.pattern_slug)
        result = promote_entry(path)
        print(json.dumps(result, indent=2))
        return 0 if result["action"] == "promoted" else 1
    if cmd == "dismiss":
        path = _entry_path_from_slugs(vault, args.source_slug, args.pattern_slug)
        result = dismiss_entry(vault, path)
        print(json.dumps(result, indent=2))
        return 0 if result["action"] == "dismissed" else 1
    if cmd == "defer":
        try:
            date.fromisoformat(args.until)
        except ValueError as e:
            print(f"ERROR: --until must be ISO date YYYY-MM-DD: {e}", file=sys.stderr)
            return 1
        path = _entry_path_from_slugs(vault, args.source_slug, args.pattern_slug)
        result = defer_entry(path, until_date=args.until, reason=args.reason)
        print(json.dumps(result, indent=2))
        return 0 if result["action"] == "deferred" else 1
    # default: review
    stats = review_watchlist(vault)
    print(json.dumps(stats, indent=2))
    return 0 if stats["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
