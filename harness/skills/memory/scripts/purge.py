#!/usr/bin/env python3
"""purge.py — the operator-only purge lane.

Filing v2 part 6 (task 2). Deletion is not on the lifecycle axis. A purge is
an operator act that writes a manifest first, and no policy outcome ever
deletes a memory — the rule the contract states in so many words, made
mechanical here in the shape the corpus migration's tool-stub purge proved:

1. `select` walks the memory classes for what the criteria name — today,
   `--lifecycle archived` (the only state a purge should ever start from)
   optionally `--older-than-days N` on `lifecycle_since` — and writes a
   manifest: every path with its title, its body hash, and the inbound
   wikilinks that still point at it. It deletes nothing.
2. `apply` takes that manifest back, refuses unless `--confirm-count`
   equals the manifest's row count and every listed file still hashes as
   the manifest recorded it, deletes exactly those files, and journals each
   as `to: purged` with the manifest's path as the reason.

Nothing under the dreaming layer or the runner imports this module — a test
holds that line — so the only hands on it are the operator's at a shell.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import lifecycle_transitions as lt  # noqa: E402
from filing_engine import _frontmatter  # noqa: E402

MANIFEST_NAME = "manifest.json"
_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")


class RefusedPurge(RuntimeError):
    """The manifest and the disk (or the operator's count) disagree."""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def select(vault: "Path | str", *, lifecycle: str = "archived", older_than_days: "int | None" = None,
           now: "str | None" = None) -> list:
    """The rows a purge would touch: [{rel, title, lifecycle, since, sha256}]."""
    vault = Path(vault)
    today = _dt.date.fromisoformat((now or _dt.date.today().isoformat())[:10])
    rows = []
    for p in lt.memory_notes(vault):
        text = p.read_text(encoding="utf-8")
        if lt.lifecycle_of(text) != lifecycle:
            continue
        fm, _ = _frontmatter(text)
        since = str(fm.get("lifecycle_since") or "")[:10]
        if older_than_days is not None:
            try:
                age = (today - _dt.date.fromisoformat(since)).days
            except ValueError:
                continue  # no dated entry into the state: not old enough to say
            if age < older_than_days:
                continue
        rows.append({"rel": p.relative_to(vault).as_posix(), "title": str(fm.get("title") or p.stem),
                     "lifecycle": lifecycle, "since": since, "sha256": _hash(text)})
    return rows


def inbound_links(vault: "Path | str", rels: list) -> list:
    """Wikilinks elsewhere in the vault that still resolve to a row's stem —
    what a purge would break. Reported, never acted on."""
    vault = Path(vault)
    stems = {Path(r).stem for r in rels}
    targets = set(rels)
    out = []
    for p in sorted(vault.rglob("*.md")):
        rel = p.relative_to(vault).as_posix()
        if rel in targets:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in _LINK_RE.finditer(text):
            target = m.group(1).strip()
            if Path(target).stem in stems:
                out.append({"from": rel, "to": target})
    return out


def write_manifest(vault: "Path | str", rows: list, *, criteria: dict, out_dir: "Path | str | None" = None,
                   now: "str | None" = None) -> Path:
    vault = Path(vault)
    stamp = (now or _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S"))
    out = Path(out_dir) if out_dir else vault / "diagnostics" / "migrations" / "purge" / stamp
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"written": stamp, "vault": str(vault), "criteria": criteria, "count": len(rows), "rows": rows,
                "inbound_links": inbound_links(vault, [r["rel"] for r in rows])}
    path = out / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def apply(vault: "Path | str", manifest_path: "Path | str", *, confirm_count: int, now=None,
          journal: "Path | str | None" = None) -> int:
    """Delete exactly what the manifest lists. Refuses on a count mismatch,
    a file that changed since the manifest, or a file already gone; journals
    every deletion as `to: purged` by the operator. Returns the count."""
    vault = Path(vault)
    manifest_path = Path(manifest_path)
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = m.get("rows") or []
    if confirm_count != len(rows):
        raise RefusedPurge(f"the manifest lists {len(rows)} memories; you confirmed {confirm_count}. Nothing deleted.")
    for r in rows:
        p = vault / r["rel"]
        if not p.exists():
            raise RefusedPurge(f"{r['rel']} is no longer on disk; the manifest is stale. Nothing deleted.")
        if _hash(p.read_text(encoding="utf-8")) != r["sha256"]:
            raise RefusedPurge(f"{r['rel']} changed since the manifest was written. Nothing deleted.")
    ts = lt._now_iso(now)
    for r in rows:
        (vault / r["rel"]).unlink()
        lt.journal_append({"ts": ts, "rel": r["rel"], "from": r.get("lifecycle", "archived"), "to": "purged",
                           "actor": "operator", "reason": f"purge manifest {manifest_path}", "run_id": None},
                          path=journal)
    return len(rows)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="the operator-only purge lane: a manifest first, then exactly that")
    ap.add_argument("--vault", required=True, help="the memory root (the directory holding memory/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select", help="write the manifest of what a purge would delete (deletes nothing)")
    s.add_argument("--lifecycle", default="archived", choices=lt.STATES)
    s.add_argument("--older-than-days", type=int, help="only memories in that state at least this long (by lifecycle_since)")
    s.add_argument("--report-dir", help="where the manifest goes (default: <vault>/diagnostics/migrations/purge/<ts>)")
    a_ = sub.add_parser("apply", help="delete exactly what a manifest lists, on a matching confirmed count")
    a_.add_argument("--manifest", required=True)
    a_.add_argument("--confirm-count", type=int, required=True, help="the manifest's row count, typed by the operator")
    a = ap.parse_args(argv)
    vault = Path(a.vault)
    if a.cmd == "select":
        rows = select(vault, lifecycle=a.lifecycle, older_than_days=a.older_than_days)
        path = write_manifest(vault, rows, criteria={"lifecycle": a.lifecycle, "older_than_days": a.older_than_days},
                              out_dir=a.report_dir)
        links = json.loads(path.read_text(encoding="utf-8"))["inbound_links"]
        print(f"{len(rows)} memor{'y' if len(rows) == 1 else 'ies'} selected, {len(links)} inbound link(s) would break; "
              f"manifest at {path}. Nothing deleted. To apply: purge.py --vault {vault} apply --manifest {path} "
              f"--confirm-count {len(rows)}")
        return 0
    n = apply(vault, a.manifest, confirm_count=a.confirm_count)
    print(f"purged {n} memor{'y' if n == 1 else 'ies'} listed in {a.manifest}; each journaled as purged by the operator")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RefusedPurge as e:
        print(f"purge refused: {e}", file=sys.stderr)
        sys.exit(3)
