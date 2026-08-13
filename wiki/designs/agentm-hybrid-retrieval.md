---
title: AgentM Hybrid Retrieval — the recall ladder
status: proposed
kind: design
scope: architecture
area: agentm
parent: agentm-rescope-topology.md
seeded: 2026-08-12
---

# AgentM Hybrid Retrieval — the recall ladder

## Why now, in numbers

The week-1 ruling — FTS5-only, no vector sidecar — carried its own re-open
condition: a driver weaker than Opus calling `memory_search`, where lexical-only
retrieval degrades. That condition has now fired, on our own scorecard rather
than on anyone's advocacy. The prompt-submit recall hook is the degenerate case
of a weak driver — single-shot, 300ms, nothing iterating for it — and measured
on the frozen `goldv2-20260812` corpus it reads **10.9% R@5** against the agent
layer's 0.725. The gap is not tuning. Three findings close every lexical exit:

- **An oracle over term subsets reaches 82.8%.** The information sits in the
  extracted terms for 53 of 64 questions; ANDing six terms destroys it, and no
  implementable selection policy recovers half of what the oracle can, because
  choosing the right subset means already knowing which terms the answer
  contains.
- **Fusion without rejection fails its own rule.** Max-score subset fusion
  lifts R@5 to 42.2% and drops negative rejection to 0% — every question with
  no answer returns a confident wrong one.
- **No BM25 floor can fix that**, because negatives score *higher* than
  answerables (median top-1 15.1 against 13.2, overlapping and inverted). BM25
  measures term-match strength, not answer-existence. A plausible question
  about a well-discussed topic outscores a question answered by one small note,
  and no monotone threshold separates them.

So the fast path needs a retrieval channel that crosses vocabulary gaps and a
rejection signal that is actually about relevance. Those are the two things
this design adds, and nothing else.

## Two paths, split by budget

**Fast path — the hook.** Dual retrieval (FTS5 and dense vectors) → reciprocal
rank fusion → cross-encoder rerank of the top ~20 → a floor on the
cross-encoder score → inject the survivors, or inject nothing. Entirely local,
no network, no LLM call; the budget is 300ms warm and every stage is
milliseconds. A cross-encoder score is a calibrated relevance judgment in a
bounded range — the signal a BM25 floor pretends to be and measurably is not.

**Deliberate path — the agent and the background jobs.** The interactive agent
keeps `memory_search` and remains its own query expansion and rejection gate —
that is what 0.725 already is, and it inherits the better tool underneath.
Unattended consumers (briefs, digests, filing) may add an LLM rejection gate
where latency is free.

## One daemon, models as supervised children

There is no second service. `agentmd` spawns `llama-server` children — one
embedder, one reranker — health-checks them, restarts them with backoff, and
reports them on its own status surface: `embedder ok (warm)` or `embedder
DEGRADED — hybrid off, lexical-only`. Loopback only, no config surface, no MCP
exposure. A model crash degrades retrieval visibly and never touches the
committer, the watcher, or the gate.

In-process inference was considered and declined. Every credible engine is
cgo, and `CGO_ENABLED=0` is what makes the static binary, the toolchain-free
CI build, and the NAS cross-compile work. It would also reopen a settled
decision: Go beat Rust for this daemon *conditional on* the daemon not running
models in-process. An external model server the machine might already run
(Ollama-style) was also declined — that is a second independently-managed
resident process, a dependency this daemon cannot version or fold into its own
health honestly.

The vector store is deliberately boring: embeddings in a plain SQLite table,
brute-force cosine over the embedded spaces. At 9,473 notes × 768 dimensions
that is 29MB of vectors inside a 98MB index, and single-digit milliseconds per
query. No ANN library, no new index format, and the store stays a deletable
cache rebuilt from files.

**Scope: the vector arm covers `memory/`, `desk/` and `external/`.** The
original call was `memory/` only, on the reasoning that memory notes are atomic
by capture doctrine and so embed whole while `desk/` would need a chunking
policy. Building it that way refuted the call on its own gold set. The set's 64
answerable questions expect 90 note paths, and 65 of them are in `desk/` or
`external/` against 25 in `memory/` — so a `memory/`-only arm cannot reach most
of what it is scored on, and worse, it *actively regresses* the baseline: at
42.2% for lexical fusion alone, adding a `memory/`-only dense arm scored 40.6%.
The mechanism is reciprocal-rank displacement. The dense arm returns its 50 best
candidates for every query whether or not any of them is relevant, and for a
question answered in `desk/` those 50 are all noise that outranks a correct
lexical hit sitting at rank 3. Widening the scope to where the answers actually
live took the same arm to 54.7%.

What survives of the original reasoning is the part about length, and it is now
a measured cost rather than an exclusion: `desk/` runs 589 tokens at the median
against `memory/`'s 187, so 562 of 9,473 notes (5.9%) exceed the embedder's
window and are embedded from their head. `_meta/` and `_vault-archive/` stay
out — `_meta`'s p90 is 203,000 tokens, which is the case a chunking policy
genuinely exists for, and there is still no chunking policy.

## Fusion and rejection, with the failed alternatives on record

**RRF is the default fusion** (`k=60`), because it is calibration-free across
corpora. Score fusion was examined and its portability trap measured: AgentKV's
sigmoid constants, fitted to a 120-note corpus, saturate on ours — every match
maps to ~1.0 and the lexical channel collapses to a presence bit. A
recalibrated score-fusion arm may run as a comparison; it never ships as the
default on constants fitted elsewhere.

**The cross-encoder floor is the fast path's rejection gate.** Its threshold is
chosen *before* the gold run — from the literature prior (~0.35) checked
against the score distribution on off-gold probe queries — never by sweeping
the gold set, which is fitting to the answer sheet. If the floor fails to
separate negatives the way the BM25 floor failed, that is a finding, and the
LLM gate on the deliberate path becomes load-bearing rather than optional.

## LLM judgment stages, cast and placed

| stage | model | where | why |
|---|---|---|---|
| query expansion | none standing | driver in-session; deterministic extractor in the hook; job-inline in background | the iterating agent *is* expansion — measured at 0.725 |
| rejection gate | Claude Haiku 4.5, JSON, low effort | deliberate path only, via `claude -p` | binary keep/drop over ≤20 chunks is a Haiku-shaped judgment |
| filing / invalidation | Sonnet 5, batched | dreaming's filing pass, propose→confirm, behind the corpus-write gate | supersede/merge is the judgment regex mining failed at; wrong supersede loses a true fact |

The layering rule that governs all of it: **the daemon touches local models
only; runner jobs shell out via `claude -p`; the interactive session delegates
nothing.** Every shell-out runs with `--settings '{"disableAllHooks":true}'` —
a filing job whose subprocess fires the reflect hooks is a feedback loop, and
`--disallowedTools` does not close that surface. Opus appears nowhere in the
background by design; that is what pays for all-day interactive autonomy.

## Measurement: the ladder is the contract

Fixed corpus (`goldv2-20260812`, 9,971 notes, vault head `4391c9e`), fixed 84
questions, deterministic retrieval-layer runs — one scorecard column per landed
step, each with its rule written before its code. Levels from other systems do
not transfer and are never targets; AgentKV's FTS baseline reads 62.86% where
ours reads 10.9% on identical architecture, because 120 notes against 9,971
changes what an AND leaves standing.

| step | column | rule that must hold | measured |
|---|---|---|---|
| 0 | `and-of-6` | measured: 10.9% R@5 / 35% rejection | 10.9% / 35% |
| 1 | `lexical-fusion` | reproduces ~42% in-daemon, flag-gated, hook untouched | 42.2% / 0% — met |
| 2 | `+vector RRF` | paraphrase ≥50%, research-corpus ≥58%, distinctive-token does not regress | 54.7% overall; research-corpus 58.3% and distinctive-token 8/12 met, **paraphrase 38.9% missed** |
| 3 | `+rerank+floor` | rejection ≥70% while R@5 ≥ step 2 | |
| 4 | `+temporal` | episodic ≥60%, others flat | |
| 5 | `hook e2e` | p50/p90 <300ms warm, strata within noise of step 4 | |
| 6 | `agent layer` | week-1 driver rerun, n≥6, ≥0.725 — non-regression | |

Step 6 exists because the two layers have disagreed once already: the alias
backfill was slightly better at the tool level and 3.85 points worse at the
agent level. A retrieval-layer win is necessary, never sufficient.

Targets: every stratum in the 70–90% band at the hook layer; rejection ≥70%
from the cross-encoder floor now, ≥90% where the LLM gate runs. The 82.8%
oracle is the lexical-subset ceiling, not the hybrid ceiling — the vector arm
reaches notes with zero term overlap, so exceeding it is possible and expected
on the paraphrase strata.

## Distribution

`install.sh --daemon` grows the model leg: fetch the pinned embedding GGUF into
`~/.local/share/agentm/models/`, verified by SHA-256, discarding the file on a
mismatch rather than keeping it — a GGUF that is subtly not the one we pinned
produces vectors that are merely worse, and every search still answers, which is
the failure nobody notices for months. `--no-embedder` opts out, and an install
without the model runs lexical-only and says so on every status surface.

The real size envelope is larger than the ~5MB + 30–130MB first sketched here.
The selected embedder is **333MB** on disk, and `llama-server` is not built by
the installer at all: it is a cgo project, and building it is exactly what the
daemon's static pure-Go constraint exists to avoid, so its absence is reported
rather than repaired (`brew install llama.cpp` on macOS).

Model selection happens at build time via a bake-off on the research-corpus
stratum, not by reputation — see the amendment log for the one that ran.

## Out of scope, with owners

The Sonnet filing pass, the staleness stratum that tests it, the alias
accretion loop (blocked on a real usage-confirmation signal from heat or
reflect), and the Haiku gate's rollout beyond briefs all belong to the memory
storage / filing / dreaming arc that follows this one. Intent-sourced aliases
at capture time are already standing practice and need no build.

## Related

- `agentm-rescope-week1-experiment.md` — the FTS5-only ruling this supersedes
  for the hook path, and the standing scorecard discipline this inherits.
- `agentm-rescope-topology.md` — the daemon this extends; its sidecar
  conditional is now exercised.
- `scripts/health/results/goldv2/NOTES.md` — every measurement cited above.
- The AgentKV reciprocal handoff
  (`<vault>/Agent/desk/projects/agentm/_harness/agentkv-reciprocal-handoff.md`)
  — the cross-system findings this design absorbs and corrects.

## Amendment log

*Newest first.*

- **2026-08-12 · the vector arm's scope widened from `memory/` to `memory/` +
  `desk/` + `external/`, and the step-2 rule's paraphrase clause recorded as
  missed.** Building the rung refuted the scope call on this design's own gold
  set. 65 of the 90 expected answer paths are in `desk/` or `external/`, so a
  `memory/`-only arm could not reach most of what it was scored on — and it did
  not merely under-reach, it regressed the lexical baseline, 42.2% → 40.6%, by
  reciprocal-rank displacement: the dense arm returns 50 candidates for every
  query whether or not any is relevant, and a noise document at dense rank 1
  outscores a correct lexical hit at rank 3. Widening to where the answers live
  gives **54.7%**. Why not keep the narrow scope and re-word the rule: a rung
  that regresses the rung below it is not a rung. Why not add chunking and take
  `_meta/` too: its p90 is 203,000 tokens, which is a real chunking problem and
  out of scope here. Residual cost, measured: 562 of 9,473 notes (5.9%) exceed
  the embedder's window and are embedded from their head. **Re-audit trigger:**
  a chunking policy landing, or the gold set being re-labelled against a corpus
  with a different answer distribution.

- **2026-08-12 · EmbeddingGemma-300M pinned as the embedder; Qwen3-Embedding-
  0.6B declined.** The bake-off on the research-corpus stratum came in one
  question apart (9/12 against 8/12), which is not a difference, so it was rerun
  at full parity — identical window, identical scope, complete backfills, idle
  machine. There EmbeddingGemma wins **every stratum** and the full set 35/64
  against 24/64. Qwen's 37.5% is below the 42.2% lexical baseline, so its dense
  arm is a net loss on this corpus. Why not Qwen despite the longer window: the
  window is what its candidacy rested on and it is unusable here — batch buffers
  size from the context, and at `-b/-ub 8192` it takes a Metal page fault six
  requests into an idle machine, after which llama.cpp requires a process
  restart. That made the parity comparison necessary and the parity comparison
  settled it on quality regardless. EmbeddingGemma is also half the disk, ~4x the
  throughput, and 25% smaller vectors. **Re-audit trigger:** different GPU
  hardware or a llama.cpp release that fixes the fault would make Qwen's window
  testable again, but its quality would have to beat 35/64 to matter.

- **2026-08-12 · the cross-encoder floor is load-bearing earlier than stated.**
  Step 3's floor was specified as the rejection gate for negatives. The
  displacement finding above makes it also the only thing that stops the dense
  arm promoting confident noise on questions it cannot answer, since cosine
  similarity has no natural empty result. This raises the cost of step 3 failing
  its own rule: it would leave both problems open, not one. **Re-audit trigger:**
  step 3's measured rejection.

- **2026-08-12 · seeded.** Written after the goldv2 measurement campaign:
  baseline, candidacy analysis, fusion arms, floor sweep, and the oracle
  ceiling, all on the frozen corpus. The Evolve-4 metadata-overlay proposal
  from the work setup was reviewed alongside and declined for the vault —
  files-are-truth means a note's frontmatter cannot lie about its own status —
  while its three underlying hazards were adopted as conventions (see the
  memory design's amendment of the same date).
