#!/usr/bin/env python3
"""eval_write_path.py — one real session's filings against a hand-labeled sample.

Filing v2, the write path (task 5). The design's own doctrine says benchmark
numbers are unusable here: the question is how this engine files this
operator's sessions into this corpus. So the eval is end to end and local —
mine one real transcript the way the reflect hook does, decide every candidate
the way the writer would (against the live corpus, read-only: nothing is
written), and put each decision in front of the operator with the words to
judge it by. The agreement rate is then a count over labels, with n, never an
adjective.

No model judges anything here. Every decision is deterministic given the
transcript, the corpus and the contract, all three named in the worksheet's
header, so a re-run reproduces the rows — replicates are for judged evals,
and this is not one.

Usage:
    eval_write_path.py build --transcript <session.jsonl> --out <worksheet.md> [--vault <memory-root>]
    eval_write_path.py score <worksheet.md>

Labels (one per row, on its `label:` line):
    right             the type, class, operation and flags are what you would have done
    unsure            you cannot tell (counted as not right, reported apart)
    wrong-type        it is a memory, but not of that type
    wrong-op          the update relationship is wrong (should have superseded / should not have / wrong twin)
    wrong-flag        a duplicate or update flag is missing or spurious
    should-not-file   this is not a memory at all
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent.parent / "harness" / "skills" / "memory" / "scripts"
for p in (_SKILL, _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import filing_engine as fe  # noqa: E402
import reflect  # noqa: E402

LABELS = ("right", "wrong-type", "wrong-op", "wrong-flag", "should-not-file", "unsure")
# The operator labels in their own words and adds a note after a dash; the
# scorer reads the first token and the synonyms an actual labelling produced.
# `unsure` counts as not-right and is reported apart, so a doubt is never
# scored as agreement or silently dropped.
_SYNONYMS = {"do-not-file": "should-not-file", "do-not-record": "should-not-file",
             "not-sure": "unsure", "not": "unsure", "wrong-operation": "wrong-op"}
_LABEL_RE = re.compile(r"^label:[ \t]*([A-Za-z-]*)(?:[ \t]+sure)?(?:[ \t]*[-—:].*)?[ \t]*$", re.M)
_ROW_RE = re.compile(r"^### (\d+)\. ", re.M)


def _memory_root(explicit: "str | None") -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("MEMORY_VAULT_PATH")
    if env:
        return Path(env)
    import corpus_scorecard  # noqa: E402  (same skill dir)
    root = corpus_scorecard.memory_root_from_daemon()
    if not root:
        raise SystemExit("no memory root: set $MEMORY_VAULT_PATH or start the daemon")
    return Path(root)


def _provenance(vault: Path) -> dict:
    out = {"memory_root": str(vault)}
    try:
        import storage_rules  # noqa: E402
        out["contract_hash"] = storage_rules.rules().content_hash()
    except Exception as exc:  # the contract is named or its absence is
        out["contract_hash"] = f"unavailable ({exc})"
    binary = os.environ.get("AGENTMD", "").strip() or "agentmd"
    try:
        proc = subprocess.run([binary, "status", "--json"], capture_output=True, text=True, timeout=30)
        status = json.loads(proc.stdout or "{}")
        out["index_documents"] = ((status.get("index_detail") or {}).get("documents")
                                  or ((status.get("health") or {}).get("index") or {}).get("documents"))
    except Exception:
        out["index_documents"] = None
    return out


def build_rows(candidates: list, vault: Path, *, search=None) -> list:
    """One decision per mined candidate, judged against the corpus as it
    stands. Read-only: `decide` never writes."""
    corpus = fe.CorpusIndex(vault)
    search = search if search is not None else fe.default_search(vault)
    rows = []
    for c in candidates:
        d = fe.decide(vault, title=c.title, body=c.body, slug=c.slug,
                      type_hint=reflect._candidate_type(c), confidence=c.confidence,
                      source="conversation", corpus=corpus, search=search)
        rows.append({
            "slug": c.slug, "title": c.title, "category": c.category, "confidence": c.confidence,
            "occurrences": c.occurrences, "body": c.body,
            "type": d.type, "class": d.class_dir, "dest": d.dest_rel, "op": d.op,
            "related": d.related, "filing_confidence": d.filing_confidence,
            "flags": list(d.flags), "reasons": list(d.reasons),
        })
    return rows


def render(rows: list, *, transcript: Path, messages: int, provenance: dict, today: str) -> str:
    lines = [
        "---",
        f"title: write-path eval — {transcript.stem[:8]}",
        "kind: report",
        "status: active",
        f"created: {today}",
        f"updated: {today}",
        "tags: [filing-v2, write-path, eval, labels]",
        "group: harness",
        f"slug: write-path-eval-{transcript.stem[:8]}",
        "---",
        "",
        f"# Write-path eval — session {transcript.stem[:8]}",
        "",
        "One real session's filings, decided against the live corpus and put in front of "
        "you to judge. Nothing was written. Fill every `label:` line with one of: "
        + ", ".join(f"`{l}`" for l in LABELS) + ". Then run "
        "`python3 scripts/health/eval_write_path.py score <this file>`.",
        "",
        "## Provenance",
        "",
        f"- transcript: `{transcript}` ({messages} messages)",
        f"- memory root: `{provenance.get('memory_root')}`",
        f"- contract hash: `{provenance.get('contract_hash')}`",
        f"- index documents at build time: {provenance.get('index_documents')}",
        f"- candidates: {len(rows)}",
        "- judge: none — every decision is deterministic given the three inputs above",
        "",
        "## Decisions",
        "",
    ]
    for i, r in enumerate(rows, 1):
        excerpt = r["body"].strip().replace("\n", " ")
        if len(excerpt) > 400:
            excerpt = excerpt[:400] + " …"
        related = f" · related `{r['related']}`" if r["related"] else ""
        flags = f" · flags {', '.join(r['flags'])}" if r["flags"] else ""
        lines += [
            f"### {i}. {r['title']}",
            "",
            f"- mined as **{r['category']} / {r['confidence']}** (×{r['occurrences']}), slug `{r['slug']}`",
            f"- decision: **{r['op']}** as `{r['type']}` → `{r['dest']}` at {r['filing_confidence']} confidence{related}{flags}",
            f"- why: {'; '.join(r['reasons']) or '—'}",
            f"- text: {excerpt}",
            "",
            "label: ",
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def build(transcript: Path, out: Path, *, vault: "Path | None" = None, today: "str | None" = None) -> Path:
    vault = _memory_root(str(vault) if vault else None)
    mined = reflect.mine_transcript(transcript)
    rows = build_rows(mined["memory_candidates"], vault)
    text = render(rows, transcript=transcript, messages=mined["messages_processed"],
                  provenance=_provenance(vault), today=today or date.today().isoformat())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def wilson(k: int, n: int, z: float = 1.96) -> "tuple[float, float]":
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def score(worksheet: Path) -> dict:
    """Agreement over the labels. A missing or unknown label is an error, not
    a zero: a half-filled worksheet must never score as a finished one."""
    text = worksheet.read_text(encoding="utf-8")
    rows = _ROW_RE.findall(text)
    labels = [_SYNONYMS.get(l.lower(), l.lower()) for l in _LABEL_RE.findall(text)]
    labels = ["unsure" if l == "not" else l for l in labels]
    if len(labels) != len(rows):
        raise ValueError(f"{len(rows)} rows but {len(labels)} label lines")
    missing = [i + 1 for i, l in enumerate(labels) if not l]
    unknown = [(i + 1, l) for i, l in enumerate(labels) if l and l not in LABELS]
    if missing or unknown:
        raise ValueError(f"unlabelled rows {missing}; unknown labels {unknown}")
    n = len(labels)
    right = sum(1 for l in labels if l == "right")
    by_label = {l: labels.count(l) for l in LABELS}
    lo, hi = wilson(right, n)
    return {"n": n, "right": right, "agreement": right / n if n else None,
            "wilson95": (round(lo, 3), round(hi, 3)), "by_label": by_label}


def main(argv: "list | None" = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--transcript", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--vault", help="the memory root; default: the daemon's")
    s = sub.add_parser("score")
    s.add_argument("worksheet")
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "build":
        out = build(Path(args.transcript), Path(args.out), vault=Path(args.vault) if args.vault else None)
        print(out)
        return 0
    try:
        result = score(Path(args.worksheet))
    except ValueError as exc:
        print(f"not scorable: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
