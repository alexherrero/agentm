# Week-1 gold set

Ground truth for the week-1 retrieval experiment
(`wiki/designs/agentm-rescope-week1-experiment.md`). Read by
`scripts/health/week1_retrieval_experiment.py`.

Three files live here:

| file | what it is |
| --- | --- |
| `gold-set.json` | the original 60-question set, hand-labeled, **pinned**. Labeled against the agent tree as the two pre-cutover snapshots still hold it. Do not relabel it; they cannot move. |
| `gold-set-v2.json` | the merged 84-question set (2026-08-12), labeled against `goldv2-20260812`. The 60 originals re-resolved, plus a `research-corpus` stratum and negatives grown 8 → 20. This is the one to score current work against. |
| `smoke-set.json` | eight throwaway questions written to test the runner. Not a gold set. Nothing measured against it is a result. |

## Why v2 is a new file rather than an edit

Labels are paths, and paths are a rendering of location rather than identity.
Slugs would be the obvious stable key and they do not work: 1,008 of 7,377
distinct slugs collide across the tree, because every project carries an
`_index` and a `conventions`. So there is no ID to key on, and a gold set is
labeled **against one named corpus**. Scoring a different corpus needs an
explicit recorded remap, never a silent transform.

The stage-2 move and the rehoming pass together relocated every path in the
original set: 56 of 64 by the mechanical space rename, 6 more by unique
basename lookup, and 2 by explicit record where the rehoming re-slugged a
domain index. All 84 questions' targets resolve in `goldv2-20260812`.

## Which set, which corpus, which flag

`score_at_k` matches by **exact string equality**, so labels must agree with the
corpus about where every note sits. Two different mismatches can arise, and they
have different answers.

**A different root** is a flag. `--expected-path-prefix Agent` covers a corpus
rooted at the whole vault rather than the agent tree — the 2026-08-10 cutover's
only effect on labels. A prefix cannot drift from itself, which is why an
early second copy of the file with rewritten paths was retired the same day.

**A different layout** is a different corpus, and needs its own labeled set.
The stage-2 move and the rehoming pass relocated notes *within* the tree, which
no prefix can express. That is what `gold-set-v2.json` is.

| corpus | score with | flag |
| --- | --- | --- |
| `week1-corpus-20260807.tar.gz` | `gold-set.json` | *(none)* |
| `week3-retest-20260808.tar.gz` | `gold-set.json` | *(none)* |
| `stage1-pre-20260810.tar.gz` | `gold-set.json` | `--expected-path-prefix Agent` |
| `stage1-post-20260810.tar.gz` | `gold-set.json` | `--expected-path-prefix Agent` |
| `goldv2-20260812.tar.gz` | `gold-set-v2.json` | *(none — labels are absolute)* |
| the live vault | `gold-set-v2.json` | *(none, while live matches goldv2)* |

## Getting the root wrong is loud

Every harness resolves expected paths against the corpus **before** the first
driver call and aborts with exit 2 if any are missing, rather than spending a
full run to produce scores that are labeling artefacts. The error names the
prefix that would have worked, discovered by searching the corpus's own
top-level directories rather than assuming `Agent/`:

```
[week1] ERROR: 64 expected note path(s) … do not exist in the vault.
[week1] HINT: every missing path resolves under 'Agent'. Re-run with
        --expected-path-prefix Agent — the gold set is labeled relative to
        that subtree, and this corpus is rooted one level above it.
```

That precheck is why a root mismatch costs one second instead of a scorecard.

## Schema

A JSON array, or an object with an `entries` array, of:

```json
{
  "id": "q07",
  "question": "What did we decide about the vault path convention?",
  "expected_note_paths": ["personal/_always-load/vault-path-resolve-dont-recall.md"],
  "stratum": "distinctive-token",
  "source": "transcript"
}
```

- **`id`** — unique within the file.
- **`question`** — as a person would actually ask it.
- **`expected_note_paths`** — vault-relative POSIX paths. Every path must exist;
  the runner exits non-zero and names the offenders rather than scoring a
  labeling error as a retrieval miss. An empty list is the negative stratum.
- **`stratum`** — one of `distinctive-token`, `pure-paraphrase`,
  `episodic-temporal`, `research-density`, `negative`. Scores break out per
  stratum, so a stratum name that appears nowhere else silently becomes its own
  one-question bucket.
- **`source`** — `transcript`, `cold`, or `authored`.

## Two labeling constraints from the design

Do not label against a note on the retirement list in
`agentm-rescope-memory.md`. The gold set outlives the cutover; a question
pointing at a note that will not exist is a question that will have to be
rewritten or dropped.

Expect inbox-staged notes to rank low. They are rank-penalized deliberately, so
a low-ranked `status: inbox` result is the system working rather than a miss to
score against.

## Scoring, and what the negative stratum means

P@5 and R@5 against `expected_note_paths`, via `eval_v6_retrieval.score_at_k`.
P@5 divides by 5, not by how many paths came back.

Negative questions score 1.0 only when the driver *concluded* that nothing
answers the question. A timeout, a crash, or a reply that never reached its
`ANSWER:` line also produces zero paths, and those score 0.0 — otherwise a
broken driver would look like a well-calibrated one.

## Running it

```bash
python3 scripts/health/week1_retrieval_experiment.py --gold-set scripts/health/fixtures/week1-gold/gold-set.json --arm A --out /tmp/armA.json
```

`--arm B` adds the vector tool. `--driver mock` exercises the harness without an
API key. `--limit N` runs the first N questions.
