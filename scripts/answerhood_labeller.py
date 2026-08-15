#!/usr/bin/env python3
"""answerhood_labeller.py — label search candidates by whether they answer.

The deliberate path's rejection lever, per `wiki/designs/agentm-rejection-and-
vocabulary.md` §3. One Haiku call per search: input is the natural question plus
the candidate set, output is a verdict attached to every candidate.

**It labels. It never deletes.** This is the design's load-bearing choice and
the probe is why. A binary keep/drop gate over the same candidates reached 85.0%
projected negative rejection against a calibrated cross-encoder's 40% — the
judgment is real — but it also destroyed 13.4% of true answers, and the two
effects very nearly cancelled (+0.008 blended). A gate that *labels* hands the
consumer the same rejection signal while leaving a wrong verdict recoverable,
because every note is still there to read. The fast path already made this call
for the same reason when it chose inject-with-metadata over a manufactured
empty.

**It reads the natural question, never the reduced tool query.** On the same
instrument those two inputs fixed 86.7% and 8.9% of the recorded failures. That
is not a tuning detail — it decides where this can be placed at all. A gate
inside `memory_search` sees only the query and lands in the first number, which
is why this is deliberate-path infrastructure and is banned from the interactive
path and the hook by the layering rule.

**Excerpting is the instrument the probe had to correct.** Its first pass showed
each candidate one 1,200-char chunk chosen by raw query-term overlap, and 43.2%
of the apparent over-rejections turned out to be that selector rather than the
model: a note whose decisive words appear once loses to a common term repeated
in its head, and 1% of a 118KB append-only log answers nothing. The corrected
form — IDF-weighted, head + best-middle + tail, small notes shown whole — is
what ships here, and it is the single implementation both this module and the
replay instrument use.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field

# Excerpt geometry, carried over verbatim from the corrected probe instrument.
# The head is what a note declares itself to be, the tail is an append-only
# log's current state, and the middle is where a specific answer usually sits.
HEAD, TAIL, MID, N_MID = 900, 500, 700, 2
WHOLE_IF_UNDER = 3500

# The design's own "binary keep/drop over <=20 candidates" bound, kept for the
# labelling shape so a search that returns more does not silently cost more.
MAX_CANDIDATES = 20

MODEL = "claude-haiku-4-5-20251001"

# Mirrors the embedder's degrade contract: a failure is announced in the output
# rather than swallowed. Shaped like the daemon's existing "(hook skipped:" so a
# reader already trained on those recognizes it, and deliberately distinct from
# every DEGRADED_MARKS string in retrieval_scorecard.py so a scorecard cannot
# confuse one subsystem's degrade for another's.
DEGRADE_MARK = "(labeller unavailable:"

VERDICT_ANSWERS = "answers"
VERDICT_RELATED = "related"
VERDICT_UNLABELLED = "unlabelled"

PROMPT = """You are a retrieval gate. A search returned candidate notes for a question. \
For each candidate, decide whether that note actually ANSWERS the question.

A candidate answers the question if a reader of that note could state the answer from it. \
This includes an answer the reader must work out from what the note records — a date to \
count from, a list to count, a record to compare — as long as the note contains what the \
answer is derived from. It does not answer if it is merely about a related topic, mentions \
the subject in passing, or discusses the area without containing what was asked.

It is normal and correct for NONE of them to answer. Many questions have no answer in this \
corpus at all. If no candidate answers the question, return an empty list.

QUESTION: {question}

CANDIDATES:
{candidates}

Return only JSON, no prose, no code fence: {{"answers": [indices of candidates that answer]}}"""


@dataclass
class Candidate:
    """One search result on its way to a consumer, with room for a verdict."""
    path: str
    text: str = ""
    verdict: str = VERDICT_UNLABELLED


@dataclass
class LabelResult:
    """Every candidate, always. `note` carries the degrade marker when the call
    failed — the candidates come back unlabelled rather than dropped, which is
    the whole contract."""
    candidates: list[Candidate]
    note: str = ""
    cost_usd: float = 0.0
    error: str = ""
    truncated: int = 0
    labelled: bool = True

    @property
    def answering(self) -> list[Candidate]:
        return [c for c in self.candidates if c.verdict == VERDICT_ANSWERS]

    def as_dict(self) -> dict:
        return {
            "candidates": [{"path": c.path, "verdict": c.verdict}
                           for c in self.candidates],
            "note": self.note,
            "labelled": self.labelled,
            "cost_usd": round(self.cost_usd, 6),
            "error": self.error,
            "truncated": self.truncated,
        }


def idf(term: str, df: dict[str, int], n_docs: int) -> float:
    """Rarer terms weigh more. This is excerpt selection, not ranking — it only
    has to beat raw counts, which this corpus already measured as dominated by
    common terms."""
    return math.log(1 + n_docs / (1 + df.get(term, 0)))


def build_df(texts) -> tuple[dict[str, int], int]:
    """Document frequencies over the candidate pool. Approximated from the pool
    rather than the index on purpose: the selector runs where the index is not
    necessarily reachable, and the ranking it feeds is local to one call."""
    df: dict[str, int] = {}
    n = 0
    for t in texts:
        n += 1
        for term in set(re.findall(r"[a-z0-9]+", t.lower())):
            df[term] = df.get(term, 0) + 1
    return df, max(n, 1)


def excerpt(text: str, question: str, df: dict[str, int] | None = None,
            n_docs: int = 1) -> str:
    """Head + the best middle chunks + tail, weighted by IDF. A small note is
    returned whole, so there is no selection step left to get wrong.

    `df`/`n_docs` come from build_df over the candidate pool. Passing neither
    still works and degrades to near-uniform weights — worse selection, never a
    crash, because a labeller that raises on a thin pool is a labeller that
    takes the whole brief down with it.
    """
    if len(text) <= WHOLE_IF_UNDER:
        return text
    df = df or {}
    terms = {t for t in re.findall(r"[a-z0-9]+", question.lower()) if len(t) > 2}
    body = text[HEAD:len(text) - TAIL]
    scored = []
    for start in range(0, max(len(body) - MID, 1), MID // 2):
        chunk = body[start:start + MID]
        low = Counter(re.findall(r"[a-z0-9]+", chunk.lower()))
        # min(count, 3) so a term repeated twenty times in one chunk cannot
        # dominate the sum — the failure mode that produced ep05's wrong verdict.
        score = sum(idf(t, df, n_docs) * min(low.get(t, 0), 3) for t in terms)
        scored.append((score, start, chunk))
    scored.sort(key=lambda x: (-x[0], x[1]))
    mids = [c for _, _, c in scored[:N_MID]]
    return (f"{text[:HEAD]}\n[...]\n" + "\n[...]\n".join(mids) +
            f"\n[...]\n{text[-TAIL:]}")


def parse_verdict(raw: str, n_candidates: int) -> tuple[list[int], str]:
    """Indices the model said answer, plus an error string when it did not say
    anything usable.

    Tolerant of a code fence and of surrounding prose, because the cost of a
    strict parser here is an unlabelled brief rather than a caught bug. Indices
    outside the candidate range are dropped rather than clamped — a model that
    invents index 47 for 5 candidates has not made a small mistake about which
    note it meant.
    """
    if not raw or not raw.strip():
        return [], "empty response"
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return [], "no JSON object in response"
    try:
        doc = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        return [], f"unparseable JSON: {exc}"
    raw_list = doc.get("answers")
    if raw_list is None:
        raw_list = doc.get("keep")          # the probe's own key, accepted
    if raw_list is None:
        return [], "response carried no answers list"
    if not isinstance(raw_list, list):
        return [], "answers was not a list"
    out = []
    for v in raw_list:
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        if 1 <= v <= n_candidates:
            out.append(v)
    return sorted(set(out)), ""


def call_model(prompt: str, timeout: float = 120.0) -> tuple[str, float, str]:
    """One `claude -p` round trip — the layering rule's transport for the
    deliberate path.

    MCP is stripped and hooks disabled for two different reasons, both real: the
    servers cost about 60s of startup per call, and a live reflect hook would
    write into the operator's actual vault from a labelling run.
    `--system-prompt` plus `--tools none` also took the probe from 33,631 input
    tokens to 3,813 — $0.0296 to $0.0048 a call, which is the difference between
    affordable and not at this call volume.
    """
    cmd = [
        "claude", "-p",
        "--model", MODEL,
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--settings", '{"disableAllHooks":true}',
        "--output-format", "json",
        "--system-prompt", "You are a precise retrieval filter. "
                           "Answer only with the requested JSON.",
        "--tools", "none",
    ]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", 0.0, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return "", 0.0, f"claude exited {proc.returncode}" + (
            f": {detail[0][:160]}" if detail else "")
    try:
        payload = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        return "", 0.0, "claude returned unparseable JSON envelope"
    return (payload.get("result") or "",
            float(payload.get("total_cost_usd") or 0.0), "")


def label(question: str, candidates: list[Candidate],
          *, timeout: float = 120.0, caller=call_model) -> LabelResult:
    """Attach a verdict to every candidate. Never drops one.

    `caller` is injected so the tests can drive the parse and degrade paths
    without a model in the loop — the two behaviours worth pinning are what
    happens to the candidate list when the call succeeds and what happens when
    it fails, and neither needs a real round trip to state.
    """
    if not candidates:
        return LabelResult(candidates=[], note="", labelled=True)

    kept = candidates[:MAX_CANDIDATES]
    truncated = len(candidates) - len(kept)

    df, n_docs = build_df([c.text for c in kept if c.text])
    blocks = []
    for i, c in enumerate(kept, 1):
        body = excerpt(c.text, question, df, n_docs).strip() if c.text else "(unavailable)"
        blocks.append(f"[{i}] {c.path}\n{body}\n")

    raw, cost, err = caller(PROMPT.format(question=question,
                                          candidates="\n".join(blocks)))
    if not err:
        indices, err = parse_verdict(raw, len(kept))

    if err:
        # Degrade: every candidate comes back, unlabelled, and the note says so.
        # An unlabelled candidate is exactly today's behaviour, which is what
        # makes this recoverable rather than a silent quality drop.
        for c in candidates:
            c.verdict = VERDICT_UNLABELLED
        return LabelResult(candidates=candidates,
                           note=f"{DEGRADE_MARK} {err})",
                           cost_usd=cost, error=err, truncated=truncated,
                           labelled=False)

    answering = set(indices)
    for i, c in enumerate(kept, 1):
        c.verdict = VERDICT_ANSWERS if i in answering else VERDICT_RELATED
    # Anything past the cap is honestly unlabelled rather than implicitly judged.
    for c in candidates[MAX_CANDIDATES:]:
        c.verdict = VERDICT_UNLABELLED

    note = ""
    if not answering:
        note = ("no candidate appears to answer this question — the notes are "
                "still listed and worth reading, but none of them states the answer")
    if truncated:
        note = (note + "; " if note else "") + (
            f"{truncated} candidate(s) past the {MAX_CANDIDATES}-candidate cap "
            f"were not labelled")
    return LabelResult(candidates=candidates, note=note, cost_usd=cost,
                       truncated=truncated, labelled=True)
