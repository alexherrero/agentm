# Week-1 gold set

Ground truth for the week-1 retrieval experiment
(`wiki/designs/agentm-rescope-week1-experiment.md`). Read by
`scripts/health/week1_retrieval_experiment.py`.

Two files live here:

| file | what it is |
| --- | --- |
| `gold-set.json` | the real 60-question set, hand-labeled. A durable artifact — it outlives this experiment and becomes the ongoing recall scorecard. |
| `smoke-set.json` | eight throwaway questions written to test the runner. Not a gold set. Nothing measured against it is a result. |

## The path root is a flag, not a second file

Expected paths are labeled relative to **the agent's own tree** — `personal/…`,
`projects/…`, `external/…`. That was the vault root until the 2026-08-10
git-transport cutover moved the root up to the whole Obsidian folder, after
which a daemon answers `Agent/projects/…`. `score_at_k` matches by **exact
string equality**, so the labels have to agree with the corpus about where the
root is.

One labeled set covers both. Pass `--expected-path-prefix` when the corpus is
rooted above the agent tree:

| corpus | root it serves | pass |
| --- | --- | --- |
| `week1-corpus-20260807.tar.gz` | the agent tree | *(no prefix)* |
| `week3-retest-20260808.tar.gz` | the agent tree | *(no prefix)* |
| `stage1-pre-20260810.tar.gz` | the whole vault | `--expected-path-prefix Agent` |
| `stage1-post-20260810.tar.gz` | the whole vault | `--expected-path-prefix Agent` |
| the live vault | the whole vault | `--expected-path-prefix Agent` |

A second copy of the file with the paths rewritten was tried on 2026-08-10 and
retired the same day: two hand-synced 60-question sets drift the moment either
is relabeled, and the drift is silent, since both stay valid JSON and both keep
scoring — against different ground truth. A flag cannot drift from itself.

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
