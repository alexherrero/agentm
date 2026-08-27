#!/usr/bin/env python3
"""Give the thin reference notes the content that was never captured.

`_parse_github_api_repos` used to set a candidate's body to `repo["description"]`
— GitHub's one-line description field, which is often a tagline and sometimes
empty. Measured on the live vault, that left **37 of 150** reference notes
holding a title and a link and nothing else.

The operator's expectation, and it is the right one: a reference holds a summary
of the content *and* the link, so the thing does not have to be re-fetched to be
read, and so semantic search has something to match on. Verified: `deepseek-ocr`
gave the dense arm seven words, and a paraphrase of what it is returned nothing.

The capture path is fixed. This is the backlog it left.

## Why not enrichment

Enrichment's job is to distil a raw capture into a memory, and it cannot distil
what was never captured — a body of four words has nothing in it to summarise. It
would have to fetch the source, which is this pass's job, done once rather than
per lookup. Enrichment can run afterwards over a note that now has something to
work from.

## What it does and does not touch

Only the body, and only between the title and the `Source:` line. Frontmatter,
title and slug are left exactly as they are: the design says a settled note's slug
never changes because links point at it, and this is a content backfill rather
than a re-filing.

A note whose README cannot be fetched is left alone and reported. Half a backfill
that quietly skipped is indistinguishable from one that had nothing to do.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import forward_learning as fl  # noqa: E402

FRONTMATTER = re.compile(r"\A---[ \t\r]*\n(.*?)\n---[ \t\r]*\n", re.S)
SOURCE_FM = re.compile(r"^source:\s*(\S+)\s*$", re.M)
TYPE_FM = re.compile(r"^type:\s*reference\s*$", re.M)
STATUS_FM = re.compile(r"^status:\s*(\S+)", re.M)
HEADING = re.compile(r"^\s*#{1,6}\s")
SOURCE_LINE = re.compile(r"^\s*Source:\s*(\S+)\s*$", re.M)
GITHUB_REPO = re.compile(r"^https?://github\.com/([^/]+/[^/#?]+)")

# Below this many prose words, a reference is a pointer rather than a summary.
#
# Twelve, which is where the live corpus's own distribution puts the break: the
# 25th percentile of reference prose is exactly 12 words, and everything at or
# under it is a repo tagline or nothing at all. Not a universal truth — a number
# read off this vault, and it should be re-read if the corpus changes shape.
THIN_WORDS = 12

# How many notes one run may touch, matching the other passes in this arc.
DEFAULT_BATCH = 25


@dataclass
class Finding:
    rel: str
    outcome: str  # "backfilled" | "no-readme" | "not-a-repo" | "already-full"
    words_before: int = 0
    words_after: int = 0
    new_body: str = ""

    def as_dict(self) -> dict:
        out = {"rel": self.rel, "outcome": self.outcome,
               "words_before": self.words_before}
        if self.words_after:
            out["words_after"] = self.words_after
        return out


@dataclass
class Report:
    findings: list = field(default_factory=list)
    scanned: int = 0

    def counts(self) -> dict:
        c: dict = {}
        for f in self.findings:
            c[f.outcome] = c.get(f.outcome, 0) + 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def prose_words(body: str) -> int:
    """Everything that is not the title or the source line."""
    n = 0
    for ln in body.split("\n"):
        t = ln.strip()
        if not t or HEADING.match(ln) or SOURCE_LINE.match(ln):
            continue
        n += len(t.split())
    return n


def repo_of(raw: str) -> str:
    """The `owner/name` this note came from, or "" if it did not come from one."""
    m = FRONTMATTER.match(raw)
    head = m.group(1) if m else ""
    url = ""
    if s := SOURCE_FM.search(head):
        url = s.group(1)
    if not url:
        if s := SOURCE_LINE.search(raw):
            url = s.group(1)
    g = GITHUB_REPO.match(url.strip("'\""))
    return g.group(1).removesuffix(".git") if g else ""


def rewrite_body(raw: str, summary: str) -> str:
    """Put the summary between the title and the source line.

    Rebuilt from the parts rather than patched in place, because the thin notes
    have three shapes — title then blank then source, title then a tagline then
    source, and title then nothing — and a regex that handled all three would be
    harder to read than the reconstruction.
    """
    m = FRONTMATTER.match(raw)
    head = raw[:m.end()] if m else ""
    body = raw[m.end():] if m else raw

    title, source = "", ""
    for ln in body.split("\n"):
        t = ln.strip()
        if not title and HEADING.match(ln):
            title = t
        elif SOURCE_LINE.match(ln):
            source = t
    parts = [p for p in (title, summary.strip(), source) if p]
    return head + "\n" + "\n\n".join(parts) + "\n"


def scan(vault: Path, *, fetch=None, limit: int = 0) -> Report:
    """Decide every thin reference note. Writes nothing."""
    fetch = fetch or _default_fetch
    rep = Report()
    for path in sorted(vault.rglob("*.md")):
        if any(x in path.parts for x in ("_archive", "_shelf", "_inbox", "scratch")):
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        m = FRONTMATTER.match(raw)
        if not m or not TYPE_FM.search(m.group(1)):
            continue
        st = STATUS_FM.search(m.group(1))
        if st and st.group(1).strip("'\"") == "expired":
            continue
        rep.scanned += 1

        body = raw[m.end():]
        before = prose_words(body)
        rel = path.relative_to(vault).as_posix()
        if before > THIN_WORDS:
            continue

        repo = repo_of(raw)
        if not repo:
            rep.findings.append(Finding(rel, "not-a-repo", before))
            continue
        summary = fetch(repo)
        if not summary or len(summary.split()) <= before:
            # No README, or one no better than what is already there. Reported
            # rather than written, because replacing a tagline with a shorter
            # tagline is churn dressed as a repair.
            rep.findings.append(Finding(rel, "no-readme", before))
            continue

        rep.findings.append(Finding(rel, "backfilled", before,
                                    len(summary.split()),
                                    rewrite_body(raw, summary)))
        if limit and len(rep.findings) >= limit:
            break
    return rep


def _default_fetch(repo: str) -> str:
    """The README, trying the branches GitHub actually uses.

    `main` then `master`: the repos API reports `default_branch`, but this pass
    reads notes rather than API responses and the note does not record it.
    """
    for branch in ("main", "master"):
        if text := fl._repo_readme(repo, branch):
            return text
    return ""


def eligible(rep: Report) -> int:
    """How many notes the scan found a better body for.

    Separate from what a run writes, because the cap below means those are two
    different numbers — and a run that printed only the first of them reported
    thirty-two backfilled notes while writing twenty-five.
    """
    return sum(1 for f in rep.findings
               if f.outcome == "backfilled" and f.new_body)


def apply(vault: Path, rep: Report, revert_log, run_id: str, *,
          batch: int = DEFAULT_BATCH) -> str:
    """Write up to `batch` bodies, and return the revert-log entry covering them.

    The cap is deliberate and shared with the dreaming pipeline's auto-apply
    bound, so one run can only ever move a reviewable amount of the corpus. What
    it does not do is announce itself — the caller reports the remainder, and a
    re-run takes the next batch.
    """
    mutations = [(vault / f.rel, f.new_body) for f in rep.findings
                 if f.outcome == "backfilled" and f.new_body][:batch]
    if not mutations:
        return ""
    return revert_log.record_and_apply(run_id, "backfill_reference_bodies",
                                       mutations)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    vault = Path(args.vault)
    rep = scan(vault, limit=args.limit)

    if args.json:
        print(json.dumps({"scanned": rep.scanned, "counts": rep.counts(),
                          "findings": [f.as_dict() for f in rep.findings]},
                         indent=2))
    else:
        print(f"scanned {rep.scanned} reference notes")
        for k, n in rep.counts().items():
            print(f"  {k:<14} {n}")
        for f in rep.findings[:6]:
            if f.outcome == "backfilled":
                print(f"\n  {f.rel}\n    {f.words_before}w -> {f.words_after}w")

    if not args.apply:
        print("dry run — nothing written. Pass --apply to write.", file=sys.stderr)
        return 0

    from revert_log import RevertLog  # noqa: E402
    import time
    run_id = f"refbody-{int(time.time())}"
    entry = apply(vault, rep, RevertLog(vault), run_id, batch=args.batch)
    found = eligible(rep)
    written = min(found, args.batch)
    print(f"\napplied as {run_id} / {entry} — {written} note(s) written")
    if found > written:
        print(f"{found - written} more await a re-run: one run writes at most "
              f"{args.batch} (--batch), so the rest are deferred rather than lost.")
    print(f"undo with: RevertLog(vault).revert({run_id!r}, {entry!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
