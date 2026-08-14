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

**The cross-encoder floor was to be the fast path's rejection gate, and it is
refuted** (2026-08-13, mechanism completed 2026-08-14 — see the amendment log).
The conditional this section always carried fired: the floor fails to separate
negatives the way the BM25 floor failed, and the post-mortem showed why no
floor placement can fix it — on a single-owner corpus whose negative questions
are by design about topics the corpus is saturated with, positives and hard
negatives interleave on cross-encoder score in *either* query format (measured
0.003–0.959 against 0.267–0.906). Similarity is not answerhood. The LLM gate
on the deliberate path is therefore **load-bearing**, and the fast path
currently ships with no rejection gate at all — the hook-cutover step owns the
injection policy that follows from that (inject-with-metadata and let the
reading agent judge, or hold the lexical arm's honest-empty where it occurs).

## LLM judgment stages, cast and placed

| stage | model | where | why |
|---|---|---|---|
| query expansion | none standing | driver in-session; deterministic extractor in the hook; job-inline in background | the iterating agent *is* expansion — measured at 0.725 |
| rejection gate | Claude Haiku 4.5, JSON, low effort | deliberate path only, via `claude -p` — **load-bearing** since the CE floor's refutation | binary keep/drop over ≤20 chunks is a Haiku-shaped judgment; answerhood, which the CE post-mortem showed similarity cannot proxy |
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
| 3 | `+rerank+floor` | rejection ≥70% while R@5 ≥ `+chunking` (56.2%) | **refuted** — jina (the bake-off winner) 39.1% R@5 / 40% rejection; bge 32.8% / 10%. Both floored `ep05` recovered, `rc08` did not. Neither ships. Post-mortem (2026-08-14): partly a query-format test artifact, structurally a similarity≠answerhood interleave — see the amendment log. |
| 3.5 | `+question` | the daemon accepts the natural question alongside the terms; the dense arm embeds the question, the lexical arms keep the terms. **Rule:** overall R@5 ≥ 62.5% (40/64), pure-paraphrase holds ≥50% (≥9/18), no stratum regresses by more than one question, and the per-question gain/loss diff is published with the column | **met, well past the floor** — 75.0% (48/64), pure-paraphrase 61.1% (11/18), every stratum improved and none regressed (12 gained / 0 lost). 11 of the 16 diagnosed dense-top-5 candidates converted. |
| 4 | `+lex3` | overall R@5 ≥ 51/64 (79.7%), no stratum regresses by more than one, per-question diff published | **refuted** — 76.6% (49/64), net +1 against a required net +3. Regression clause held (no stratum lost more than one); the overall floor did not. 3 of the 7 diagnosed candidates converted (`dt07`, `dt10`, `rc03`); 2 unrelated losses (`pp02`, `rd04`) to reciprocal-rank displacement. Code kept, quarantined behind `-lex3` — see the amendment log. |
| 5.5 | `+temporal` | *(re-scoped, moved after the cutover)* no stratum regresses at all against `hook e2e`; the 14 at-risk date-phrase questions enumerated with before/after ranks | |
| 5 | `hook e2e` | p50/p90 <300ms warm, strata within noise of step 4 | |
| 6 | `agent layer` | week-1 driver rerun, n≥6, ≥0.725 — non-regression | |

Step 6 exists because the two layers have disagreed once already: the alias
backfill was slightly better at the tool level and 3.85 points worse at the
agent level. A retrieval-layer win is necessary, never sufficient.

Targets: every stratum in the 70–90% band at the hook layer; rejection ≥70%
now belongs to the deliberate-path LLM gate (the CE floor that was to deliver
it on the fast path is refuted), ≥90% where that gate runs. The 82.8% oracle
is the lexical-subset ceiling, not the hybrid ceiling — the vector arm reaches
notes with zero term overlap, so exceeding it is possible and expected on the
paraphrase strata. The 78.1% figure recorded during step 3 (answers present in
the fused top-20) was a property of the terms-shaped candidate pool, not of
the corpus: the step-3.5 reach diagnosis found 22 of the 28 remaining misses
reachable once the dense arm is queried with the natural question.

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

- **2026-08-14 · step 4 (3-term subset fusion) measured: refuted on the
  overall floor, regression clause held.** `agentmd search -lex3` (and the
  matching unpublished MCP argument) widens `fusion`'s — and through it
  `hybrid`'s — lexical arm from 2-term to 2- and 3-term subsets of the
  query's extracted terms, still max-score across all of them; `false`
  reproduced `lexical-fusion` and `+question` byte-identically, row for row
  (0 differences across 84 rows each), before the new column was scored.
  Result: overall R@5 48/64 → **49/64 = 76.6%** against a required ≥51/64
  (net +3) — **net +1**, a floor miss. No stratum regressed by more than one
  question (pure-paraphrase and research-density each −1, inside tolerance),
  so the regression clause that worried the task text most did not trip;
  the floor clause did. Per-question: 3 gained (`dt07`, `dt10`, `rc03`), 2
  lost (`pp02`, `rd04`) to reciprocal-rank displacement — both were already
  at the edge of the window (rank 3, rank 5) under `+question`, and one new
  competitive candidate from the widened lexical arm was enough to push each
  out. The diagnosis's 7 candidates were re-derived against the branch build
  before trusting them and reproduced exactly (same ids, same ranks), but
  only 3 converted: the isolated-triple probe tests one subset's reachability
  in its own private search, while the shipped mechanism issues every 2- and
  3-term subset simultaneously in one max-score ranking, so a candidate's
  isolated rank-1 subset must still outscore whatever the *other* 34 subsets
  of a 6-term query surface. `dt02`'s own winning triple ranks its answer 1st
  in isolation (raw score 11.4) but loses the full competition to unrelated
  cache-snapshot documents another subset scores at 21.0+. Why not sweep a
  parameter (RRF depth, candidate cap) to try to close the gap: the plan's
  own ground rule forbids tuning against the gold set to rescue a refuted
  rule, and the shortfall is a floor miss, not evidence of a fixable defect —
  the mechanism worked as designed and simply did not clear its own bar.
  **Code kept, not reverted** — quarantined behind `-lex3` (CLI flag,
  unpublished MCP argument), which the hook does not request and the
  published tool schema does not expose, so nothing about production
  behavior changes; the same reasoning that kept step 3's refuted rerank
  code in the tree. Lexical-only latency for the widened arm: p50 26.4ms,
  p90 39.8ms (baseline, lex3 off: p50 18.1ms, p90 23.7ms) — far inside the
  hook's 300ms budget, so task 5 does not inherit a latency concern from
  this, only a recall one. **Generalizable finding, worth carrying past this
  task:** isolated single-configuration reachability is a weak proxy for a
  max-score-across-many-configurations mechanism's actual outcome, because
  every other configuration's candidates compete in the same final ranking —
  any future diagnosis of this shape should expect the same optimistic gap.
  **Re-audit trigger:** none — the ladder's remaining target is unaffected,
  since `+question` (75.0%) is still the highest column and the design's own
  2026-08-14 sizing already priced >90% as unreachable inside this design
  regardless of whether step 4 converted. Full investigation, including the
  isolated-vs-competitive rank table: `scripts/health/results/goldv2/
  NOTES.md` § "Task 4: three-term subset fusion — refuted".

- **2026-08-14 · step 4 is now 3-term subset fusion; temporal wiring is
  re-scoped as a non-regression change and moved after the cutover. The
  70–90% target band is reachable; >90% is not, inside this design.** The
  operator set a >90% recall goal, which forced an honest sizing of what
  remains after `+question`'s 75.0%. Three measurements decided it. **(1)
  Temporal is a filter, not a retrieval channel** — applied to a query that
  already succeeds it either changes nothing or deletes the answer, and can
  only add a hit where a temporally-wrong note outranks the right one, which
  is not why any remaining question misses. Measured: 14 currently-passing
  questions carry a date phrase and are at risk, against 5 date-phrase misses
  of which ~1 (`ep09`) is not otherwise reachable. Its rule (episodic ≥60%)
  was already satisfied at 75.0% before any code, so it could not
  discriminate — a rung that cannot fail is not a rung. **(2) 3-term subsets
  reach 7 of the 16 remaining misses**, all outside the five written off as
  vocabulary-bridging, and cover two of the three episodic misses without any
  temporal wiring. That is the largest addressable set left, so it takes
  position 4. **(3) The ceiling is now legible and the target must move.**
  Perfect 3-term conversion gives 55/64 = 85.9%; at task 3.5's observed 11/16
  fusion-conversion rate, realistically ~83%, plus temporal's unique `ep09`.
  The residue — `pp05`, `pp09`, `pp17` and the five written-off — is
  pure-paraphrase and vocabulary bridging, which this design explicitly
  scoped to the alias/filing arc. **So the stated 70–90% band stands as
  reachable and >90% does not belong to this design**; recording that here
  rather than letting the next rung chase it. Why not run temporal anyway
  since it is written: its expected value is negative against a stratum that
  just went 7/12 → 9/12, and deferring costs nothing because the
  `after:`/`before:` bounds already exist for callers that set them.
  **Re-audit trigger:** if the hook cutover shows real prompts carrying date
  phrases far more often than the gold set's standalone questions do, step
  5.5's upside is larger than measured here and its sizing should be redone
  against hook traffic rather than against the gold set.

- **2026-08-14 · step 3.5 (question passthrough) measured: 75.0% R@5, well
  past its own floor, zero stratum regressions.** `agentmd search -question`
  (and the matching unpublished MCP argument, on the same seam `mode`
  already uses) hands the daemon the natural question alongside the
  AND-reduced terms; the dense arm embeds `WrapQuery(question)`, defensively
  truncated to the embedder's window by reusing `ChunkText`'s own budget
  arithmetic (`windowBudget`, `daemon/internal/index/vector.go`) rather than
  inventing a second notion of the window. The lexical arms are untouched:
  `index.Query.Text` is set from the terms in every caller and never from the
  question, so `-mode and`/`fusion` are provably unaffected — re-scored with
  no `-question` flag, the branch build reproduced `+chunking`'s own
  per-question JSON bit-for-bit (same 84 rows, same hits, same ranked lists),
  not merely the same aggregate. Landed as the `+question` column in
  `scripts/health/results/goldv2/NOTES.md`. Result: overall R@5 36/64 →
  **48/64 = 75.0%** (rule: ≥40/64), pure-paraphrase 9/18 → **11/18 = 61.1%**
  (rule: ≥9/18), every stratum improved and none regressed (rule: none
  regress by more than one question) — 12 gained, 0 lost, published by
  question id. Of the diagnosis's 16 dense-top-5 candidates, **11
  converted**; the other 5 (`dt07`, `pp05`, `pp09`, `pp17`, `rc03`) were
  dense-top-5 by raw cosine but did not survive RRF fusion into the final
  top-5 — the fusion friction the rule's own +4 floor (deliberately below
  the diagnosis's 16) was sized to tolerate. One gain, `ep05`, converted
  through fusion synergy rather than the diagnosed mechanism: dense rank 19
  alone, inside RRF depth but not top-5 by itself, yet the lexical arm's own
  contribution lifted it into the fused top-5 — the same `ep05` step 3's
  cross-encoder recovered. `rc08`, the case both rerank candidates floored
  to empty in step 3's investigation, is also recovered here, by a mechanism
  with no cross-encoder in it at all. Why not raise the rule's floor now
  that the diagnosis under-promised relative to the result: the floor was
  fixed before scoring specifically so a strong result would not
  retroactively read as merely clearing a bar tuned to the outcome; the
  result stands on its own margin instead. **Re-audit trigger:** none
  fired — the diagnosis's own named trigger ("falls well short of the 16
  direct candidates") did not occur (11 of 16 converted, and the overall
  rule cleared at roughly 3x its required margin), so fusion friction (RRF
  depth, per-arm contribution) was not investigated and remains untouched,
  per the plan's explicit scope fence. Full per-question detail:
  `<vault>/Agent/_meta/health/goldv2/question-20260814.json`.

- **2026-08-14 · the 2026-08-13 entry's re-audit trigger fired: the query
  representation was the artifact, and correcting for it licenses step 3.5.**
  Operator-directed post-mortem of the step-3 refutation. What changed: the
  refutation *stands* but its recorded cause was incomplete. Four findings.
  (1) The test fed both cross-encoders `_daemon_query_terms`' reduced
  keywords, never the natural question — `cmdSearch` has one query string for
  all arms. Counterfactual on the same note and model: rd08's correct answer
  scores sigmoid 0.000 under the terms query and 0.959 under the question.
  Every floored-to-empty positive traces to this. (2) The artifact was also
  throttling the **dense arm**, which embedded the same keyword string:
  probing the 28 remaining misses with the question embedded instead puts the
  expected note in the dense top-5 outright for 16 of them (rd10: dense rank
  3,019 → 1) and inside RRF depth for 22 of 28 counting 3-term lexical
  subsets. (3) Two findings survive any format fix and keep the CE dead:
  positives and hard negatives interleave on CE score in either format
  (0.003–0.959 vs 0.267–0.906 — similarity is not answerhood on a corpus
  whose negatives are topic-saturated by design), and max-over-chunks hands
  long documents an extreme-value subsidy that displaced 1.4KB notes with
  39KB roadmaps even under natural questions. (4) The jina GGUF conversion is
  compromised (canonical pair 0.843 vs bge's 0.9996) — its step-3 numbers
  understate the model; immaterial to the verdict since healthy bge
  interleaves too. **Step 3.5 added to the ladder** (question passthrough:
  dense arm embeds the natural question, lexical arms keep the terms; rule in
  the table, pre-registered before any implementation). Why not re-run the CE
  with the fix instead: at a measured ~18–125 ms/pair it cannot clear the
  hook's 300ms budget in any configuration, and its rejection story is dead
  on the interleave — the ordering question it could still answer is worth at
  most a parked experiment. Why not add 3-term lexical subsets in the same
  rung: one mechanism per rung keeps the column attributable; triples (14/28
  reachable, heavily overlapping the dense gains) stay licensed and parked.
  **Re-audit trigger:** if step 3.5's implemented column falls well short of
  the diagnosis (16 direct dense-top-5 candidates), the gap is fusion
  friction — revisit RRF depth or per-arm contribution *off-gold* before
  concluding the diagnosis over-promised. Full mechanism record:
  `scripts/health/results/goldv2/NOTES.md` § "Task 3 post-mortem".

- **2026-08-13 · step 3's cross-encoder floor is refuted; the deliberate
  path's LLM rejection gate is promoted from optional to load-bearing.**
  Bake-off between `bge-reranker-v2-m3` and `jina-reranker-v2-base-
  multilingual`, both scored on the full 84-question gold set with a floor
  derived off-gold before either run. jina won both axes and ran roughly 7x
  cheaper per pair, and still reached only 39.1% R@5 against the ≥56.2%
  requirement and 40% rejection against ≥70% (bge: 32.8% / 10%). Three defect
  hypotheses (wrong candidate count, floor scale, head-only chunk blindness)
  were checked directly against the daemon's own JSON output and a targeted
  unit test before accepting the numbers — none held; see
  `scripts/health/results/goldv2/NOTES.md`'s task-3 section for the full
  investigation. The mechanism is not inert: both models recovered the `ep05`
  watchlist casualty, real evidence a topically-related wrong chunk can be
  outranked by the right note when the cross-encoder is confident. It mostly
  is not confident enough on this corpus — a general-purpose cross-encoder
  reads "topically adjacent, densely related internal engineering document"
  as relevant enough to survive an off-gold-calibrated floor, which is
  measurably the same failure this design already named for BM25 (*"a
  plausible question about a well-discussed topic outscores a question
  answered by one small note"*) recurring on the signal meant to be immune to
  it. Why not raise the floor to fix rejection: the dominant failure mode
  (21-31 of the misses, both models) is the cross-encoder outranking a wrong
  candidate above the right one, which survives any floor placement — the
  ranking itself disagrees with the gold labels, not merely the threshold.
  Why not ship the better-of-two anyway: the rule is a rule, not a
  leaderboard, and the ladder's own principle is that a rung failing its
  rule is not a rung. **Re-audit trigger:** a fine-tuned or larger
  cross-encoder, or a query representation richer than
  `_daemon_query_terms`'s reduced keywords — both out of this design's
  current scope, recorded as live hypotheses rather than ruled out.

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
