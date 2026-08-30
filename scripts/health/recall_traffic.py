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


def _assistant_text(rec: dict) -> str:
    """Prose *and* reasoning from one assistant record.

    Thinking counts. It is where a model works over injected material, and a
    definition that excluded it would undercount use to keep the notion tidy —
    408 of 676 immediate children are thinking blocks, so excluding them was
    most of why the first pass saw so little.
    """
    c = (rec.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    if not isinstance(c, list):
        return ""
    out = []
    for b in c:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            out.append(b.get("text") or "")
        elif b.get("type") == "thinking":
            out.append(b.get("thinking") or "")
    return "\n".join(out)


def _is_typed_prompt(rec: dict) -> bool:
    """A prompt the operator typed, as opposed to a tool result.

    Claude Code writes tool results as `user` records too, so "the next user
    record" is not the end of a response — it is usually the middle of one.
    A typed prompt carries plain string content and no tool payload.
    """
    if rec.get("type") != "user":
        return False
    if rec.get("toolUseResult") is not None:
        return False
    c = (rec.get("message") or {}).get("content")
    if isinstance(c, str):
        return bool(c.strip())
    if isinstance(c, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result"
                       for b in c)
    return False


def _response_span(start_uuid: str, recs: list, order: dict) -> str:
    """Every assistant word between an injection and the next typed prompt.

    Walked in file order rather than by parentUuid: the uuid chain threads
    through tool results and sidechains, and following it stops early at the
    first tool call. File order is what "what happened next" means here.
    """
    i = order.get(start_uuid)
    if i is None:
        return ""
    out = []
    for rec in recs[i + 1:]:
        if _is_typed_prompt(rec):
            break
        att = rec.get("attachment") or {}
        if att.get("hookName") == HOOK_NAME:
            break  # the next prompt's own injection
        if rec.get("type") == "assistant" and not rec.get("isSidechain"):
            out.append(_assistant_text(rec))
    return "\n".join(t for t in out if t)


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


def iter_injections(projects: pathlib.Path = None, include_synthetic: bool = False,
                    with_text: bool = False):
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
        order = {r["uuid"]: n for n, r in enumerate(recs) if r.get("uuid")}
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

            # The response span, not the single child: 408 of 676 immediate
            # children are thinking blocks and 192 are tool calls, so reading
            # only the child found prose on 11% of turns and called the rest
            # answerless.
            answer = _response_span(rec.get("uuid"), recs, order) or None

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
            if with_text:
                # In memory, for the caller's own pass. Never persisted — the
                # module's contract is no prompt or answer text on disk. A
                # judge needs all three: the query it is judging, the block
                # that was injected, and what came back.
                row["_answer"] = answer or ""
                row["_prompt"] = prompt or ""
                row["_injected"] = att.get("stdout") or ""
            row.update(parse_stderr(att.get("stderr") or ""))
            row.update(parse_stdout(att.get("stdout") or ""))
            yield row


# ── the deterministic signal ────────────────────────────────────────────────
#
# Did the turn that followed an injection visibly use any of it? This is the
# comparator any judge has to beat before it earns its cost: on human-labeled
# data, RAGChecker found plain BLEU and ROUGE-L beating three published LLM
# judges by three to five times, so a cheap string check is not a strawman.
#
# It is a **floor, not a measure**. ContextCite's finding is that what a model
# cites and what it actually used diverge, and slug matching is weaker than
# citation: a turn can lean entirely on an injected note while naming none of
# it, and will be scored here as unused. Read the number as "at least this
# often the context was demonstrably used", never as utilisation.

_SLUG_SPLIT = re.compile(r"[-_]+")

# A single word counts as evidence only if it is rare in the corpus of answers.
# The first version used a hand-written stoplist and a length floor; a
# hand-check of six real turns showed why that cannot work. "carry header_path,
# content, and embedding together" scored a hit for the note
# `i-want-to-put-together-to`, and 88.8% of all `used` verdicts rested on one
# word — led by `progress`, which names 86 notes in the vault. A stoplist would
# have to enumerate English to catch that.
#
# Rarity in what a model actually writes covers both ways a single word lies:
# words that name many notes cannot say which one was read, and ordinary words
# appear whether or not anything was read. Both are common in the corpus.
#
# The 1% bar follows from a contamination budget rather than tuning. Traffic
# carries ~4.5 injected notes per turn, so at background rate p the expected
# false positives are 4.5 * turns * p; holding that under a tenth of the
# verdicts observed puts p near 0.014, rounded down to 0.01 for strictness.
# See results/online-v1/RULE-single-word-evidence.md.
RARE_MAX_TURN_SHARE = 0.01


def _candidates(slug: str) -> list:
    """The note's name, verbatim and with separators as spaces.

    Only the whole name. Reading every surviving verdict by hand showed that
    fragments were the entire source of contamination: `observability` matched
    inside an unrelated `observability-email-daily.yaml`, `20260813` inside a
    different timestamp, and `notifications` is simply an English word. A
    fragment of a name is not the name, and it never was evidence.

    Which of these two forms means anything is not decided here; rarity is
    decided by measurement in `rare_evidence`.
    """
    out = [slug, slug.replace("-", " ").replace("_", " ")]
    return sorted({o for o in out if len(o) > 6})


# The smallest non-zero share a corpus of n turns can express is 1/n, so below
# 1/RARE_MAX_TURN_SHARE turns nothing can qualify as rare and every run reports
# zero. That reads as a finding about the system when it is a fact about the
# corpus, so a short run is refused rather than answered.
MIN_TURNS_FOR_RARITY = int(1 / RARE_MAX_TURN_SHARE) + 1


def background_rates(answers: list, candidates) -> dict:
    """Share of turns whose answer contains each candidate.

    Measured by substring, the same way the match itself is made — a rate
    computed by tokenizing would not describe the test being applied. Measured
    over turns rather than occurrences, because a name written ten times in one
    answer is still evidence from one turn.
    """
    lows = [(a or "").lower() for a in answers]
    n = len(lows)
    if not n:
        return {}
    return {c: sum(1 for a in lows if c in a) / n
            for c in {x.lower() for x in candidates}}


def rare_evidence(injections: list) -> set:
    """Which candidate strings this corpus rarely produces on its own.

    Rates are measured only for candidates that matched somewhere. One that
    matches nowhere yields no verdict either way, so its rate cannot change an
    answer — and skipping them turns a scan of every name against every answer
    into a scan of a few hundred.
    """
    answers = [i.get("_answer") or "" for i in injections]
    # No guard for a short corpus here, deliberately. A candidate reaches this
    # set only by matching somewhere, so its share is at least 1/n, and below
    # MIN_TURNS_FOR_RARITY that is already above the bar — the arithmetic does
    # the excluding, and a guard restating it could never change an answer.
    # `overlap_summary` is where a short run gets refused, because there the
    # difference between "nothing qualified" and "the corpus cannot say" is
    # visible to a reader.
    seen = set()
    for i, low in zip(injections, (a.lower() for a in answers)):
        for slug in (i.get("slugs") or []):
            seen.update(c.lower() for c in _candidates(slug) if c.lower() in low)
    rates = background_rates(answers, seen)
    return {c for c, r in rates.items() if r < RARE_MAX_TURN_SHARE}


def slug_evidence(slug: str, rare: set = None) -> list:
    """The strings whose presence would show *this* note was used.

    Every candidate is held to the same bar, including the slug itself. The
    earlier version exempted the slug and its spaced form as "identifiers, not
    words", which is true of `agentm-auto-organization` and false of
    `design-doc` — the phrase "design doc" appears in 1.5% of answers and
    earned that note ten of the twenty-six verdicts then standing.

    With no corpus (`rare=None`) there is no evidence at all. Rarity is the
    whole of the test, and a single turn cannot estimate it; a floor computed
    without the means to check should come out too low rather than too high.
    """
    if rare is None:
        return []
    return [c for c in _candidates(slug) if c.lower() in rare]


def used_slugs(slugs: list, answer: str, rare: set = None) -> list:
    """Which injected slugs left a visible trace in the answer."""
    low = (answer or "").lower()
    return [s for s in slugs
            if any(ev.lower() in low for ev in slug_evidence(s, rare))]


def overlap_summary(injections: list) -> dict:
    """Injected-vs-used, over turns that carry both an injection and an answer."""
    rows = [i for i in injections
            if i.get("slugs") and i.get("has_answer") and i.get("_answer")]
    if not rows:
        return {"turns": 0, "note": "no joined turns carried both an injection "
                                    "and an answer"}
    if len(rows) < MIN_TURNS_FOR_RARITY:
        return {"turns": len(rows),
                "note": f"a corpus of {len(rows)} turns cannot tell a rare "
                        f"note name from a common one — the smallest share it "
                        f"can express is {1/len(rows):.1%}, above the "
                        f"{RARE_MAX_TURN_SHARE:.0%} bar. Needs "
                        f"{MIN_TURNS_FOR_RARITY}."}
    rare = rare_evidence(rows)
    per_turn, injected, used_total = [], 0, 0
    for i in rows:
        hit = used_slugs(i["slugs"], i["_answer"], rare)
        injected += len(i["slugs"])
        used_total += len(hit)
        per_turn.append(len(hit))
    wasted = sum(1 for n in per_turn if n == 0)
    return {
        "turns": len(rows),
        "notes_injected": injected,
        "notes_visibly_named": used_total,
        "note_named_rate": round(used_total / injected, 4) if injected else None,
        "turns_naming_no_note": wasted,
        "turns_naming_no_note_rate": round(wasted / len(rows), 4),
        "median_named_per_turn": statistics.median(per_turn),
        "rare_evidence_strings": len(rare),
        "rare_max_turn_share": RARE_MAX_TURN_SHARE,
        "floor_caveat": "this counts naming, not use. A turn that leaned "
                        "entirely on an injected note without writing its name "
                        "scores zero here. The plan calls the second figure a "
                        "wasted-injection rate; it is not one — models rarely "
                        "cite context they use, and the gap between naming and "
                        "using is what the judge exists to measure.",
    }


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
        injections = list(iter_injections(
            include_synthetic=args.include_synthetic, with_text=True))
        join = verify_join(ledger_rows, injections)
    except JoinError as exc:
        print(f"recall-traffic: {exc}", file=sys.stderr)
        return 2

    out = {"summary": summarize(ledger_rows, injections), "join": join,
           "overlap": overlap_summary(injections)}
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
    ov = out.get("overlap") or {}
    if ov.get("turns"):
        print(f"\ninjected-vs-named ({ov['turns']} turns, deterministic floor)")
        print(f"  notes injected     : {ov['notes_injected']}")
        print(f"  named in the reply : {ov['notes_visibly_named']} "
              f"({ov['note_named_rate']:.1%})")
        print(f"  turns naming none  : {ov['turns_naming_no_note']} "
              f"({ov['turns_naming_no_note_rate']:.1%})")
        print(f"  evidence strings   : {ov['rare_evidence_strings']} note names "
              f"rare enough to mean anything (<{ov['rare_max_turn_share']:.0%} "
              f"of turns)")
        print(f"  This counts naming, not use — the second figure is not a "
              f"wasted-injection rate.")

    print(f"\nthe join")
    print(f"  prompts recoverable: {j['injections_with_prompt']} of {j['injections']}")
    print(f"  matched the ledger : {j['matched']} ({j['match_rate']:.1%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
