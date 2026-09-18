#!/usr/bin/env python3
"""deep_search.py — the search that looks where ordinary recall does not.

`/memory search --deep <query>` answers one question: *where did that note go?*

Ordinary recall is about what is live. This is about what is not, and it looks
in three places, in the order of how much of the note is left:

1. **The archive.** `archive/memory/<class>/<slug>.md` — the note moved there by
   the night's lifecycle job. It is still a file; it is still readable; it left
   everyday search and nothing else.
2. **The deletion manifests.** `diagnostics/migrations/purge/<stamp>/manifest.json`
   — the row the night wrote *before* it deleted anything, carrying the path,
   the title, how long the note had been silent and the SHA-256 of what was
   removed. Every deletion has its manifest first; that is the rule this reads.
3. **The vault's git history.** For a note the manifests name, the commit that
   deleted it, and the exact `git show` that prints the file as it last stood.
   The manifest says a note existed; git is where it still does.

The three are separate on purpose. A note in the archive needs no git. A note
git can print needs no apology. And a query that finds nothing anywhere is a
real answer — it means the note was never in this vault under that name.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Where the night writes a manifest before it deletes anything
# (`dreaming.PurgeManifestDir`). Read, never written, by this module.
PURGE_DIR = "diagnostics/migrations/purge"

# The memory archive (`dreaming.ArchiveDir`), under the memory root.
ARCHIVE_DIR = "archive"

DEFAULT_K = 10

_TITLE_RE = re.compile(r"(?m)^title:[ \t]*(.+?)[ \t]*$")


def _terms(query: str) -> list[str]:
    """The query's words, lower-cased. Deliberately not a ranker: this is a
    "where did it go" question, and a note that matches every word is the
    answer whatever its BM25 score would have been."""
    return [w for w in re.split(r"[^\w-]+", query.lower()) if w]


def _matches(text: str, terms: list[str]) -> bool:
    low = text.lower()
    return bool(terms) and all(t in low for t in terms)


def _title_of(text: str) -> str:
    m = _TITLE_RE.search(text)
    return m.group(1).strip().strip("\"'") if m else ""


def _body(text: str) -> str:
    """The note below its frontmatter.

    Separated because an excerpt drawn from the frontmatter reads as the note
    saying something when it is only the note being labelled — `title:
    worktree-guard` under a hit for "worktree guard" tells a reader nothing
    they did not already have from the filename.
    """
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    return text[end + 4:] if end >= 0 else text


def _excerpt(text: str, terms: list[str], width: int = 160) -> str:
    """The first line of the body that carries the first term, trimmed."""
    for line in _body(text).splitlines():
        if terms and terms[0] in line.lower():
            line = line.strip()
            if line:
                return line[:width] + ("…" if len(line) > width else "")
    return ""


def archive_hits(root: Path, query: str, k: int = DEFAULT_K) -> list[dict]:
    """Notes in the memory archive that match every term."""
    terms = _terms(query)
    base = Path(root) / ARCHIVE_DIR
    out: list[dict] = []
    if not base.is_dir():
        return out
    for p in sorted(base.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = p.relative_to(root).as_posix()
        if not (_matches(text, terms) or _matches(rel, terms)):
            continue
        out.append({"rel": rel, "title": _title_of(text), "excerpt": _excerpt(text, terms)})
        if len(out) >= k:
            break
    return out


def manifest_rows(root: Path, query: str, k: int = DEFAULT_K) -> list[dict]:
    """Deleted notes the manifests name.

    Newest manifest first, because a note deleted twice — moved, restored,
    deleted again — is most usefully described by the last thing that happened
    to it.
    """
    terms = _terms(query)
    base = Path(root) / PURGE_DIR
    out: list[dict] = []
    if not base.is_dir():
        return out
    for manifest in sorted(base.rglob("manifest.json"), reverse=True):
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for row in doc.get("rows") or []:
            if not isinstance(row, dict):
                continue
            hay = " ".join(str(row.get(f) or "") for f in ("rel", "title"))
            if not _matches(hay, terms):
                continue
            out.append({
                "rel": row.get("rel", ""),
                "title": row.get("title", ""),
                "days_silent": row.get("days"),
                "sha256": row.get("sha256", ""),
                "deleted": doc.get("written", ""),
                "run_id": doc.get("run_id", ""),
                "manifest": manifest.relative_to(root).as_posix(),
            })
            if len(out) >= k:
                return out
    return out


def _git(vault: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(vault), *args],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def commit_for(vault: Path, rel: str) -> dict:
    """Where a deleted note still lives.

    The commit that *deleted* it, and the file as it stood in that commit's
    parent — which is the version anyone asking this question wants. Reported as
    the `git show` to run rather than as a sha and a path to assemble, because
    assembling it wrong prints nothing and looks like the note is gone.
    """
    if not (Path(vault) / ".git").exists():
        return {"available": False,
                "why": "the vault is not a git repository, so nothing kept a copy"}
    line = _git(vault, "log", "--diff-filter=D", "-1",
                "--format=%H%x1f%s%x1f%ad", "--date=short", "--", rel)
    if not line:
        # Never deleted in a tracked commit: either still present, or never
        # committed at all. Both are worth saying rather than reporting nothing.
        if (Path(vault) / rel).exists():
            return {"available": False, "why": "the file is still on disk at that path"}
        return {"available": False,
                "why": "no commit in this vault's history deletes that path"}
    sha, subject, date = (line.split("\x1f") + ["", ""])[:3]
    return {"available": True, "deleted_in": sha, "subject": subject, "date": date,
            "show": f"git -C {vault} show {sha}^:{rel}"}


def search(root: Path, vault: Path, query: str, k: int = DEFAULT_K) -> dict:
    root, vault = Path(root), Path(vault)
    archived = archive_hits(root, query, k)
    deleted = manifest_rows(root, query, k)
    for row in deleted:
        row["git"] = commit_for(vault, _vault_rel(root, vault, row["rel"]))
    return {"query": query, "archived": archived, "deleted": deleted}


def _vault_rel(root: Path, vault: Path, rel: str) -> str:
    """A manifest's path is memory-root-relative; git wants it from the vault
    root. The same note under two bases is the mistake that makes a deep search
    print "no commit deletes that path" for a note git is holding."""
    try:
        prefix = Path(root).resolve().relative_to(Path(vault).resolve()).as_posix()
    except ValueError:
        return rel
    return rel if prefix in ("", ".") else f"{prefix}/{rel}"


def render(report: dict) -> str:
    lines = [f"deep search: {report['query']!r}"]
    archived, deleted = report["archived"], report["deleted"]
    if archived:
        lines.append(f"\nIn the archive ({len(archived)}) — still files, out of everyday search:")
        for h in archived:
            lines.append(f"  {h['rel']}" + (f" — {h['title']}" if h["title"] else ""))
            if h["excerpt"]:
                lines.append(f"      {h['excerpt']}")
    if deleted:
        lines.append(f"\nDeleted ({len(deleted)}) — named by the manifest written before the deletion:")
        for d in deleted:
            lines.append(f"  {d['rel']}" + (f" — {d['title']}" if d["title"] else ""))
            lines.append(f"      deleted {d['deleted']} after {d['days_silent']} silent day(s)"
                         f" · {d['manifest']}")
            g = d.get("git") or {}
            if g.get("available"):
                lines.append(f"      the note lives in {g['deleted_in'][:12]}^ ({g['date']})")
                lines.append(f"      {g['show']}")
            else:
                lines.append(f"      {g.get('why', 'git could not be read')}")
    if not archived and not deleted:
        lines.append("\nNothing in the archive and nothing in a deletion manifest. "
                     "If the note existed, it was never under that name here.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="search the archive, the deletion manifests and the vault's git history")
    ap.add_argument("query", help="the words to look for; a note must match all of them")
    ap.add_argument("--vault", required=True,
                    help="the memory root (the directory holding memory/)")
    ap.add_argument("--vault-root", default=None,
                    help="the vault root, if it is not the memory root's parent chain "
                         "(this is the git repository deep search reads)")
    ap.add_argument("-k", type=int, default=DEFAULT_K, help=f"most hits per place (default {DEFAULT_K})")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args(argv)

    root = Path(args.vault).expanduser()
    vault = Path(args.vault_root).expanduser() if args.vault_root else _vault_root_of(root)
    report = search(root, vault, args.query, args.k)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


def _vault_root_of(root: Path) -> Path:
    """The git repository above the memory root: the nearest ancestor with a
    `.git`, else the memory root itself. Resolved rather than remembered — an
    absolute vault path cached anywhere becomes wrong the moment the vault
    moves, and wrong content reads as valid."""
    p = Path(root).resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return candidate
    return p


if __name__ == "__main__":
    sys.exit(main())
