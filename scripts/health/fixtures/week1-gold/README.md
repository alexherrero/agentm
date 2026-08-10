# Week-1 gold set

Ground truth for the week-1 retrieval experiment
(`wiki/designs/agentm-rescope-week1-experiment.md`). Read by
`scripts/health/week1_retrieval_experiment.py`.

Three files live here:

| file | what it is |
| --- | --- |
| `gold-set.json` | the real 60-question set, hand-labeled. A durable artifact — it outlives this experiment and becomes the ongoing recall scorecard. |
| `gold-set-vault-root.json` | the same 60 questions with every expected path prefixed `Agent/`. Same labels, same strata — only the path root differs. |
| `smoke-set.json` | eight throwaway questions written to test the runner. Not a gold set. Nothing measured against it is a result. |

## Which gold set to pass

`score_at_k` matches expected paths against answered paths by **exact string
equality** — no normalization, no prefix stripping. So the file you pass has to
share a root with the corpus the daemon is serving, and neither harness defaults
`--gold-set`: you always name one.

| corpus | root it serves | pass |
| --- | --- | --- |
| `week1-corpus-20260807.tar.gz` | the agent tree | `gold-set.json` |
| `week3-retest-20260808.tar.gz` | the agent tree | `gold-set.json` |
| `stage1-pre-20260810.tar.gz` | the whole vault | `gold-set-vault-root.json` |
| `stage1-post-20260810.tar.gz` | the whole vault | `gold-set-vault-root.json` |
| the live vault | the whole vault | `gold-set-vault-root.json` |

The split exists because the 2026-08-10 git-transport cutover moved the vault
root up from the agent's own tree to the whole Obsidian folder, so the daemon
now answers `Agent/projects/…` where it used to answer `projects/…`. The two
frozen week-1/week-3 snapshots keep the old root forever — that is what a frozen
snapshot is for — so the original labels stay correct for them and are left
byte-identical, per this repo's fixture discipline: a correction is a new file,
never a mutation of the pinned one.

Against the live vault the original set scores **0/64** and has since the
cutover. That is a stale label, not a retrieval regression, and any scorecard run
against the live vault before 2026-08-10 that looked catastrophic should be
re-read with that in mind.

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
