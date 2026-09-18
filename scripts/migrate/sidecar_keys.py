#!/usr/bin/env python3
"""Re-key the two sidecars from a note's slug to a note's path.

`.lifecycle.json` holds the decay clock and `.heat.json` holds the always-load
heat counters, and both keyed an entry on the note's slug — its basename, or the
`slug:` in its frontmatter. That is not an identity. Measured on the live file
the morning this was written: 26 keys matched more than one file in the vault,
`progress` matching eight, `_index` twenty-four, `design-doc` seventeen. Eight
notes shared one clock, so a recall of any one of them read as a recall of all
eight, and the decay curve was measuring something that was not silence.

Version 2 keys on the note's path from the vault root — the key the daemon's own
index rows already carry — and records each note's body fingerprint so a file
that moves can be followed rather than losing its clock.

What this does with each old key:

  * **one file matches** — re-keyed to that file's path, with its fingerprint.
  * **several files match** — dropped, and named in the manifest. The entry was
    the sum of several notes' hits and cannot be split between them; keeping it
    would mean choosing one note to inherit another's history.
  * **no file matches** — dropped, and named. The note is gone; the clock is
    about nothing.

Both readers already fall back to the old keys while the file says `version: 1`,
so a vault that never runs this keeps every clock it has — it just keeps the
collisions too, and stops gaining new entries under the old shape.

    python3 scripts/migrate/sidecar_keys.py                  # dry run + manifest
    python3 scripts/migrate/sidecar_keys.py --apply --confirm-count N

The manifest is written outside the vault, beside the engine state, so it
survives what it describes. Re-running after an apply is a no-op: a version-2
file has nothing to re-key.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import engine_state  # noqa: E402
import harness_memory  # noqa: E402
import lifecycle  # noqa: E402
import vault_layout  # noqa: E402

SIDECARS = (
    (".lifecycle.json", "the decay clock"),
    (".heat.json", "the always-load heat counters"),
)

_SLUG_RE = re.compile(r'(?m)^slug:[ \t]*["\']?([^"\'\n]+)["\']?[ \t]*$')


def _vault_root() -> Path:
    """The vault root, resolved and never remembered."""
    return Path(harness_memory.vault_path())


def _memory_root(vault_root: Path) -> Path:
    """The memory root the sidecar helpers take."""
    root = vault_layout.env_memory_root()
    if root is not None:
        return Path(root)
    # One spelling: the roots are lowercase since the root-casing landing.
    if (vault_root / "agent").is_dir():
        return vault_root / "agent"
    return vault_root


def index_by_slug(vault_root: Path) -> dict:
    """Every note in the vault, indexed by the keys a version-1 sidecar used.

    Both spellings, because both were written: the file's stem, and the `slug:`
    its frontmatter carries where it carries one and they differ.
    """
    by_slug: dict = collections.defaultdict(list)
    for md in vault_root.rglob("*.md"):
        parts = md.relative_to(vault_root).parts
        if any(p.startswith(".") for p in parts):
            continue
        rel = md.relative_to(vault_root).as_posix()
        by_slug[md.stem].append(rel)
        try:
            head = md.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        m = _SLUG_RE.search(head.split("\n---", 1)[0] if head.startswith("---") else "")
        if m and m.group(1).strip() and m.group(1).strip() != md.stem:
            by_slug[m.group(1).strip()].append(rel)
    return by_slug


def plan_for(path: Path, by_slug: dict, vault_root: Path) -> dict:
    """What re-keying one sidecar would do. Reads; writes nothing."""
    out = {"path": str(path), "version": None, "entries": 0,
           "rekeyed": {}, "ambiguous": {}, "orphaned": [], "already": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    out["version"] = data.get("version")
    entries = data.get("entries") or {}
    out["entries"] = len(entries)
    if data.get("version") != 1:
        return out
    for key in sorted(entries):
        # A key that is already a path is left where it is: a file half-written
        # by an interrupted run must not be re-keyed twice.
        if "/" in key:
            out["already"].append(key)
            continue
        matches = sorted(set(by_slug.get(key, ())))
        if len(matches) == 1:
            out["rekeyed"][key] = matches[0]
        elif matches:
            out["ambiguous"][key] = matches
        else:
            out["orphaned"].append(key)
    return out


def render_manifest(plans: list, when: str) -> str:
    lines = [
        "# Manifest — the sidecars re-keyed from slugs to paths",
        "",
        f"Written {when}, before the change, by agentm-vault plan 11 task 3.",
        "",
        "A version-1 entry keyed on a note's slug. Where exactly one note in the",
        "vault answers to that slug, the entry moves to that note's path. Where",
        "several do, the entry is dropped: it is the sum of several notes'",
        "history and cannot be split between them. Where none does, the note is",
        "gone and the entry is about nothing.",
        "",
        "Reverse: restore the sidecar from the copy this run leaves beside it",
        "(`<name>.before-rekey`), or from the vault's own git history.",
        "",
    ]
    for plan in plans:
        lines += [
            f"## `{plan['path']}`",
            "",
            f"- version on disk: {plan['version']}",
            f"- entries: {plan['entries']}",
            f"- re-keyed: {len(plan['rekeyed'])}",
            f"- dropped, several notes share the slug: {len(plan['ambiguous'])}",
            f"- dropped, no note answers to it: {len(plan['orphaned'])}",
            "",
        ]
        if plan["ambiguous"]:
            lines.append("### Dropped — several notes share the slug")
            lines.append("")
            for key, matches in sorted(plan["ambiguous"].items()):
                lines.append(f"- `{key}` — {len(matches)} notes: " +
                             ", ".join(f"`{m}`" for m in matches[:6]) +
                             ("…" if len(matches) > 6 else ""))
            lines.append("")
        if plan["orphaned"]:
            lines.append("### Dropped — no note answers to it")
            lines.append("")
            lines.append(", ".join(f"`{k}`" for k in sorted(plan["orphaned"])))
            lines.append("")
        if plan["rekeyed"]:
            lines.append("### Re-keyed")
            lines.append("")
            for key, rel in sorted(plan["rekeyed"].items()):
                lines.append(f"- `{key}` -> `{rel}`")
            lines.append("")
    return "\n".join(lines) + "\n"


def apply_plan(path: Path, plan: dict, vault_root: Path) -> int:
    """Rewrite one sidecar to version 2. Returns how many entries moved."""
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries") or {}
    rebuilt = {}
    for key, rel in plan["rekeyed"].items():
        entry = dict(entries.get(key) or {})
        if path.name == lifecycle.LIFECYCLE_SIDECAR_NAME:
            # `rel` is a path from the vault root, which is where the note is.
            fp = lifecycle.fingerprint_of(vault_root / rel)
            if fp:
                entry["fingerprint"] = fp
        rebuilt[rel] = entry
    for key in plan["already"]:
        rebuilt[key] = entries.get(key)
    data["entries"] = rebuilt
    data["version"] = 2
    backup = path.with_suffix(path.suffix + ".before-rekey")
    if not backup.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return len(rebuilt)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", help="vault root (default: the configured one)")
    ap.add_argument("--apply", action="store_true", help="rewrite the sidecars")
    ap.add_argument("--confirm-count", type=int,
                    help="the dry run's re-keyed total, typed by the operator")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    vault_root = Path(args.vault) if args.vault else _vault_root()
    memory_root = _memory_root(vault_root)
    by_slug = index_by_slug(vault_root)

    plans = []
    for name, what in SIDECARS:
        path = vault_layout.sidecar_path(memory_root, name)
        plan = plan_for(path, by_slug, vault_root)
        plan["what"] = what
        plans.append(plan)

    total = sum(len(p["rekeyed"]) for p in plans)
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_dir = engine_state.engine_state_dir() / "sidecar-keys"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_dir / f"manifest-sidecar-keys-{stamp}.md"
    manifest.write_text(render_manifest(plans, when), encoding="utf-8")

    for plan in plans:
        print(f"{plan['path']} — {plan['what']}")
        print(f"  version {plan['version']} · {plan['entries']} entries · "
              f"{len(plan['rekeyed'])} re-keyed · {len(plan['ambiguous'])} share a slug · "
              f"{len(plan['orphaned'])} orphaned")
    print(f"manifest: {manifest}")

    if not args.apply:
        print(f"dry run. To apply: --apply --confirm-count {total}")
        return 0
    if args.confirm_count != total:
        print(f"refusing: --confirm-count {args.confirm_count} does not match the "
              f"{total} entries this run would re-key", file=sys.stderr)
        return 2
    moved = 0
    for plan, (name, _what) in zip(plans, SIDECARS):
        if plan["version"] != 1:
            continue
        moved += apply_plan(Path(plan["path"]), plan, vault_root)
    print(f"applied: {moved} entries now keyed on a path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
