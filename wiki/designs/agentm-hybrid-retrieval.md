---
title: AgentM Hybrid Retrieval — the recall ladder
status: launched
kind: design
scope: architecture
area: agentm/memory-index
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
on the deliberate path is therefore **load-bearing**, and the fast path ships
with no rejection gate at all.

**Sharpened 2026-08-17 (see the amendment log): "similarity is not
answerhood" holds for ordering too, not only for thresholding.** A
reorder-only pass — no floor, cannot evict a candidate already in the
pool — was tested against the strictly weaker property a floor never
needed: within one question's own pool, does the cross-encoder rank the
labeled answer above its competitors. On the 9 reachable misses closest to
the top of that test, it does on 4 — short of the ≥5 the rule required.
The mechanism is bounded-safe (a permutation cannot make a reachable
question unreachable) but not accurate enough to buy its own
implementation. No reordering mechanism ships on the fast path as of this
amendment.

**What the fast path does instead, decided 2026-08-14: inject with metadata.**
The hook returns its top-k labelled — each hit's score, its space, and an
explicit statement that these are candidates matched by similarity rather than
verified answers — and passes through unchanged the honest empty the lexical
arm already produces on its own. It does not manufacture a rejection. This
inverts the original design above, and the inversion is the measurement's
doing: a floor placed without separation drops true answers at the same rate as
wrong ones, whereas the agent reading a labelled injection can make the
answerhood judgment the cross-encoder cannot — which is precisely the
capability the deliberate path's 0.725 already demonstrates. Rejection is the
LLM gate's job; the fast path's job is to be honest about what it is handing
over.

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

**Fixture version, as of 2026-08-17: `goldv3-20260817`, not `goldv2-20260812`.**
Steps 0–6 below are the historical record against v2 and are never rewritten —
each was measured against the fixture that stood at the time, and that
measurement is what it is. The goldv2 → goldv3 changeover itself shipped no
mechanism (`touches_architecture: false`): the corpus and gold set were
decontaminated and relabeled (four capture-contamination decoy notes purged,
six label defects fixed, three hook-policy-only questions annotated instead
of miscounted), not the daemon or the query paths. The live numbers today are
the v3 opening baseline, immediately below the table — not step 6's row,
which stays v2's own number, permanently. See
`scripts/health/results/goldv2/NOTES.md` § "goldv3 changeover (2026-08-17)"
for the full per-question accounting.

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
| 5 | `hook e2e` | p50/p90 <300ms warm through the *installed* hook; each stratum within one question of `+question` (75.0%); inject-with-metadata, no manufactured empty | **met, both clauses** — p50/p90 213.8ms/222.4ms end-to-end through the installed hook (n=84); every stratum within one question of `+question` (73.4% overall, 47/64). Honest-empty on the 20 negatives: 0/20 genuine. See NOTES.md for the per-question diff and the latency-cliff finding it also surfaced. |
| 5.5 | `+temporal` | *(re-scoped, moved after the cutover)* no stratum regresses at all against `hook e2e`; the 14 at-risk date-phrase questions enumerated with before/after ranks | **met** — 73.4% (47/64), byte-identical to `hook e2e` on all 84 rows. The extractor never fires on this gold set (0 questions match), so the "14 at-risk" estimate does not hold up — see the amendment log. Shipped wired. |
| 6 | `agent layer` | week-1 driver rerun, n≥6, ≥0.725 — non-regression | **refuted** — mean 0.6799 across 6 replicates (0.661, 0.683, 0.700, 0.679, 0.617, 0.740; only one clears the bar). Concentrated in negative rejection, 87.5% → 62.5% against the 2026-08-06 baseline; answerable-question recall through the tool is flat-to-improved (78.1% answerable-only, ahead of every retrieval-layer column). Does not implicate the hook, which is deterministic and was measured separately in step 5. |

Step 6 exists because the two layers have disagreed once already: the alias
backfill was slightly better at the tool level and 3.85 points worse at the
agent level. A retrieval-layer win is necessary, never sufficient.

**v3 opening baseline (`goldv3-20260817`, 2026-08-17) — the changeover, not a
ladder step.** `+question` 50/64 (78.1%, up from v2's 48/64); hook e2e 48/61
(78.7%, denominator now excludes the 3 `hook_reachable: false` questions
whose only answers live in hook-excluded subtrees, reported separately
rather than counted as generic misses). Both arms scored twice,
bit-identical. New integrity triple: 15,029 docmeta / 14,529 embedded notes
/ 17,407 chunk vectors (up from 9,971/9,473/11,761 — organic corpus growth
in the five days since v2, not curated). Contamination + label-defect
correction accounts for the full movement: 5 of 5 near-certain conversions
landed exactly as predicted (`dt07`, `pp09`, `pp10`, `ep08`, `pp16`, all
rank 1); 0 of 3 probabilistic conversions landed (`ep07`, `pp07`, `pp17`) —
two to competing notes already flagged as live risk, one (`pp07`) to a
defect the diagnosis had not found: a dream/consolidation dedup-proposal
note that quotes a purged decoy's full text verbatim and outlived the
purge. Step 6's agent-layer arithmetic (0.6799, the arc-close gate's
blended baseline) was computed against v2 and is now stale — any future
re-pricing of that gate re-derives against v3, per
`arc-close.PLAN.md`'s own dated note.

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

**Generated aliases are closed as a retrieval lever (2026-08-16).** Three
independently pre-registered strategies — content-only prompting, structural
prompting, and outcome-filtered generation — each returned the same null on the
same targets, and the third eliminated the first two's diagnosed failure mode by
construction: every alias it kept provably retrieved its own note, and the
gold-query rank still did not move. The `pp09` trace explains why and redraws
the problem: the corpus already ranks the right note first for the rare term
that distinguishes it, and the gold query loses it because fusion's two-term
subset carries the common words instead. **What remains of this residue is
query-formulation- and fusion-shaped, not vocabulary-shaped** — the framing this
design's own Alternatives section once argued the other way. A future rung
belongs on the ranking side; another write-side vocabulary rung does not, and
the burden on one is now to show what it would do differently from all three.

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

- **2026-08-17 · Floorless rerank refuted at its pre-flight probe —
  "similarity is not answerhood" now covers ordering, not only
  thresholding.** The goldv3 reach probe (previous entry) found 11 of 14
  `+question` misses already sitting in the k=50 pool, mostly at rank
  6–14 — the residue looked like ordering, not recall, and the natural next
  mechanism was a floorless reorder of the existing hybrid pool: no floor,
  no filter, a permutation truncated to k, structurally unable to evict a
  candidate already reachable. Before buying the implementation, the
  pre-registered rule required a cheap probe first: for the 9 misses whose
  labeled answer sits at k ≤ 20, score the labeled answer and its
  question's current top-5 occupants with the shipped cross-encoder (jina)
  and require the labeled answer to beat a majority of its own pool on ≥5
  of 9 questions. **Measured 4 of 9** — `pp05`/`pp06`/`ep04` a clean 5/5
  each, `dt10` a narrow 3/5, then five questions where the labeled answer
  loses to a majority of what already outranks it, `rc01` losing to all
  five and `pp07` losing to a dream/consolidation dedup-proposal note (the
  sixth FOLLOWUP the goldv3 changeover filed) by the single largest margin
  measured in either direction. The instrument was proven live first (two
  unrelated strings scored -3.1409 vs -3.7396; every question's scores
  show real spread), so the null is not a dead-server artifact. **The
  distinction this sharpens:** step 3's refutation measured *cross-question
  score comparability* (0.003–0.959 against 0.267–0.906) — whether one
  threshold works across questions, which a floor needs. This probe
  measured *within-question ordering* — whether the labeled answer beats
  its own pool, one question at a time — a strictly weaker property step 3
  never tested, and it fails too. No code ships; the rung closed at the
  probe, before an implementation task was ever written, so there is
  nothing to keep inert. Full record:
  `scripts/health/results/goldv3/NOTES.md` § "Floorless rerank — refuted
  at the pre-flight probe".

- **2026-08-17 · goldv3 changeover — decontaminate, relabel, re-baseline.**
  Immediately after the retrieval-competition arc closed with the deterministic
  number unmoved (previous entry), a fresh diagnosis of the 16 chronic misses
  (`_harness/goldv3-diagnosis.md`) found the residue split four ways, only one
  of them genuine. **The gold set had contaminated its own corpus**: the
  message that drafted the 84 questions was itself mined by the preference
  miner (an interrogative-as-directive bug) into four decoy notes, one of
  which — sharing the question's own rare tokens, typos included — outranked
  the real answer outright. **Six questions had label defects**: retrieval
  returned right-or-defensible answers the gold set scored wrong (a
  mislabeled note, an over-narrow accept-set, an ambiguous term, a truncated
  question). **Three questions were a hook-policy artifact**, not a retrieval
  miss: their only answers live in subtrees the hook excludes by design, and
  the ladder's denominator was counting them as generic misses instead of
  measuring the policy honestly. **A genuinely hard core of six remained
  correctly bounded** — precise paraphrase and role-register questions with
  correct labels, unchanged, the arc's actual finding. Purged the four decoys
  (git-recoverable, live-vault), authored `gold-set-v3.json` as a new file
  (v2 never edited), extended `retrieval_scorecard.py` with folder-prefix
  acceptance / hook-denominator exclusion / gate-only negative reporting (all
  backward-compatible no-ops on v2), cut a fresh corpus snapshot, and
  re-baselined: `+question` 48/64 → 50/64, hook e2e 47/64 → 48/61 (honest
  denominator). 5 of 5 near-certain label-fix conversions landed exactly as
  predicted; 0 of 3 probabilistic ones did — two to already-flagged competing
  notes, and one (`pp07`) to a defect the diagnosis had not found: a
  dream/consolidation dedup-proposal note that quotes a purged decoy's full
  text verbatim as a "supporting excerpt" and so outlived the purge, filed as
  a sixth system-defect FOLLOWUP. **Comparability with every v2 number is
  deliberately broken as of this changeover** — see the Measurement section's
  fixture-version note and `scripts/health/results/goldv2/NOTES.md` § "goldv3
  changeover (2026-08-17)" for the full per-question accounting. The
  arc-close gate's blended 0.6799 baseline (previous entries) was computed
  against v2 and is now stale arithmetic, not a stale conclusion — its own
  hold header carries a dated note; re-pricing it, if it happens, re-derives
  against v3.

- **2026-08-16 · Outcome-filtered alias generation (retrieval-competition arc,
  section 5) refuted at its pre-flight probe — and with it the whole arc closes,
  every section accounted for and the ladder's number unmoved.** The conditional
  final section kept a generated alias only if, with every candidate applied and
  indexed, the alias's own text retrieved its own note in the lexical top-5 — an
  *outcome* filter, deliberately not Doc2Query--'s relevance filter, which the
  SIGIR 2024 reproducibility study found harms recall-based metrics. It kept 451
  of 552 aliases and moved the gold-query lexical rank of all three reachable
  targets by exactly nothing (`pp05`, `pp09` ×2, `pp15`, all `>50 → >50`), the
  same null the two prior alias prompts produced. The null was proven live
  before it was believed: the aliases are in the scored corpus, absent from the
  pristine baseline, and retrieve their own note at rank 1. **Why this is not
  the earlier vocabulary diagnosis repeated:** `pp09`'s surviving alias carries
  `primos`, a corpus-rare term the gold query uses, and the corpus *already*
  ranks the right note first for that term alone — the gold query fails because
  fusion's two-term subset carries the common words (`kept`, `notes`) and
  dilutes the rare one. **The residue is query-formulation-shaped, not
  vocabulary-shaped**, converging with `pp07`'s independently-diagnosed fusion
  friction. Three independently pre-registered alias strategies have now
  returned the same null, the third having eliminated the first two's diagnosed
  failure mode by construction, so alias generation is closed as a lever on this
  residue. Also recorded: the reachable target set was **three** — `pp07`,
  `pp16`, `pp17`, `rc01`, `rd01` sit outside the fixed structural scope entirely,
  and the scope was deliberately not widened to reach them (the gold-informed
  back door). Code kept but inert (`alias_pilot.py filter` has no live caller);
  `call_model`'s inherited-cwd leak fixed on its own merits, the same leak the
  HyDE probe hit, which both prior alias pilots predate and were likely
  contaminated by. **Arc verdict: all five sections are now closed — 1, 2, 4 and
  5 refuted, 3 closed without a run — and the arc-close gate's release condition
  ("at least one rung that moves the deterministic retrieval-layer number") went
  unmet across every one.** The ladder's live numbers are unchanged from where
  the arc opened: 48/64 (75.0%) on `+question`, 47/64 (73.4%) on the hook arm.
  Re-audit trigger: a *ranking-side* mechanism (query formulation or fusion
  weighting), not another write-side vocabulary rung — the arc has now bounded
  that thread from three directions. See NOTES.md § "Outcome-filtered alias
  generation (section 5)".

- **2026-08-16 · HyDE probe (retrieval-competition arc, section 4) refuted
  on non-regression — the first rung in this arc whose positive prediction
  half is not zero, but collateral damage elsewhere still sinks it.**
  Generating a Haiku hypothetical document per question and embedding that
  instead of the bare question converted 4 of 7 `pure-paraphrase` misses
  (`pp05`, `pp09`, `pp10`, `pp15`) while regressing 9 questions across four
  other strata it was never aimed at — `+question` R@5 fell to 70.3% (from
  75.0%), and `research-corpus` alone dropped 2 questions, past the
  1-question stratum ceiling. Two instrument bugs were found and fixed
  before any scoring run, both worth naming for any future rung that
  shells out to `claude -p` for generation: the subprocess inherited the
  worktree's cwd and Claude Code auto-loaded this repo's own CLAUDE.md/
  AGENTS.md into what was meant to be a *blind* hypothetical generation
  (fixed with a neutral cwd; the user's global `~/.claude/CLAUDE.md`
  remains a smaller, documented residual leak risk for 4 of the 7 target
  questions, since closing it via `--bare` breaks authentication in this
  environment); and naming the mechanism "HyDE" in the system prompt let
  the model break character into meta-commentary on imperative-phrased
  queries, fixed by never naming the task. **The three misses that stayed
  misses were exactly the three heaviest leak-risk questions** — the
  mechanism's unleaked signal converted everything it plausibly could and
  nothing it couldn't, evidence the 4-of-7 conversion is genuine rather
  than contamination. Against the alias-oracle's own eight
  (`wiki/designs/agentm-rejection-and-vocabulary.md`'s rung 0), HyDE
  converts 4 of 8 in measurement, though nothing ships. Full measurement:
  `scripts/health/results/goldv2/NOTES.md`'s "HyDE probe" entry.
  **Re-audit trigger:** a future query-side-bridge rung that bounds its own
  blast radius — substituting the query text only when a cheap pre-check
  suggests the bare-question dense arm is already weak, rather than
  unconditionally for every query — is the next candidate worth testing
  against this same corpus, not a re-run of this exact unconditional form.
  **Four sections into the retrieval-competition arc** (section 3 closed
  without a run; sections 1, 2, and 4 all refuted on non-regression), the
  arc-close gate's release condition has gone unmet across every section
  run so far — see the gate's own hold entry below for the accumulating
  count.

- **2026-08-16 · embedder-swap probe (retrieval-competition arc, section 3)
  closed without a run — the comparison it proposed already happened, in this
  same design's own 2026-08-12 entry below.** The brief scoped section 3 as
  re-embedding with Qwen3-Embedding-0.6B to target `pure-paraphrase`, framing
  EmbeddingGemma-300M as "#2 among sub-1B models" and the comparison as
  untested. Neither holds up: this design's own 2026-08-12 bake-off entry
  already ran the identical comparison at full parity (same 2,048-token
  window, same scope, idle machine) and EmbeddingGemma won every stratum
  including `pure-paraphrase` itself (7/18 against Qwen's 5/18) — the recorded
  re-audit trigger ("different hardware, a llama.cpp fix, or a candidate that
  beats 35/64") has not fired. A web research pass added to this: Google's own
  EmbeddingGemma paper states it is #1, not #2, under 500M params on MTEB
  English v2, and its own task breakdown (STS 74.7 against Retrieval 51.2,
  plus a PTEB study finding it the most paraphrase-robust in its size class)
  runs opposite the assumption that it is comparatively weak on paraphrase.
  No Go code, no re-embed — a probe closing on its pre-flight reading rather
  than a measured result, which the brief itself priced as an acceptable
  section-3 outcome. Full writeup:
  `scripts/health/results/goldv2/NOTES.md`'s "Embedder-swap probe (section 3)
  — closed without a run" entry. The arc proceeds directly to section 4
  (HyDE) — a query-side bridge, the mechanism actually shaped for
  `pure-paraphrase`'s zero-lexical-overlap gap, rather than another embedding-
  quality lever.

- **2026-08-16 · Vector-PRF (retrieval-competition arc, section 2) refuted —
  the second competition-mechanism rung, on a different arm than the first,
  refuted for a different reason.** Pseudo-relevance feedback (Rocchio
  `q' = α·q + β·mean(top-k)`, published defaults, registered off a
  gold-blind probe set before any code) mixes the dense arm's own top-3
  result vectors back into the query and re-searches once. `+question` R@5
  fell to 70.3% (from 75.0%), hook to 67.2% (from 73.4%) — refuted on
  non-regression before its own conversion clause even mattered (neither of
  the two registered plausible conversions, `ep09`/`rc01`, converted).
  Traced by hand: PRF amplifies noise when the seed dense rank is already
  mediocre (rank 12, not top-5) by mixing the query toward whatever
  happened to occupy the top-3 — in one traced case, a cluster of unrelated
  boilerplate notes, collapsing the correct note's dense rank from 12 to
  2486. The mechanism's own pre-flight probe (100% clean top-3/top-5) could
  not have caught this: it was built entirely from easy, already-rank-1
  queries and never contained a mediocre seed to measure against. Reverted
  before any PR opened, matching the first competition rung's own
  precedent. **Three rungs running now share one signature** — a
  registered positive-prediction set that converts zero of its members
  (`path-signal`, `chunk-lexical`, and this one) — worth carrying forward
  as a pattern in how this arc derives "plausible conversion" targets, not
  three independent surprises. Full measurement:
  `scripts/health/results/goldv2/NOTES.md`'s "Off-gold probe set +
  Vector-PRF" entry. The off-gold probe set itself survives as a reusable
  artifact for the arc's remaining sections, its own easy-query limitation
  now documented rather than assumed.

- **2026-08-16 · the "residue is vocabulary-shaped" premise is corrected, and
  a first competition-mechanism rung (chunk-level lexical indexing) is
  refuted in its own right.** This design's own 2026-08-14 step-4 entry
  wrote the retrieval residue off as "pure-paraphrase and vocabulary
  bridging," scoped to the alias/filing arc, and implicitly priced further
  ranking mechanism out of this design's own scope on that basis. Three
  subsequent rungs — `alias-pilot`, `alias-pilot-structural`, `path-signal`
  (a new `retrieval-competition` arc, briefed after path-signal's finding) —
  each assumed a signal was missing and each found it present but
  **outranked** by a different document instead. The residue is a
  competition problem, not a vocabulary one; the correction is inline above,
  at the step-4 entry itself.

  The retrieval-competition arc's first rung tried the obvious fix — chunk
  the lexical arm to the same granularity the dense arm already chunks at,
  so a long document's whole-document term-frequency mass stops crowding out
  a short atomic note — and it is also refuted, for a mechanism-level reason
  rather than a missing-signal one. A pre-flight probe (measured before any
  Go code) rejected the literature-standard aggregation (MaxP) outright: on
  every reachable target, a long document's single densest chunk out-scored
  a short document's whole body, not from many noisy draws but because that
  one chunk simply contained the query terms more times, in less text.
  Rank-based aggregation (best-chunk rank fused by RRF, bounded contribution)
  was registered instead, and it did what it was measured to do for its two
  intended targets — and, corpus-wide, promoted a Windows/PowerShell
  administration note into an AgentM query's top-5 because "agent" (a
  delegation agent) and "right" (an access right) happened to co-occur
  densely in that note's own short, undiluted chunk. **Rank compression
  cannot distinguish a short document's density that reflects genuine
  relevance from one that reflects coincidental common-word co-occurrence.**
  R@5 −12.5 to −15.6 points on the two production arms; reverted before any
  PR opened, nothing reached the live binary. Why the pre-flight probe could
  not have caught this: it measured only the handful of candidates the
  mechanism was built to help, never the other ~60 already-correct
  questions — which is exactly why clause (a)'s non-regression check is the
  arc's primary clause, not clause (b)'s conversion count.

  **This design's own ladder does not yet contain a working competition
  mechanism.** The arc continues (an off-gold probe set, then Vector-PRF on
  the dense arm — a purely vector-space mechanism sharing no machinery with
  chunk-lexical's approach) rather than declaring the residue permanently
  out of reach; a design update naming a working mechanism, if one is found,
  belongs here when it lands. **Re-audit trigger:** a future rung that finds
  a way to weight chunk-level density by genuine topical relevance rather
  than raw term co-occurrence (a discriminating signal this rung had none
  of) would be the first candidate worth re-examining against this same
  corpus. Full derivation:
  `scripts/health/results/goldv2/NOTES.md` § "Chunk-lexical indexing —
  refuted", and `<vault>/Agent/desk/projects/agentm/_harness/progress.md`'s
  2026-08-16 "task 5" entry.

- **2026-08-14 · step 6 (agent-layer non-regression) measured: refuted — mean
  0.6799 against a required ≥0.725, concentrated in negative rejection rather
  than recall.** `week3_daemon_retest.py` (the week-1 driver harness, reused
  unmodified) pointed at a scratch `agentmd serve` bound to the frozen goldv2
  corpus, `claude -p --model opus`, 6-call budget, 6 replicates of all 84
  questions. Before scoring: `/status` confirmed the embedder warm and
  attached (`vectors: 9473, stale: 0`), and a live differential call on `pp02`
  — miss under `and` mode with the exact recorded query terms, hit at rank 3
  under `hybrid` with the question passed, reproducing `question-20260814.json`
  byte-for-byte — proved the dense arm reachable rather than assumed it, the
  same discipline the `-no-embedder` lesson (below) exists to enforce.

  **The numbers, both ways.** Blended (the rule's own denominator: 84
  questions, negatives scored 1.0/0.0 into the same average as answerable
  questions, exactly how the 2026-08-06 baseline computed 0.725): mean 0.6799,
  individual replicates 0.6607/0.6825/0.7004/0.6786/0.6171/0.7401 — five of
  six below the bar. Read the retrieval-ladder's own way (R@5 over the 64
  answerable questions, rejection over the 20 negatives separately): 78.1%
  answerable-only R@5 — ahead of every retrieval-layer column in this design,
  including `+question`'s 75.0% — against 62.5% negative rejection.
  **Answerable-question recall is not the problem.**

  **Compared directly against the actual 2026-08-06 run**
  (`scripts/health/results/week1/opus-arm-a.json`, read from disk rather than
  quoted from this doc's own §Why-now numbers): every answerable stratum moved
  by single digits either way (pure-paraphrase actually gained, 47.2% →
  52.8%, consistent with what a dense arm is for) — negative rejection is the
  one double-digit move, 87.5% → 62.5%, and it is the whole gap. Two things
  are both true: goldv2's negative stratum was deliberately grown from 8 to 20
  and hardened at exactly AgentKV's own request (§2 of the reciprocal
  handoff), so part of this is a harder population, not a pure regression;
  and the rule is measured against today's frozen corpus regardless, which is
  the same standard every other rung in this ladder was held to. The harder
  population explains the direction: it does not pass the gate.

  **The obvious mechanism does not hold.** This ladder has measured
  `fusion`/`hybrid` at ~0% retrieval-layer negative rejection since step 1, so
  the first hypothesis was that agent access to those modes poisons agent-layer
  rejection the same way. Instrumented directly (`week3_daemon_shim.py` now
  logs `mode`/`question_passed` per served call) rather than inferred: negatives
  where the agent used `fusion`/`hybrid` at least once rejected *better*
  (77.2%) than negatives where it stayed on the default `and` mode the whole
  time (14.3%) — the opposite of the hypothesis. Read cautiously (thoroughness
  and correct judgment plausibly share a common cause this data cannot
  separate), but it rules out the specific story this design would have told
  first. **Root cause of the negative-rejection drop is open, not resolved.**
  Across all 1,795 served calls, 79.9% never set `mode` at all — an agent with
  a published, documented escalation path mostly does not take it, which is
  itself worth carrying forward past this task.

  **What is not refuted, and what does not change.** The hook (step 5) is
  deterministic — it always calls `-mode hybrid -question`, with no agent
  discretion — and was already separately measured and passed; nothing here
  touches that measurement or that code path. Nothing is reverted: the
  refutation is about whether *today's* agent-layer performance clears a bar
  set eight days earlier from a smaller, easier, differently-composed gold
  set, not evidence that any shipped mechanism actively makes things worse.
  Full investigation, the per-stratum table against the actual
  historical JSON, and the mode-usage correlation:
  `scripts/health/results/goldv2/NOTES.md` § "Task 6: agent-layer
  non-regression — refuted". **Re-audit trigger:** any change to the
  `memory_search` tool description's escalation guidance, since the dominant
  finding here is usage rate, not retrieval quality, and a description change
  is the cheapest lever on usage rate that this investigation did not try.

- **2026-08-14 · the step-5 latency cliff root-caused: it is `snippet()`, not
  `rrfDepth`, and the fix costs no recall.** Step 5 flagged a 6-second cliff on
  3 of 84 gold questions and named hybrid's fixed fusion depth as the suspect,
  with a follow-up to investigate `searchFusion`'s `k`-scaling. That follow-up
  ran, and the suspected mechanism is refuted. The lexical arm's over-fetch
  window is `max(note.Overfetch, k)`, so for every `k` at or below 200 the
  ranking query is *identical* — measured flat at 22.7–24.4ms across
  `k` ∈ {10, 20, 30, 40, 50} on the frozen `goldv2-20260812` corpus, while total
  time went 26ms → 6,500ms over the same sweep. All of the growth is the snippet
  pass.

  What drives that pass is **how often the query's terms occur in a document,
  not how large the document is** — established on a within-document control
  rather than a correlation, and timed through the system `sqlite3` CLI so the
  instrument is not the implementation agreeing with itself. Ranking the full
  200-row window costs 3ms. Holding one 1,032,973-byte note fixed and changing
  only the query, `snippet()` costs 223ms under `"server" "model"` (7,647 × 296
  occurrences) and 10ms under `"collapse" "model"` (3 × 296) — same bytes, 22×
  spread. The falsifying case settles it: `dt11`'s returned window is *larger*
  in bytes than `rd03`'s and far cheaper — 29 rows / 13.2 MB / 1,165 occurrences
  at 242ms against `rd03`'s 50 rows / 11.9 MB / 100,110 occurrences at 6,446ms.
  A size-driven account predicts the wrong one. Size correlates only because
  large notes tend to contain common terms many times over.

  **What changed.** Ranking and snippeting are now separate steps, and the
  snippet pass runs once, over exactly the rows the caller receives.
  `searchAnd` and `searchFusion` each split into a ranking half (`andRanked`,
  `fusionRanked`) that returns the winning match expression per row, plus a thin
  wrapper that snippets what it is about to return. `searchHybrid` reads its
  lexical arm to `rrfDepth` for the ranks RRF needs, then fuses, truncates to
  `k`, and snippets those rows alone — where it previously received fifty
  already-snippeted rows and discarded all but `k` of them.

  Snippet eligibility is deliberately kept to the lexical arm's own returned
  rows. `fusionRanked`'s `wonBy` covers every candidate the subset sweep
  considered — 936 on `rd03` — so snippeting straight from it would newly
  highlight a row that matched lexically below the fusion window and was
  promoted into the result by the dense arm, which previously came back bare.
  That may well be the better answer, but it changes what gets injected into a
  prompt, and this is a latency fix; widening the coverage is its own change to
  propose and score. It is the one place where the obvious reading of "pass
  `wonBy` straight through" silently alters emitted content, so it is pinned by
  a test rather than left to the next reader's judgement.

  **Latency on the production call shape.** Measured as `recall._daemon_search`
  actually issues it — `-k 10 -mode hybrid -question <raw prompt>`, embedder
  live — because that subprocess is what the 250ms budget bounds. p50 87.6ms →
  77.9ms, p90 96.1ms → 86.3ms, max 6,322.8ms → **110.3ms**, and questions over
  budget **2 → 0** (`dt11` at 295ms and `rd03` at 6,323ms before; none after).
  An earlier pass of this work reported 1 → 0 from a `-no-embedder` run: that
  shape skips the dense arm and pays no embedding round-trip, and it understated
  `dt11`, which sits at 242ms without the round-trip and 295ms with it. A
  production claim has to be measured on the production path.

  **No column moves, measured rather than argued.** The reasoning that ranks are
  free is sound, but "a latency fix that costs no recall" is exactly the claim
  that should be measured, because that is the shape the error would take if the
  reasoning were quietly wrong. All four landed columns were re-scored on before
  and after binaries against the frozen corpus with the dense arm live — 84
  questions × 4 arms = 336 rows each — compared per question rather than in
  aggregate, and on returned paths, snippet text, scores and hit vectors rather
  than verdicts alone, since task 2.5 had stratum counts look flat while `rc08`
  and `ep05` silently swapped. **Zero rows differ in any field.** `and` 7/64,
  `fusion` 27/64, `+lex3` 32/64, `hybrid --question` 48/64, identical on both
  sides; those also reproduce the landed record independently, which is the
  check that the rebuilt index is the one those columns were scored against.
  Per-question tables are in NOTES.md rather than repeated here.

  **Why not lower `rrfDepth`.** Because the depth was never the cost. Fifty
  ranks are free — reading the arm deeper does not touch the over-fetch window,
  which is 200 either way — so trading measured recall for latency would have
  bought nothing that decoupling the snippet pass did not buy outright. The
  pre-registered rule that a change to `rrfDepth` or the over-fetch policy must
  state its expectation before measuring is untriggered here: neither was
  changed, and both keep the values step 2 chose.

  **A larger, older hazard is now on record, and is not fixed here.** The same
  mechanism ships on `main` today, predates this plan entirely, and is worse
  there, because it does not need a deep `k` to fire — it only needs one note in
  the top few rows carrying a query term thousands of times. Measured on `main`'s
  own binary against the same corpus, `agentmd search -k 5 "mcp servers"` costs
  **10.0 seconds** and `-k 50` costs **43.3 seconds**, because three of that
  query's top five are ~1 MB server lists in which "servers" occurs in the
  thousands. This is reachable in production: the MCP `memory_search` tool takes
  a caller-supplied `k` clamped to 50, and has no equivalent of the
  prompt-submit hook's 250ms subprocess budget to bound it. It is left unfixed
  deliberately — every remedy changes which text an agent reads (cap snippets by
  size or occurrence count, chunk large notes in the lexical index as step 3
  already does for the vector arm, or lower the MCP clamp), and that is a
  product call for the operator rather than a refactor. **Re-audit trigger:** a
  `memory_search` or CLI call that visibly stalls, or corpus growth that puts
  term-dense notes of this shape into ordinary top-5 results.

  **The invariant is now executable.** `Index.snippetedDocs` already existed and
  already documented this exact rule — "it is called for the k rows a caller
  reads and not for the 200-row over-fetch window" — and nothing asserted on it
  for the fusion or hybrid paths, which is how the regression reached a shipped
  column. `snippetcost_test.go` pins it per mode, and fails on the pre-fix code
  with `snippet() saw 50 documents for a k=5 search` for both hybrid branches
  while passing for `and` and `fusion`, which were always correct.
- **2026-08-14 · step 5.5 (temporal wiring) measured: rule met, byte-identical
  to `hook e2e`, and the extractor never fires on this gold set.**
  `_extract_temporal_bound` (`harness/skills/memory/scripts/recall.py`) is a
  deterministic, model-free regex-and-calendar extractor wired unconditionally
  into `_daemon_search`: on a confident match it adds `-after`/`-before` to
  the daemon call; on no match — the outcome for every one of the 84 goldv2
  questions — the call is unchanged. Verified two ways: calling the function
  directly against all 84 questions returns `None` for each one, and diffing
  a full `--via-hook` rescoring against `hook-e2e-20260814.json` row for row
  (hit, rank, top-5 path list, correct_rejection) finds 0 of 84 differ. No
  stratum regressed, none gained, and the pre-registered "14 at-risk" / "5
  upside" figures do not hold up: they came from an unpreserved keyword-style
  probe, and this task's own worked example — "when did I decide X" bounds
  nothing, "what did I decide last week" bounds something — is exactly the
  distinction that probe collapsed. Every episodic-temporal gold question,
  including `ep09` (the diagnosis's one named upside candidate), is the
  first shape: it asks FOR a date rather than supplying one to bound with.
  Stays wired rather than reverted — it clears the non-regression bar
  byte-identically, so there is no regression to protect against by
  disconnecting it, and it is real, tested infrastructure that will matter
  on casual production prompts this gold set (standalone written questions)
  cannot represent. Full derivation, the keyword-scan cross-check, and the
  ops trail in NOTES.md's own "Task 5.5" section.

- **2026-08-14 · step 5 (hook cutover) measured: rule met on both clauses.**
  The prompt-submit hook's daemon call switches from `-mode`'s implicit
  `and` default to `-mode hybrid -question <raw prompt>` — the `+question`
  arm. p50/p90 213.8ms/222.4ms end-to-end through the *installed* hook
  (n=84, real shell-wrapper invocations, bash+python startup included); every
  stratum within one question of `+question` (73.4% overall, 47/64, against
  75.0%). Honest-empty on the 20 negatives: 0/20 genuine (the daemon
  searched and found something every time — unchanged since `-mode fusion`'s
  own 0% since step 1, not a new trade this step makes). MCP `mode`/`question`
  published in the tool `inputSchema` (`and`/`fusion`/`hybrid`; `rerank`
  excluded — refuted, not wired to the MCP surface; `lex3` stays unpublished,
  refuted and default-off). Full per-question diff, the hygiene-filter
  explanation for the diff's 3 "losses" (none are real — see NOTES.md), and a
  latency-cliff finding in hybrid's own fixed `rrfDepth=50` (task 2,
  independent of this step) are in NOTES.md's own "Task 5" section rather
  than repeated here.

  **Two infrastructure gaps found and closed, both prerequisites the design
  did not anticipate needing.** First: a one-shot `agentmd search -mode
  hybrid` (what the hook issues) had no way to reach the resident `agentmd
  serve` daemon's own warm embedder — that child binds a kernel-assigned
  random port, opaque to any other process, so a bare hook invocation would
  have spawned and loaded a fresh model on every single prompt. Fixed by
  giving `serve` a fixed loopback port to spawn on (`embed.DefaultAttachPort`,
  8901 — matching the port this plan's own measurement runs have used
  throughout) and a matching default on the one-shot search path
  (`embedderAttachDefault`/`embedderSpawnPort`, `cmd/agentmd`); inert for
  every other caller (an explicit `--embedder-url`, `agentmd embed`'s own
  backfill) so no prior task's measurement or reproduction is affected.
  Second: `embed.Options.Port` existed and was documented but was never
  wired to the supervisor that actually spawns the child — `New` never
  copied it and `runOnce` always called `freePort`, a latent dead field
  caught while building the fix above, not something this step's own change
  introduced.

  **The operator's live vault had never been embedded.** All of tasks 2
  through 4's measurements ran against the frozen corpus snapshot or scratch
  indexes; nothing had ever backfilled the real, live vault. Cutting over
  without doing so would have shipped `-mode hybrid` against zero vectors —
  a cosmetic cutover, behaviorally identical to fusion-only.
  Backfilled: 12,301 notes, 0 failed, 5m45s warm, same scope as every other
  task (`Agent/memory`, `Agent/desk`, `Agent/external`). Separately, the
  live kernel config had no `daemon.embed_model` pinned, and both bake-off
  candidates' GGUFs are present on this machine from task 2/3's own toolchain
  state — `embed.Discover`'s deterministic tie-break sorts by model name
  ascending, and `"Qwen3-..."` sorts before `"embeddinggemma-..."`
  byte-wise, so an unpinned install would have silently resolved to the
  refuted, unstable candidate. Pinned explicitly
  (`daemon.embed_model: embeddinggemma-300M-Q8_0`) rather than left to
  chance.

  **Verification, not assertion.** `agentmd status` showed no `embedder`
  line at all before this task (the running daemon predated the entire
  ladder — binary dated before task 1); after rebuild+reinstall+kickstart,
  `embedder ok (warm) · embeddinggemma-300M-Q8_0 · 12301/12301 embedded`,
  and a live `agentmd search -mode hybrid` against the real vault returned
  real, relevant hits with `matched: 97` and no degrade note. The real
  installed hook script, invoked end-to-end with a live prompt, reported
  `engine: daemon, mode=hybrid` and the injection carried the new
  metadata (`score=... daemon-hybrid, space: ...`) and disclaimer line —
  the installed hook, not a facsimile of it.

  **Injection policy shipped as specified — inject with metadata, never a
  manufactured empty.** Each daemon-sourced hit's header now reports its
  effective mode (`daemon-hybrid`, or `daemon-lexical` when the dense arm
  had nothing to embed with mid-query — the daemon's own `note`, matched
  rather than inferred) and its space (`memory`/`desk`/`external`/… — the
  literal top-level directory, honest about hits outside the vector arm's
  own three-space scope). The whole injection carries one disclaimer
  sentence when the daemon answered: these are candidates matched by
  similarity, not verified answers. No threshold was added anywhere in this
  change; an honest empty from the lexical arm passes through exactly as it
  did before.

  `retrieval_scorecard.py` gained `--via-hook` (scores through
  `recall._daemon_search` — the hook's own code — instead of shelling to
  `agentmd search` directly, which is what "scored through the hook rather
  than the CLI" means) and `--via-hook-budget-ms` (a generous per-query
  override for the correctness pass alone, so the rare latency-cliff outlier
  is never confused with a genuine miss; the latency clause is still
  measured under the real, unmodified 250ms budget, separately, against the
  installed hook). `DEGRADED_MARKS` gained a third marker for a silently
  skipped `--via-hook` query, mirroring the existing child-not-serving
  refuse-to-publish gate.

  **A pre-existing test hermeticity gap, exposed rather than caused.** Three
  tests (`test_recall_trace.py`, `test_recall_stream_admission.py`) called
  the real `prompt_submit()` without mocking `subprocess.run`, relying on
  the daemon declining by accident — true only because the previously
  installed (pre-ladder) binary rejected the now-standard `-mode`/`-question`
  flags as unrecognized. A working rebuilt daemon no longer declines by
  accident, so these tests started answering from the real live vault
  instead of their own tempdir fixtures. Fixed by mocking `subprocess.run`
  to force the decline deliberately, restoring the hermeticity both files'
  own docstrings already claimed.

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
  scoped to the alias/filing arc. **[Corrected 2026-08-16 — see the
  retrieval-competition arc's amendment below: three targeted vocabulary
  rungs each found their signal present but outranked by a different
  document, not missing. The residue is a competition problem this design's
  own ladder does not yet solve, not a vocabulary gap; "vocabulary bridging"
  undersold what was actually missing.]** **So the stated 70–90% band stands
  as reachable and >90% does not belong to this design**; recording that here
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
