---
title: AgentM Rejection and Vocabulary
status: final
visibility: published
author: alexherrero
contributors: []
created: 2026-08-14
updated: 2026-08-14
last_major_revision: 2026-08-14
prd:
project:
kind: design
scope: architecture
area: agentm/memory-index
parent: agentm-hybrid-retrieval.md
children: [agentm-rejection-and-vocabulary/parts/alias-oracle.md, agentm-rejection-and-vocabulary/parts/rejection.md, agentm-rejection-and-vocabulary/parts/alias-pilot.md, agentm-rejection-and-vocabulary/parts/arc-close.md]
seeded: 2026-08-14
---

# AgentM Rejection and Vocabulary

## Context

### Objective

The hybrid-retrieval ladder took prompt-time recall from 10.9% to 75.0%, and
its final gate still failed: the interactive agent now rejects unanswerable
questions at 62.5% where the old baseline managed 87.5%. This design recovers
the ability to say "nothing answers this" on both surfaces where that judgment
can legally run, and bridges the vocabulary gap that keeps eight gold questions
unreachable by every mechanism the ladder built. The arc closes on one
agent-layer run clearing two bars at once: the historic 0.725 blended score,
and 90% answerable-only recall. Every rung carries its rule before its code,
and a rung that fails its rule closes as refuted, recorded rather than shipped.

### Background

The previous arc (`agentm-hybrid-retrieval.md`, launched, merged as `8feb58e`)
ended with a diagnosis rather than a victory lap. Its agent-layer gate refuted
at mean 0.6799 against ≥0.725, and the shortfall is entirely rejection:
answerable-only recall actually rose to 78.1%, ahead of every retrieval-layer
column, while negative rejection fell 25 points. All 120 negative trials
concluded — zero timeouts — and every one of the 45 failures was the same
failure, an agent confidently naming a note that does not answer the question.
A probe run before this design (`PROBE-haiku-answerhood-gate.md`, vault-side)
settled the sharpest open question for $45.59: an LLM answerhood judgment
*does* separate what the cross-encoder provably could not — 85.0% projected
rejection against a calibrated CE floor's 40% — but the gate as originally
specified *deletes* candidates, and deletion destroys 13.4% of true answers,
netting +0.008 blended. The judgment is worth building on; the deletion is not.

Two constraints shape everything here. First, the layering rule — the daemon
touches local models only, runner jobs shell out via `claude -p`, the
interactive session delegates nothing — splits rejection across two surfaces
with different legal levers. The interactive agent (the surface the 0.725 gate
measures) can only be helped by what the tool says to it: its description and
its result annotations. The deliberate path (briefs, digests, filing jobs) can
afford a real LLM gate, and that is where the probe-validated Haiku judgment
ships. Second, vocabulary work in this vault has a refuted precedent that this
design treats as its null hypothesis: the 2026-08-08 bulk alias backfill wrote
generated aliases into 1,930 notes, measurably *cost* 3.85 points of R@5
(p = 0.0411, exact permutation over six replicates per arm), and was reverted.
Any alias mechanism this design ships must demonstrate it is not that
mechanism wearing a new name.

The cost realities: the agent-layer gate costs ~$50.68 and ~3 hours per run,
so this arc budgets exactly one, at close. Everything before it is cheap on
purpose — retrieval-layer runs on the frozen corpus are deterministic and
effectively free, and LLM probes cost cents per call once `claude -p` is
stripped to `--system-prompt` + `--tools none` ($0.0048/call against the
default surface's $0.0296, measured). The prompt-submit hook itself is done:
75.0% through the installed hook at 213ms p50, deterministic and separately
gated. This design builds around it and leaves it as it stands.

## Design

### Overview

Two threads, five rungs, one paid gate.

**Thread A — rejection, on both legal surfaces.** The interactive surface gets
an elicitation fix: the tool's guidance currently coaches "conclude no-answer"
only when results come back *empty*, and all 45 recorded failures happened on
*non-empty* results. The probe showed the judgment itself is easy — Haiku,
handed the same candidates with explicit permission to say "none of these,"
out-rejects an Opus agent mid-loop — so the interactive fix is to put that
framing into the tool's own text, and it is measured by a cheap pre-registered
mini-gate before the arc's close run. The deliberate path gets the Haiku
answerhood gate the prior design named essential, built as a **labeller**:
verdicts attach to candidates, every note stays in the consumer's hands, and a
wrong verdict stays recoverable because the reader can still see what was
judged. This is the same call the fast path already made when it chose
inject-with-metadata over a manufactured empty.

**Thread B — vocabulary, bounded before built.** Eight questions miss on the
ladder's best retrieval-layer column, and this thread was built on the premise
that a vocabulary gap is why (`pure-paraphrase` is zero-overlap by labelling
rule, and these particular notes miss on the dense arm too). Three measured
rungs have since refuted that premise on the lexical arm: the distinguishing
terms are present in the index and outranked rather than absent, which makes
the residue a competition problem rather than a vocabulary one. The thread's own
construction is what established that, and the amendment log's `alias-pilot`,
`alias-pilot-structural` and `path-signal` entries are where it is measured.
Rung 0 is an oracle: hand-written ideal
aliases for exactly those eight notes, on a frozen-corpus copy, scored once.
If perfect aliases cannot convert them, no alias engine can, and the thread
re-scopes before anything is built. Only if the oracle licenses it does the
targeted filing pilot build — a batched, propose→confirm alias pass that is
constructed blind to the gold set and measured against the bulk backfill's
−3.85-point precedent as its explicit null.

The arc closes with one agent-layer run scoring both clauses. The close gate
exercises the interactive surface, so it measures the elicitation fix and the
alias work; the labeller is deliberate-path infrastructure and is validated by
deterministic offline replay instead, where its evidence is already strong.

### Infrastructure

Everything runs on infrastructure that already exists; this design adds one
Python module and edits text.

| component | platform | what this design changes |
|---|---|---|
| `memory_search` description + result `note` | `agentmd` (Go daemon, MCP surface) | elicitation text only — no search behavior, no schema semantics |
| answerhood labeller | harness Python, invoked by runner jobs via `claude -p` (Haiku 4.5, `--system-prompt`, `--tools none`, hooks disabled) | new module, deliberate path only |
| alias oracle + scorecard arms | `scripts/health/` against frozen-corpus copies | measurement only, nothing ships from it |
| alias filing pilot | runner job (Sonnet 5, batched), propose→confirm, behind the corpus-write gate | targeted vault frontmatter edits |
| arc-close gate | `week3_daemon_retest.py`, unmodified, n=6 | one run at close |

When things run: the description text is read on every `memory_search` call by
whatever agent holds the tool. The labeller runs only inside unattended
consumers (briefs, digests, filing) — never on the interactive path, never in
the hook. The oracle and scorecard arms run manually during rungs. The filing
pilot runs as proposed batches an operator confirms.

Guarantees preserved: the hook path stays byte-identical (its 213ms p50 and
75.0% column stand as measured); the daemon keeps its pure-Go build and its
local-models-only rule; every candidate survives to the consumer that asked
for it; per-question detail stays vault-side.

### Detailed Design

#### 1. Alias oracle — rung 0, the ceiling before the mechanism

Copy the frozen `goldv2-20260812` corpus. Hand-write ideal `aliases:`
frontmatter — question-vocabulary phrasings — for exactly the eight notes
behind the `+question` arm's vocabulary misses (`pp05`, `pp07`, `pp09`, `pp15`,
`pp16`, `pp17`, `rc01`, `rd01`; all eight verified admitted by recall's hygiene
filter, which places the gap in vocabulary). Rebuild a scratch index and assert integrity the
arc's own way: 9,971 docs, and a file-level diff against the archived snapshot
showing exactly the eight edited notes differ. Re-score the `+question` arm
and the hook-shaped path.

**Rule (pre-registered):** at least 6 of the 8 convert to top-5 hits; no
currently-passing question is lost (per-question diff published by id — added
alias text changes both BM25 statistics and dense vectors, and reciprocal-rank
displacement has bitten this project three times); no stratum regresses by
more than one question.

**The gold-blindness boundary.** The oracle is diagnostic and gold-informed by construction — the same license as
the candidacy analysis and the k=20 reachability count, both accepted
precedents. Nothing oracle-derived ships. The mechanism in §4 must be
constructed blind to the gold set: its inputs are the note and its project
context, never a gold question or anything shaped by one. An alias engine that
reads the answer sheet is disqualified regardless of its score.

If the oracle refutes (<6/8), the thread re-scopes: the misses are then not
vocabulary-shaped after all (candidates: multi-path expectations, chunk
position, something unmeasured), §4 closes unbuilt, and the recall clause of
the close gate is re-priced before any money is spent.

#### 2. Interactive elicitation — the surface that actually failed

The daemon's result `note` coaches "answer
'nothing found' only after distinct vocabularies have failed" — on **empty**
results. Every one of the 45 failures was **non-empty**. 79.9% of 1,795 served
calls never set `mode`; the two negatives missed in all six replicates (`ng14`,
`ng17`) never escalated once — the first `and`-mode search returned something
plausible and the agent never pressed. And the probe's framing result: Haiku
with explicit permission to conclude "none" out-rejects Opus-in-a-loop on
identical candidates. The failure is elicitation; the judgment is already
within reach of the agent holding the tool.

What changes: the `memory_search` tool description and the non-empty result
annotation. The text adds the answerhood check the probe validated — before
naming a note, verify it answers rather than relates; a related note is not an
answer; concluding "nothing answers this" is a correct and expected outcome —
plus escalation guidance tied to it. Exact wording is a build-time artifact,
frozen before the mini-gate runs. This deliberately exercises the prior
design's own re-audit trigger ("any change to the `memory_search` tool
description's escalation guidance"), which was named because a description
change is the cheapest lever the last investigation did not try.

**Rule (pre-registered):** a mini-gate of the 20 negatives plus a fixed
15-question answerable canary sample (stratified, chosen before any run),
n=6 replicates, ~$21. Mean rejection ≥80% — against a 62.5% baseline whose
replicate spread was 55–75%, this sits roughly 2.5σ up — AND the canary
answerable mean within one question of its own baseline slice. Both clauses,
with the arm difference checked by exact permutation test, or the rung closes
refuted. `rc02` — always-missed at the agent layer yet hook-reachable — is
this rung's watchlist case.

#### 3. Deliberate-path answerhood labeller — the probe, promoted to spec

One Haiku call per search: input is the **natural question** (never the
reduced query — the probe measured 8.9% versus 86.7% failure-fix on the same
instrument, and a gate that sees only the tool-boundary query is in the first
row) plus the candidate set, excerpted exactly the way the probe's corrected
instrument does — IDF-weighted head + best-middle + tail, notes under ~3.5KB
shown whole. The thin-excerpt version of this instrument mislabelled 43.2% of
its apparent over-rejections, so the excerpting belongs in the spec. Transport is `claude -p --model claude-haiku-4-5
--system-prompt … --tools none` with hooks disabled: $0.0048/call, ~4s,
acceptable everywhere latency is free and nowhere else.

Output is a **verdict per candidate, attached as a label** — the consumer
renders "no candidate appears to answer this" or demotes labelled-drop rows,
but every note remains present and readable. Deletion is the alternative the
probe priced and this design declines: 13.4% of true answers destroyed,
unrecoverably, for +0.008 net.

The prompt must handle the class the probe found structurally mishandled:
derived answers. Episodic-temporal questions ("how long since my last blog
post?") are answered by *computing over* a note — the reader derives the answer
from what the note records — and strict answerhood preserved only 58.7% of them. The prompt asks
"does this note answer the question, or contain what a reader needs to derive
the answer" — iterated offline on the episodic slice for cents, frozen before
the scoring run.

**Rule (pre-registered, scored by deterministic offline replay on the frozen
corpus, corrected instrument, all six replicates' served candidates):** ≥80%
of negative trials have every candidate labelled drop (current evidence:
82.5% on the two fully re-checked replicates, pre-refinement — the refinement
loosens the gate toward derived answers and may push this down, which is why
the clause exists); ≥90% of answerable trials keep the expected note (current:
86.6%); episodic-temporal slice ≥80% preserved (current: 58.7% — this clause
is where the refinement either works or the rung refutes). Failure honesty:
a consumer whose labeller call fails renders unlabelled output with a visible
degrade marker, mirroring the embedder's own degrade contract.

#### 4. Targeted alias filing pilot — conditional on §1, measured against a refuted precedent

Builds only if the oracle licenses it. A batched Sonnet 5 pass over a
**bounded, targeted** scope — the spaces where vocabulary misses concentrate
(project `_index` files, `external/`, decision summaries; ≤300 notes for the
pilot, never bulk) — proposing question-vocabulary aliases from the note and
its project context alone, blind to the gold set per §1's boundary.
Propose→confirm, behind the corpus-write gate, landing as ordinary markdown
frontmatter the daemon's committer already handles and git already reverts —
the bulk backfill's clean revert exercised exactly this rollback path at 1,930
notes.

**Rule (pre-registered):** apply the pilot mechanism to a frozen-corpus copy,
rebuild, score. It must convert ≥3 of the eight oracle-validated targets, lose
zero currently-passing questions net (per-question diff by id), and leave the
20 negatives' behavior unchanged. The explicit null hypothesis is the 2026-08
backfill: a mechanism of this family, run bulk and unmeasured, cost 3.85
points and was reverted. A pilot that cannot beat "do nothing" on the same
scorecard closes refuted, and the alias story returns to capture-time practice
only.

#### 5. Arc-close gate — one run, two clauses, no averaging games

`week3_daemon_retest.py` unmodified, n=6 replicates, frozen corpus carrying
every shipped rung (the elicitation text and any confirmed pilot aliases; the
labeller is deliberate-path and does not participate — the gate drives the
interactive MCP surface, and saying so is part of the rule). ~$50.68, ~3h,
budgeted once.

**Clause 1 — non-regression:** blended mean ≥0.725, computed by the same
unmodified aggregation code as the historic baseline (fractional `r_at_5`,
negatives folded at 1.0/0.0).

**Clause 2 — the goal:** answerable-only binary R@5 mean ≥90% across the same
six replicates, negatives reported separately per the ladder's own reading.

Both clauses are reported independently with per-question diffs by id — the
flat-aggregate lesson is two arcs old now. The arc closes only if both hold; a
run clearing one clause closes that clause's story and records the other as
open, without relaxing either. Clause 2 is priced honestly as the ambitious
one: converting the four always-missed answerable questions alone reaches
~84%, and the rest rides consistency gains that are not individually
pre-measured.

## Alternatives Considered

**The deleting keep/drop gate, as originally specified.** Probe-priced on the
real failing replicates: +0.008 blended, with 13.4% of true answers destroyed
unrecoverably and the damage concentrated in derived-answer questions as a
category error no threshold fixes. The labeller captures the same judgment
recoverably. Rejected on measurement, not taste.

**Reviving the cross-encoder floor.** Refuted in task 3 and the refutation is
structural — positives and hard negatives interleave on similarity score in
either query format. Nothing in this design changes what a similarity score
is. Stays dead.

**Bulk alias backfill.** Run once at 1,930 notes, measured at −3.85 R@5
(p = 0.0411), reverted. This design's alias work exists in explicit contrast:
oracle-bounded first, targeted scope, propose→confirm, scored per rung on the
frozen corpus.

**More retrieval-ranking mechanism (RRF depth, per-arm weights, a larger
embedder).** The prior design's own sizing priced >90% out of retrieval-side
reach on this hardware, and the residue is vocabulary-shaped by construction.
Also the third-time-proven displacement risk: every added candidate source has
cost edge-of-window hits each time it was tried.

**A Haiku gate on the interactive path.** Violates the layering rule (the
interactive session delegates nothing) and would put seconds of subprocess on
a surface whose agent is already capable of the judgment when asked — the
elicitation rung is that ask.

**Accepting 62.5% as the harder population's honest level.** The negative
stratum genuinely was hardened 8→20 at AgentKV's request, and the confound
explains the drop's direction. It does not pass the gate, and the same frozen
standard held every other rung. The bar stands.

## Dependencies

The frozen `goldv2-20260812` corpus and `gold-set-v2.json` (archived,
restorable, integrity-assertable); the week-3 retest harness and shim, already
instrumented with per-call `mode` / `question_passed` / `result_paths` — the
instrumentation this design's probe replayed; `claude -p` with
`--system-prompt` and `--tools none` (the cost lever, measured); Haiku 4.5 and
Sonnet 5 via the operator's existing plan; the corpus-write gate and the
daemon's markdown-only committer for any live vault writes; the crickets
development-lifecycle plugin for `/work` execution of the plans this design
translates into. Explicitly **not** depended on: the heat/reflect
usage-confirmation signal — the full alias accretion loop stays deferred
behind it, and this design's pilot does not pretend that signal exists.

## Migrations

No schema changes, no index-format changes. Alias edits are ordinary markdown
frontmatter on existing notes, behind propose→confirm, committed by the
daemon's existing markdown path and revertible by git — a rollback exercised
at full scale once already. Measurement arms touch frozen-corpus copies only.
The elicitation text ships inside the daemon binary: the rung includes
rebuild, reinstall, and launchd kickstart, because launchd runs the installed
binary and a cutover that skips the reinstall measures the old path — the
prior arc's task-5 lesson, inherited as a step rather than relearned.

## Technical Debt & Risks

The gold-blindness boundary rests on review discipline — a carelessly
prompted filing pass could leak question shapes into alias generation, and the
only defense is that §1's boundary is checked at rung review. The elicitation
fix is prose aimed at a model: it can decay when the driver model changes, so
its re-audit trigger is any driver-model version change, consistent with the
harness's own re-audit principle. The mini-gate's n=6 over 20 negatives
carries real variance (the baseline's own replicates spread 55–75%), which is
why its bar sits ~2.5σ above baseline and its comparison runs as a permutation
test rather than an eyeball. The labeller puts note content into a Haiku
prompt, so a note can in principle argue about its own verdict — contained by
`--tools none`, by verdicts being inert labels, and by consumers treating
labels as advisory, which the recoverability rationale requires of them
anyway. The derived-answer refinement is a hypothesis: episodic preservation
at 58.7% may resist prompt iteration, and its clause is written so that
failure refutes the rung rather than shading it. Clause 2 of the close gate is
ambitious by construction and may fail while clause 1 passes; the design's own
close-out rules record that outcome honestly rather than blending it away.

## Quality Attributes

### Security

The labeller feeds corpus note excerpts to a Haiku call. That call runs with
`--tools none` and a replaced system prompt: it can emit JSON and nothing
else — no tool execution, no file access, no network beyond the API itself.
A hostile or merely odd note can at worst flip its own label, and labels are
advisory annotations on candidates the consumer still sees. No new
dependencies, no new supply-chain surface.

### Reliability

Every degrade is visible. A failed labeller call renders unlabelled output
with an explicit marker (the embedder's degrade contract, applied to a new
child). The elicitation change is static text with no failure mode beyond
being wrong, which the mini-gate exists to catch. The daemon goes on searching
exactly as it does today.

### Data Integrity

Measurement arms run on frozen-corpus copies with pre-registered integrity
assertions (doc counts, file-level diffs against the archived snapshot). Live
alias writes are propose→confirm behind the corpus-write gate, markdown-only,
git-revertible, with the rollback path already exercised at scale.

### Privacy

Labeller excerpts travel to the API — the same exposure class as every
existing `claude -p` runner job, adding no new category. All per-question
detail, probe artifacts, and replicate scorecards stay vault-side
(`<vault>/Agent/_meta/health/goldv2/`), never in the public repo, per the
standing rule.

### Latency

The hook path keeps its 213ms p50. The labeller
adds ~4 seconds per gated search on paths where latency is free by
definition; it is banned from the interactive path and the hook by the
layering rule. The elicitation text adds tens of
tokens to a tool schema — negligible.

### Testability

Every rung's rule precedes its code. The labeller is validated by
deterministic offline replay against recorded candidates; the elicitation fix
by a pre-registered mini-gate with n=6 and a permutation test; the alias work
by deterministic scorecard runs on frozen copies with per-question diffs. The
probe harness that produced this design's evidence is promoted into the repo
as the replay instrument.

## Project management

### Work estimates

Alias oracle S; elicitation rung S–M (text plus mini-gate); labeller M
(module, prompt refinement, replay validation); filing pilot M (conditional);
arc-close gate S (one orchestrated run plus close-out). The threads
interleave: rung 0 and the elicitation rung have no ordering dependency.

### Documentation Plan

This page; a `_Sidebar.md` entry nested under Rescope, which is where the whole
Rescope family lives — none of its members appear in `Designs.md`'s flat table,
and this design follows the family rather than making itself the exception;
`agentm-hybrid-retrieval.md` gains an amendment-log pointer marking its open
rejection question as owned here; `scripts/health/results/goldv2/NOTES.md`
gains one section per measured rung, as always; a how-to note for brief
authors when the labeller ships. Per-question detail stays vault-side.

### Launch Plans

Rungs land individually behind their own rules, in whatever order their
dependencies allow. The design transitions to launched when the arc-close
gate's both clauses pass and `/release` archives the final plan.

## Operations

### Monitoring and Alerting

Labeller verdicts are self-evidencing in brief output (the label is the
monitoring). Scorecard columns land in NOTES.md per rung; agent-layer
replicate JSONs land vault-side. `agentmd status` reports the same surfaces it
does today, since this design adds no resident component for it to describe.

### Logging Plan

The labeller logs per-call records (question hash, per-candidate verdicts,
latency, cost) to the vault-side health directory, same retention and
placement as the existing scorecard detail.

### Rollback Strategy

Elicitation text: one revert commit plus rebuild/reinstall/kickstart. The
labeller: a runner-job flag; off means unlabelled-with-marker, which is
exactly today's behavior. Aliases: git revert of markdown commits, exercised
at 1,930-note scale once already. Nothing in this design is unrecoverable.

## Document History

| Date | Change | Status |
|---|---|---|
| 2026-08-14 | Initial draft created via `/design author`, from the next-arc brief, the hybrid-retrieval close-out, and the Haiku answerhood probe. Four operator calls locked at authoring: both rejection surfaces with matched levers (labeller, never deleter); two-clause close gate (blended ≥0.725 and answerable-only ≥90%); alias oracle as rung 0 inside the arc; one published design. Author signaled ready for review, then ran the two-step cross-model prose pass: Gemini simplified against the operator's voice pack inlined verbatim, and its findings were applied here selectively — the banned term and eight contrastive tags were real and fixed, while its own rewrite was declined for introducing corporate register, for reaching for a word on the banned-vocabulary canon, and for flattening the Alternatives reasoning into filler. `check-slop` clean apart from one antithesis inside the exempt Alternatives section. Operator read the design and approved it as final the same day; publication pre-flight found no internal-work references beyond the one already-published peer-system name, no PII, and no absolute paths. Translated to 4 parts via `/design translate`: alias-oracle, rejection, alias-pilot, arc-close — §2 and §3 grouped as one rejection part since they share a story and neither blocks the other, and the oracle kept separate from the pilot it gates. Sequenced into 4 plans via `/design sequence`; `alias-oracle` active at `_harness/PLAN.md`, the other three queued at `_harness/designs/agentm-rejection-and-vocabulary/queued-plans/`. | final |
| 2026-08-14 | **Rung 0 (§1, alias oracle) closes with its rule met, and §4 is licensed to build.** Measured on a fresh copy of the frozen corpus that reproduced both landed columns row for row (0 of 84 differing) before a single alias was written: 7 of 8 targets convert on the `+question` arm (75.0% → 85.9% R@5) and 8 of 8 on the hook-shaped arm (73.4% → 84.4%), with zero questions lost, no stratum regressing, and one harmless rank move (`pp13` 3 → 4). The one non-conversion, `pp07`, was diagnosed rather than counted: its alias made the note **rank 1 on the lexical arm**, from outside the lexical top-50, and RRF with the dense arm put it 7th — fusion friction, not a vocabulary gap. Measured for all eight rather than inferred from the one: every target goes from outside the lexical top-50 to the top two (seven at rank 1, `pp17` at 2), so the vocabulary bound is effectively 8 of 8 and §4's ceiling is set by fusion rather than by alias quality. §1's refutation branch does not fire; the recall clause of the §5 close gate is not re-priced. Nothing shipped: the authored aliases are gold-informed by construction and stay in the scratch copy and the vault-side detail, per this design's own gold-blindness boundary, which §4 still inherits in full. **Also corrected here:** the Documentation Plan claimed a `Designs.md` entry this design does not have and should not have — the whole Rescope family is sidebar-only, and adding rows for this design and its parent alone would have left four siblings absent, so the text now names the family's actual convention rather than the flat-table one. Re-audit triggers: a change to RRF depth or per-arm contribution (which would move `pp07`'s bound), and any §4 result that reaches this ceiling, since a mechanism matching a gold-informed oracle is a signal to check the mechanism for gold leakage. **One factual correction the rung surfaced,** reconciled in the body above: the Overview and §1 called the eight "unreachable," and seven of them are, but `pp05` lands at rank 5 on the shipped `hook e2e` column. The set was drawn from the `+question` arm's misses, which is the accurate framing and is now what both places say. It does not change the rung, the rule, or the result — `pp05` was scored on both arms and converts on both. Full narrative and the measured table: `scripts/health/results/goldv2/NOTES.md` § "Alias oracle"; per-question detail at `<vault>/Agent/_meta/health/goldv2/alias-oracle-20260814.json`. | final |
| 2026-08-14 | **§2's elicitation rung closes REFUTED, and its wording is reverted rather than shipped.** Measured n=6 against the six recorded baseline replicates: negative rejection 62.5% → **69.2%** against the ≥80% bar, canary **12.00/15** against 13.17 (−1.17, outside the ±1 tolerance). Exact permutation over 924 assignments, two-sided: p = 0.0823 and p = 0.1450 — neither difference separates from noise at n=6. Reverted in `f268706` (reverting `1d8b9de`), live binary restored; a tool description has no flag to quarantine behind, so a revert is what this arc's "refuted rungs do not ship" rule means here. **Three findings the mean would have hidden.** First, the +6.7 points is two questions: `ng03` and `ng16` supply seven of the +8 net rejections across 120 trials, 14 of 20 negatives are unchanged, and `ng06` loses two. Second, `ng07`/`ng14`/`ng17` were rejected 0/6 at baseline and 0/6 here, so **the residual rejection failure is not an elicitation problem** — the surface §2 names is not where those three fail. Third, the canary cost is mostly *not* over-rejection (3 trials of 90) but the agent naming different wrong notes, so clause (b) failed for a reason clause (b) was not written to detect. Rejection variance did collapse (55–75% spread → 65–70%), recorded as a true property rather than a rescue. **A prior recorded before the run, and borne out:** the driver's system prompt already grants permission to answer "no answer found", so §2's text was always additive to an existing permission rather than filling a silence; what it added was the answerhood *test*, and that distinction stated in a tool description does not by itself convert an agent that has already decided to answer. **Consequence for §5:** the close gate exercises the interactive surface, which is now unchanged from the refuted baseline, so its blended ≥0.725 clause rests on Thread B's alias work alone and should be re-priced before the run is bought. §3's labeller is unaffected — different surface, different lever, and its probe evidence is untouched. Re-audit triggers: a driver-model version change (the design already names elicitation text as decay-prone), and any future attempt at this rung, which should target `ng07`/`ng14`/`ng17` directly rather than the stratum mean. Cost $24.41. Detail: `<vault>/Agent/_meta/health/goldv2/minigate-result-20260814.json`; narrative in NOTES.md § "Elicitation mini-gate". | final |
| 2026-08-15 | **§3's labeller is built and closes REFUTED on two clauses of three, with the mechanism demonstrated and one stratum carrying the whole failure.** Deterministic offline replay, uniform instrument: clause (a) negative rejection **82.5%** (≥80%, MET), clause (b) answerable preserved **84.1%** (≥90%, FAILED), clause (c) episodic **54.0%** (≥80%, FAILED). Ships behind the runner-job flag this design already specifies, defaulting off. **The finding that outlives the verdict: this design's own probe figures split by whether they were measured uniformly.** Negative rejection reproduces to the decimal (82.5% → 82.5%), while both answerable figures come in low (86.6% → 84.1%, 58.7% → 54.0%) — exactly as predicted before the runs, because the probe's "corrected" answerable numbers are 240 thin-excerpt survivors plus 32 corrected-excerpt rescues (272/314), an upper bound its own honest accounting names. **Clauses (b) and (c) were therefore priced against a number that was never a baseline**, which is a plan defect rather than a labeller defect and must be corrected before any successor rung is bought. **Per stratum the labeller fails in one place, not generally**: research-density 100%, research-corpus 98.1%, pure-paraphrase 88.3%, distinctive-token 83.1%, episodic-temporal 54.0% — and excluding episodic, answerable preservation is 91.6%, above clause (b)'s own bar. Three prompt-level attacks on the episodic gap were measured and all three failed to move the cases they targeted, including one whose per-question prediction was registered in advance and refuted 1-of-5 despite the rate improving 44.4% → 54.0% (kept as an unexplained gain, not credited to the mechanism). Two results worth carrying forward: `ng14` went 0/6 at the agent layer and 0/6 under elicitation to **6/6** here — a negative no interactive lever ever reached, converted completely, which is this design's two-surface thesis as a measurement rather than an argument; and `ng11` regressed 4/6 → 0/6, a real per-question loss inside a +20-point aggregate. Half the answerable loss (21 of 50 trials) is the labeller naming a *different* note rather than rejecting, which a non-deleting labeller makes recoverable and which should not be priced like the 29 genuine silences. Re-audit triggers: any successor rung must re-set (b)/(c) against these uniform numbers first, and should ask whether an 80% bar belongs on the one stratum where derivation rather than answerhood is the real question. Cost $35.68 across three measuring runs, at $0.047/call against the probe's $0.0048. Detail: `<vault>/Agent/_meta/health/goldv2/labeller-replay-{negatives,answerable}-20260815.json`; narrative in NOTES.md § "Answerhood labeller". | final |
| 2026-08-16 | **§4's targeted alias filing pilot is built and closes REFUTED on clause (a): a clean null, not the backfill repeated.** `scripts/alias_pilot.py` reuses `alias_backfill.py`'s generation/cleaning/write primitives verbatim (same prompt, same cleaning, same frontmatter mechanics as the reverted 2026-08 bulk backfill) over a fixed ≤300-note scope described entirely by structural patterns (every `_index.md`, everything under `external/`, every `PLAN.archive.*.md`), propose→confirm, gold-blindness made mechanical by `test_alias_pilot.py`'s `GoldBlindnessTests` rather than merely asserted. Scored against a fresh `goldv2-20260812` copy, baseline reproduced row-for-row (0/84 differing) before any alias was written: 120 notes in scope on the frozen corpus (290 was the live-vault count task 1 fixed the scope against four days later — the frozen corpus's own count, 120, is what this run correctly scores), all 120 aliased, applied to the scratch copy only (`--allow-ungated`, a new named opt-out for a bare-extracted tarball with no git history — the gate governs live writes, not scratch measurement), manifest-verified as exactly 120 files changed, each touching only the `aliases:` field. **Clause (a) failed: 0 of 8 (0 of 3 in-scope) targets converted, on both arms, byte-identical stratum totals throughout.** Clauses (b) and (c) held cleanly — net +0, zero negatives changed — so R@5 is unchanged at 75.0%/73.4% before and after: a clean null, not the −3.85-point backfill's damage repeated. **The diagnosis, not just the count:** the same lexical-rank check that explained the oracle's one miss (`pp07`, fusion friction) shows this pilot's three in-scope targets never became lexically competitive at all — zero rank movement, baseline to aliased. Reading the generated aliases against the gold questions explains why: the questions are structural/meta ("give me a *list* of my *pending* project *ideas*"), and the generated aliases — gold-blind, following `alias_backfill.py`'s content-focused prompt unmodified — describe what the notes are *about*, never the *role* the question asks by. A vocabulary miss, not fusion friction: the mechanism family this rung tested didn't reach for the register these three particular questions use, and a gold-blind generator has no way to know that register in advance. Not chased into a fix — tuning toward these three now would be exactly the gold-informed-by-the-back-door move task 1 declined when it fixed scope before checking which widening recovered more targets. **What this does and does not license:** per §4's own rule, nothing is applied to the live vault, and the alias story returns to capture-time practice only; a prompt that explicitly asks for structural/meta phrasings alongside content phrasings is untested by this run, not refuted — a candidate for a future rung, not a re-run of this one. Re-audit triggers: any successor alias-generation rung should pre-register against a *different* prompt strategy, and should re-check gold-blindness mechanically the same way this one did rather than by inspection alone. Cost not separately metered (8 batched Sonnet calls over 120 notes); index rebuild + two determinism re-scores on local compute. Detail: `<vault>/Agent/_meta/health/goldv2/alias-pilot-20260816.json`; narrative in NOTES.md § "Alias pilot". | final |
| 2026-08-16 | **The structural/meta-aware successor rung §4's own re-audit trigger licensed also closes REFUTED, on the identical clause and by the identical count — a second, independent clean null.** Same three clauses, same fixed 120-note scope, same gate, on a **fresh** `goldv2-20260812` copy (the prior scratch copy carries alias-pilot's own aliases). The one variable changed: `alias_pilot.py propose --variant structural` adds one instruction block asking the model to also name the note's role (list/index, summary, decision/plan record, status/capability snapshot) and phrase aliases for that role directly, alongside the unchanged content-phrasing rules — pure category language and template placeholders, never a gold question's own words, `GoldBlindnessTests` extended to cover it. Baseline reproduced row-for-row (0/84 differing) before any alias was written; 120 of 120 aliased, manifest-verified as exactly 120 files changed touching only `aliases:`; index rebuilt, integrity exact apart from the same +1-chunk delta alias-pilot's own run showed, for the same reason. **Clause (a): 0 of 8 (0 of 3 in-scope) converted, both arms, byte-identical stratum totals to alias-pilot's own content-only result.** Clauses (b) and (c) held cleanly again — net +0, zero negatives changed, a different but overlapping trio of harmless rank-displaced hits (`dt06`, `ep04`, `ep05` against alias-pilot's `ep04`, `ep05`, `pp08`). **The diagnosis sharpens rather than repeats.** The same lexical-rank check shows zero movement again for all three in-scope targets — but this time the generated aliases genuinely got closer: `pp05` gained *"list of home tech sub-projects"*, containing "list," a term the gold query itself uses, which the content-only prompt never produced. It still didn't move the note into the lexical top 50 of 9,971 documents. Read off the corpus rather than assumed: "list" and "project(s)" are both high-frequency terms in a project-heavy vault, so a thin one- or two-term overlap on common terms doesn't carry enough BM25 weight to outrank competing documents — the vocabulary got closer and the mechanism still lost. **What this licenses, now earned by two independent prompts rather than one:** the honest reading is no longer "the first prompt reached for the wrong words" — it is that gold-blind prompting, across two independently pre-registered strategies, has not found a way to make these three particular notes lexically competitive for these three particular questions. A third prompt variant is not foreclosed by this, but the burden shifts to showing what would be structurally different about it, not merely that it differs from the first two. Nothing applied to the live vault, per §4's own rule. Re-audit triggers: a third alias-generation attempt should target term *rarity* directly (a note is competitive only when its overlapping terms are corpus-rare, not merely present) rather than another prompt-wording iteration, and should keep the gold-blindness mechanism this pair of rungs established. Detail: `<vault>/Agent/_meta/health/goldv2/alias-pilot-structural-20260816.json`; narrative in NOTES.md § "Alias pilot, structural variant". | final |
| 2026-08-16 | **The path-signal rung closes REFUTED on clause (b), and the measurement it produced explains all three of this week's nulls at once.** Operator call, same day: this rung amends this design rather than `agentm-hybrid-retrieval.md`, because it is the vocabulary thread's fourth rung and the finding it acts on is why the first three read as they did. The change was a one-line schema edit — `docs(path UNINDEXED, …)` becomes `docs(path, …)` at `weightPath = 2.0`, with the weight chosen before the run from the already-measured ladder (`title 4.0`, `meta 3.0`, `body 1.0`) as the midpoint of the interval a hand-authored but heavily diluted signal belongs in, and explicitly **not** by sweeping the 84 questions, which this arc has now refused three times. **The control had to change shape too, and that is a finding of its own:** every prior rung proved absence by running the new build with its flag off and diffing against the landed column, but FTS5's bm25 length normalisation counts tokens across all indexed columns, so `weightPath = 0.0` with `path` indexed is not a no-op and would have read as one. The honest control is a pristine index built by the unmodified `main` binary at `26a1499`; it reproduced `question-20260814.json` and `hook-e2e-20260814.json` row for row, 0 of 84 differing on each, and both indexes asserted 9,971 docs / 9,473 embedded notes / 11,761 chunk vectors — the lexical schema changed and the dense arm did not. Two scoring runs of the changed index differ on 0 of 84 rows. **Clause (a) MET** — R@5 flat at 48/64 and 47/64, every stratum ±0, no question lost, five hits moved rank and stayed hits (`ep04` 3→2, `rc08` 4→3, `rd04` 5→4, `rd05` 2→1 better; `rd10` 2→3 worse), identical on both arms. **Clause (b) FAILED, 0 of 2** — neither `pp09` nor `pp17` converted, on either arm, and that refutes the rung. **Clause (c) MET at 6 of 8, and the pass is weak in a way the entry records rather than banks:** both CONVERTS predictions failed and all six matches are correct predictions that nothing would happen, so clause (c) is not evidence for the mechanism here — a successor rung needs a clause that fails when a *predicted* movement is absent, which today is clause (b)'s job alone and only for two of eight. **Clause (d) MET**, all 20 negatives unchanged per id. **The cause, measured rather than inferred.** The registered prediction required a path token the title lacks, that overlaps the query terms, and that is rare enough to earn IDF weight; `probe_paths.py` re-run against the control reproduces that derivation exactly, `primos` at df 47 included, and all three conditions hold. The condition nobody wrote down is that the token must also not already be matchable from the note's own body — and it is false in all eight cases. Comparing the two indexes token by token (they differ only in whether `path` is indexed, so the set difference is exactly what the path made newly matchable): `primos`, `developer`, `workflows` and `agentm` were all already matchable from the body, and the only tokens the path newly supplied to these notes are structural — `agent` at 97.2% of the corpus, `desk` at 20.8%, `projects` at 17.1%. Corpus-wide the change added 23,238 new (document, token) pairs across 9,819 of 9,971 documents, from a directory vocabulary of just **262 distinct tokens**, led by `memory`, `agent`, `desk`, `inbox`, `scratch` and machine-generated timestamp and hash directories. So the design call about IDF was right — the boilerplate discounted itself and cost nothing, which is what clause (a) proves — and what it missed is that once IDF has done that, the path holds nothing the body did not already say. **What this licenses.** `alias-pilot` wrote body vocabulary into notes whose problem was structural; `alias-pilot-structural` did it again with a role-aware prompt; this rung added structural vocabulary to notes whose distinguishing word was already in the body. All three assumed a signal was missing, and in every case it was **present and outranked**, which the body's Thread B framing above is now reconciled to. The next mechanism has to change the competition rather than the vocabulary, so chunk-level lexical indexing moves ahead of deterministic acronym extraction: length subsidy is a competition problem this 9,971-note corpus can measure, and vocabulary mechanisms are 0 for 3 here. **Nothing ships:** reverted rather than quarantined, because a column's UNINDEXED-ness is fixed when the virtual table is created and there is no flag to hide it behind — the same call the elicitation rung's revert made for the same reason. Re-audit triggers: any future path-derived signal must first show the token is not already matchable from the body, and any rung whose prediction clause passes only on its negative predictions should be read as unconfirmed rather than confirmed. Detail: `<vault>/Agent/_meta/health/goldv2/path-signal-20260816.json`; narrative in NOTES.md § "Path signal". | final |
