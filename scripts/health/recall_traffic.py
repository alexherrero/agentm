#!/usr/bin/env python3
"""recall_traffic — what recall actually did during real work.

The offline gold set asks whether the ranker can find a known note from a
plausible question. This module asks a different question, of production: what
did recall inject into real sessions, and what happened next. It is the reader
every later task in the online-recall plan builds on, and on its own it already
answers things nobody has looked at — starting with how often a live recall
surfaces nothing at all.

**No model calls here, and no prompt text on disk.** Everything below is read
from two artifacts that already exist:

  * the recall ledger, `~/.cache/agentm/telemetry/recall-history.jsonl` — one
    row per recall, carrying `query_hash` (sha256 of the prompt, truncated to
    16 chars — never the prompt itself), the slugs loaded, and per-hit rank and
    score evidence;
  * the session transcripts under `~/.claude/projects/**/*.jsonl`, where Claude
    Code persists the hook invocation as a record of `type: "attachment"` with
    the entire injected payload in `stdout` and the transparency line in
    `stderr`.

The join between them is the hash: `sha256(user_prompt_text)[:16]` recomputed
from the transcript equals the ledger's `query_hash`. That is checked here
rather than assumed, and `verify_join()` exists so the check is a test rather
than a claim in a docstring.

# The record chain, as it actually is

Worth stating because the obvious reading is wrong. The attachment's
`parentUuid` does **not** point at the user message — it points at the previous
attachment, and a prompt can carry several. The assistant turn is not the next
record in file order either; `last-prompt` and `custom-title` records sit
between them. So:

  * to reach the prompt, walk `parentUuid` **up** while the parent is an
    attachment, until a `user` record appears;
  * to reach the answer, take the attachment's **child** by `parentUuid`.

Both directions are exercised by the join count this module reports.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import re
import statistics
import sys

LEDGER = pathlib.Path(
    os.environ.get("AGENTM_RECALL_HISTORY")
    or pathlib.Path.home() / ".cache/agentm/telemetry/recall-history.jsonl")
PROJECTS = pathlib.Path(
    os.environ.get("AGENTM_TRANSCRIPTS")
    or pathlib.Path.home() / ".claude/projects")

# The eval harness's own `claude -p` calls run with hooks disabled and land in a
# neutral cwd. They are not real work and must never enter a production number.
SYNTHETIC_MARKER = "agentm-neutral-cwd"

HOOK_NAME = "UserPromptSubmit"
HOOK_TAG = "memory-recall"

# `[memory-recall-prompt-submit] Loaded 3 relevant entries: a, b, c (engine:
#  daemon, 68ms, scope=memory-root, terms: '…') (token budget: …)`
_LOADED = re.compile(r"Loaded (\d+) relevant entries?: (.*?) \(engine:", re.S)
_ENGINE = re.compile(r"\(engine: (\w+), (\d+)ms, scope=([\w-]+), terms: '([^']*)'")
_BUDGET = re.compile(r"(\d+) entries excerpted to fit(?:, (\d+) entries omitted)?")
# `top 3 by daemon lexical rank` — the arm that actually ranked, which is how a
# silent degrade from hybrid to lexical becomes visible.
_ARM = re.compile(r"top \d+ by (?:daemon )?(\w+)(?: rank)?")


class JoinError(Exception):
    """The ledger and the transcripts disagree about what they describe."""


def _text(rec: dict) -> str:
    msg = rec.get("message") or {}
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict))
    return ""


def query_hash(prompt: str) -> str:
    """The ledger's key. Kept identical to `recall_counter.record_recall`."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def parse_stderr(line: str) -> dict:
    """The hook's transparency line, as fields.

    Returns what it could read rather than raising: this line's format is the
    hook's own and may gain clauses, and a run that drops every injection
    because one line grew a suffix would be worse than a partial parse.
    """
    out: dict = {}
    if m := _LOADED.search(line):
        out["loaded"] = int(m.group(1))
        out["slugs"] = [s.strip() for s in m.group(2).split(",") if s.strip()]
    if m := _ENGINE.search(line):
        out["engine"] = m.group(1)
        out["elapsed_ms"] = int(m.group(2))
        out["scope"] = m.group(3)
        out["terms"] = m.group(4)
    if m := _BUDGET.search(line):
        out["excerpted"] = int(m.group(1))
        out["omitted"] = int(m.group(2) or 0)
    return out


def parse_stdout(payload: str) -> dict:
    """What the injected block says about how it was ranked."""
    out: dict = {"injected_chars": len(payload)}
    if m := _ARM.search(payload):
        out["arm"] = m.group(1)
    return out


def iter_injections(projects: pathlib.Path = None, include_synthetic: bool = False):
    """Every recall injection found in the transcripts, with its turn.

    Yields dicts carrying the hook's own fields plus `prompt_hash` (never the
    prompt) and `answer_chars`. A transcript that cannot be parsed is skipped
    and counted by the caller rather than failing the sweep — these files are
    written live and the tail of one can be half a line.
    """
    projects = projects or PROJECTS
    for f in sorted(projects.rglob("*.jsonl")):
        if not include_synthetic and SYNTHETIC_MARKER in str(f):
            continue
        try:
            raw = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        recs = []
        for line in raw:
            try:
                recs.append(json.loads(line))
            except ValueError:
                continue
        by_uuid = {r["uuid"]: r for r in recs if r.get("uuid")}
        kids = collections.defaultdict(list)
        for r in recs:
            if r.get("parentUuid"):
                kids[r["parentUuid"]].append(r)

        for rec in recs:
            att = rec.get("attachment") or {}
            if att.get("hookName") != HOOK_NAME:
                continue
            if HOOK_TAG not in (att.get("stderr") or ""):
                continue

            # Up through the attachment chain to the prompt.
            prompt, node, guard = None, by_uuid.get(rec.get("parentUuid")), 0
            while node is not None and guard < 20:
                if node.get("type") == "user":
                    prompt = _text(node)
                    break
                node = by_uuid.get(node.get("parentUuid"))
                guard += 1

            # Down to the answer: the attachment's child, not the next record.
            answer = next((_text(k) for k in kids.get(rec.get("uuid"), [])
                           if k.get("type") == "assistant"), None)

            row = {
                "session": f.stem,
                "ts": rec.get("timestamp"),
                "version": rec.get("version"),
                "duration_ms": att.get("durationMs"),
                "exit_code": att.get("exitCode"),
                "prompt_hash": query_hash(prompt) if prompt else None,
                "answer_chars": len(answer) if answer else 0,
                "has_answer": answer is not None,
            }
            row.update(parse_stderr(att.get("stderr") or ""))
            row.update(parse_stdout(att.get("stdout") or ""))
            yield row


def iter_ledger(path: pathlib.Path = None):
    path = path or LEDGER
    if not path.exists():
        raise JoinError(f"no recall ledger at {path} — nothing to read")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                yield json.loads(line)
            except ValueError:
                continue


def verify_join(ledger_rows: list, injections: list) -> dict:
    """Do the two artifacts describe the same events?

    The whole online instrument rests on this hash matching, so it is measured
    and reported rather than asserted. A zero overlap where injections exist is
    raised, not returned: it means the hash convention moved underneath us, and
    every downstream number would be silently about nothing — the same
    silent-total-null shape that shipped two false refutations in the offline
    arc.
    """
    ledger_hashes = {r.get("query_hash") for r in ledger_rows if r.get("query_hash")}
    with_hash = [i for i in injections if i.get("prompt_hash")]
    matched = [i for i in with_hash if i["prompt_hash"] in ledger_hashes]
    result = {
        "ledger_rows": len(ledger_rows),
        "injections": len(injections),
        "injections_with_prompt": len(with_hash),
        "matched": len(matched),
        "match_rate": round(len(matched) / len(with_hash), 4) if with_hash else 0.0,
    }
    if with_hash and not matched:
        raise JoinError(
            f"none of {len(with_hash)} transcript prompts hash to any of "
            f"{len(ledger_hashes)} ledger keys — the join convention has moved, "
            "and every number built on it would be about nothing")
    return result


def summarize(ledger_rows: list, injections: list) -> dict:
    hit_counts = [r.get("hit_count", 0) for r in ledger_rows]
    zero = sum(1 for c in hit_counts if c == 0)
    ranks = [h.get("rank") for r in ledger_rows for h in (r.get("hits") or [])
             if h.get("rank")]
    stamps = sorted(r["ts"] for r in ledger_rows if r.get("ts"))
    durations = [i["duration_ms"] for i in injections if i.get("duration_ms")]
    elapsed = [i["elapsed_ms"] for i in injections if i.get("elapsed_ms")]

    return {
        "window": [stamps[0][:10], stamps[-1][:10]] if stamps else None,
        "ledger_rows": len(ledger_rows),
        "zero_hit": zero,
        "zero_hit_rate": round(zero / len(ledger_rows), 4) if ledger_rows else None,
        "hit_count_median": statistics.median(hit_counts) if hit_counts else None,
        "rank_distribution": dict(sorted(collections.Counter(ranks).items())),
        "injections": len(injections),
        "with_answer": sum(1 for i in injections if i.get("has_answer")),
        "arm": dict(collections.Counter(
            i.get("arm") for i in injections if i.get("arm"))),
        "engine": dict(collections.Counter(
            i.get("engine") for i in injections if i.get("engine"))),
        "hook_ms_median": statistics.median(durations) if durations else None,
        "hook_ms_p90": (sorted(durations)[int(len(durations) * 0.9)]
                        if len(durations) >= 10 else None),
        "daemon_ms_median": statistics.median(elapsed) if elapsed else None,
        "budget_omitted_turns": sum(1 for i in injections if i.get("omitted")),
        "nonzero_exit": sum(1 for i in injections if i.get("exit_code")),
    }


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--include-synthetic", action="store_true",
                    help="include the eval harness's own hook-disabled runs "
                         "(they are not real work; off by default)")
    args = ap.parse_args(argv)

    try:
        ledger_rows = list(iter_ledger())
        injections = list(iter_injections(include_synthetic=args.include_synthetic))
        join = verify_join(ledger_rows, injections)
    except JoinError as exc:
        print(f"recall-traffic: {exc}", file=sys.stderr)
        return 2

    out = {"summary": summarize(ledger_rows, injections), "join": join}
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    s, j = out["summary"], out["join"]
    w = s["window"]
    print(f"recall traffic — {w[0]} to {w[1]}" if w else "recall traffic")
    print(f"\nthe ledger ({s['ledger_rows']} recalls)")
    print(f"  surfaced nothing   : {s['zero_hit']} ({s['zero_hit_rate']:.1%})")
    print(f"  median hit count   : {s['hit_count_median']}")
    if s["rank_distribution"]:
        shown = list(s["rank_distribution"].items())[:6]
        print("  injected by rank   : "
              + ", ".join(f"#{k}×{v}" for k, v in shown))
    print(f"\nthe transcripts ({s['injections']} injections seen)")
    print(f"  with an answer turn: {s['with_answer']}")
    print(f"  ranking arm        : {s['arm'] or 'unreported'}")
    print(f"  hook latency       : median {s['hook_ms_median']}ms, "
          f"p90 {s['hook_ms_p90']}ms")
    print(f"  daemon latency     : median {s['daemon_ms_median']}ms")
    print(f"  budget dropped some: {s['budget_omitted_turns']} turn(s)")
    print(f"  non-zero exits     : {s['nonzero_exit']}")
    print(f"\nthe join")
    print(f"  prompts recoverable: {j['injections_with_prompt']} of {j['injections']}")
    print(f"  matched the ledger : {j['matched']} ({j['match_rate']:.1%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
