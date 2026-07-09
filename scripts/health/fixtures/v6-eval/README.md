# V6 eval fixtures — edge fixture + query set (v0)

Pre-registered per `PLAN-wave-e-v6-index` task 2, before the V6-2 typed-edge graph (task 4) or the
V6-3 RRF retrieval (task 5) exist — the ordering FABLE R3/R4 name explicitly, to avoid the
self-derived-query and post-hoc-fixture flaws that invalidated GBrain's own BrainBench numbers.

## What's here

- **`edge-fixture-v0.json`** — 157 entries (145 real labeled edges across 7 of the 9 agreed edge
  types — `uses` · `depends-on` · `contradicts` · `caused` · `decided-in` · `implements` ·
  `references` — plus 12 deliberate negative/trap entries: real `[[...]]`-looking text in the vault
  that is **not** a link, such as code-block placeholders (`[[Target]]`, `[[X]]`, `[[Bob]]`) and
  backtick-quoted literal syntax (`` `[[wikilinks]]` `` describing the feature itself). A precision
  eval needs both — an extractor that flags every trap as a real edge has bad precision even if it
  never misses a true edge.
- **`query-set-v0.json`** — 22 human-gold queries with expected-answer note paths and a one-line
  rationale each, for V6-3's inaugural retrieval eval.

**Not covered in v0:** `supersedes` / `superseded_by` edges — a targeted search of
`projects/agentm/` found **zero** real frontmatter usage of either field anywhere in the vault today
(only prose *mentions* of the term while discussing the design). This is expected, not a gap in the
search: V6-1 is what introduces the `supersedes:`/`superseded_by:` lifecycle field — the vault can't
yet contain edges of a type nothing has written. Re-derive this fixture's `supersedes` slice once
V6-1 ships and some notes actually carry the field. Likewise `fixed` has no clean exemplar in the
`decisions/`/`designs/` corpus sampled (design docs don't narrate bug-fix events) — a future draft
should sample incident/postmortem-shaped notes for it instead.

## Source scope (v0)

Sampled from the real vault (not synthetic), bounded to two directories for this first pass:
`projects/agentm/decisions/` and `projects/agentm/_harness/designs/` (122 files, 491 raw `[[...]]`
occurrences, 310 unique source→target pairs after dedup). This is a deliberate v0 scope, not full
vault coverage — a future draft should widen the source directories once the V6-2 extractor exists
and this fixture needs to grow to match its actual operating surface.

## Authorship — read before trusting eval numbers derived from this fixture

**This is AI-drafted, not literally human-labeled**, despite FABLE R3/R4 calling for a "human-gold
query set" / "human-labeled edge fixture." The distinction matters: the entire reason these
artifacts are pre-registered *before* the graph and retrieval code exist is to avoid grading a
system against ground truth the same kind of system produced — the exact flaw that invalidated
GBrain's own self-reported numbers (synthetic corpus, self-derived queries, no external check).

Raised as an explicit clarification during Wave E task 2 (2026-07-07); the operator's answer: draft
now so task 4/5 aren't blocked, but label honestly and flag for spot-check rather than silently
calling it "gold." **Any V6-20 eval numbers computed against this fixture before an operator
spot-check should be read as provisional** — read for direction (does a layer move the needle at
all), not as a precise, independently-verified P/R figure, until reviewed.

Mechanical process actually used: a plain regex walk for `[[wikilinks]]` (`extract_candidate_edges.py`,
not committed — a one-off scratch helper, not part of the V6-2 implementation) surfaced every real
occurrence with surrounding context; each entry was then hand-typed by reading that context for
meaning, not by re-running the same heuristic the future extractor will use.

## Hash-pinning

Per FABLE R4 ("a dated file, hash-pinned in the plan") — SHA-256 of the files as committed:

```
3bdffc6dd9feda95d728e3bc911f87fcad97948e40c96d5d6194a30c340a239d  edge-fixture-v0.json
90b7620d4ee5d6e1933310b15dc5fd714d093909ad73b0d6238c92566e68a9bc  query-set-v0.json
```

Any future change to these files (including the operator's spot-check corrections) is a new,
separately-hashed version (`-v1`, etc.) — never a silent edit of `v0`. That is what makes the
"pre-registered before the graph existed" claim checkable later: task 4/5 can point at these exact
hashes as what they were evaluated against.

## Running with the vector stream enabled

`eval_v6_retrieval.py`'s vector similarity path (`recall._vec_search()`) needs `sqlite-vec`'s
extension-loading support, which the stock macOS `python3` binary lacks entirely
(`sqlite3.Connection` has no `enable_load_extension`). Run the eval under a Homebrew-built
interpreter instead (`/opt/homebrew/bin/python3.13` confirmed working, `vec_version()` returns
`v0.1.9`) to get a real vector-inclusive comparison instead of the fallback path silently
degrading past it. See the 2026-07-08 amendment-log entry in
[agentm-memory-system.md](../../../../wiki/designs/agentm-memory-system.md) for the numbers this
produced.
