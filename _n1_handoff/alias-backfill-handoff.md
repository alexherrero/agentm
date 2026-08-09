# Handoff — the alias backfill (session 3)

**Status: complete and merged.** [PR #422](https://github.com/alexherrero/agentm/pull/422), squashed to `f680013` on `main`, CI green across the OS matrix. Nothing is left running.

> **Superseded 2026-08-09 — the writes this describes have been reverted.** The week-3 retest measured this job at **−3.85 points of R@5, p = 0.0411**, and the 1,930 alias lines were reverted on the live vault the same day. The code, the journal, and the reasoning below all stand; what changed is that note-sourced aliases are no longer a scheduled job. See `scripts/health/results/week3-retest/NOTES.md` and the amendment logs in `wiki/designs/agentm-rescope-memory.md` and `wiki/designs/agentm-rescope-week1-experiment.md`.

## What this session was

Dreaming's first real job, run by hand before dreaming exists to run it on a schedule. Week 1 measured paraphrase recall at R@5 0.472 and found the cause on the write side rather than the read side: the six questions that missed under every ranking configuration missed because the words the operator would ask with were never in the note. The daemon already indexes an `aliases`+`tags` column weighted above body, and it measured as a no-op because 56 notes of 8,958 carried an alias. This filled it.

## What landed in the vault

| | |
| --- | ---: |
| notes aliased | **1,930** |
| — into existing frontmatter | 1,535 |
| — into a frontmatter block created for them | 395 |
| alias strings written | 8,112 |
| mean aliases per note | 4.20 |
| notes carrying an alias, before → after | 56 → 1,986 |
| commits made in the vault | **0** (it is not a git repository) |

Aliases add vocabulary rather than restating titles: 70% carry at least one content word the note never contained, mean 1.33 such words each.

Skipped, all counted:

| skipped | count | reason |
| --- | ---: | --- |
| penalized class (`fragment` / `status` / `staging`) | 6,311 | demoted, not decorated |
| indeterminable | 532 | 529 mining stubs recording a tool-invocation count with no subject to ask for — the same set across three independent passes — plus two 0-byte files and one unfilled `PLAN.md` template |
| machine artifact with no frontmatter | 134 | scraped upstream README diffs, `_moc/` regenerated link indexes, dream-staging digests, dated lint reports |
| already aliased | 56 | idempotence; never overwritten |
| invalid YAML before this ran | 8 | the write guard refused; those notes are unchanged |

## What shipped in the repo

- `agentmd classify --json` — per-note classification from the daemon's own classifier, so nothing downstream needs a second copy of the rank-penalty rules.
- `scripts/alias_backfill.py` — `survey` / `run` / `revert`. Adds one `aliases:` line and nothing else. Resumable by construction (a note with a non-empty `aliases` list is done). `--create-frontmatter` covers notes that had no block, gated by a readable path allowlist rather than a size heuristic.
- `scripts/test_alias_backfill.py` — 30 tests pinning the write path against hand-written literals.

Local battery: `check-all.sh` 37 passed, 0 failed, both before and after the merge.

## Open ends

**1. The vault is still not a git repository.** The daemon reports `git DEGRADED — the git-transport migration has not run`. The design sequences that migration *after* week 4, in the "then two weeks of just living in it" bucket, coupled to the Syncthing spike — a `.git/objects` tree under a DriveFS mount means two sync engines over one tree. The daemon deliberately refuses to `git init` on its own initiative (`daemon/internal/vcs/git.go:82`, pinned by `daemon/e2e/git_test.go:125`); it is the operator's migration to run.

Bearing on this: the vault now holds ~1,900 frontmatter edits with no version history behind them. The only undo is the journal below. Not an argument to pull the migration forward — an argument that the missing undo is now load-bearing twice over.

**2. The revert journal is the only rollback, and it lives outside the repo.** `~/alias-backfill-journal.jsonl`, 3,689 lines, one record per write with before and after hashes. `python3 scripts/alias_backfill.py revert --journal ~/alias-backfill-journal.jsonl` restores exact bytes, handles both write shapes, and refuses any file edited since. If that file is lost, this change is not cleanly reversible. It is not backed up anywhere.

**3. Eight notes carry invalid YAML frontmatter, pre-existing.** All the same defect: an unquoted `title:` / `status:` / `prd:` / `inputs:` value containing a colon-space, which YAML reads as a nested mapping. They are broken for Obsidian's property panel too. Left unrepaired because repairing edits existing frontmatter values, and this job's contract was to add a field. An eight-line fix whenever someone wants it:

```
projects/agentm/_harness/archive/designs/consolidation-review/CONSOLIDATION-VERDICT.md   line 5  inputs:
projects/agentm/_harness/designs/architecture-governance/area-taxonomy.md                line 1  title:
projects/agentm/_harness/designs/architecture-governance/worktree-native-verdict-draft.md line 3 status:
projects/agentm/_harness/designs/friday/F1-REAUDIT.md                                    line 3  status:
projects/agentm/_harness/designs/post-ag-frontload/FRIDAY-PRE-VERDICT.md                  line 3  status:
projects/agentm/_harness/designs/v8-proving/PROVING-LEDGER.md                             line 3  status:
projects/crickets/_harness/archive/dogfood/continuous-integration.baseline.md             line 9  prd:
projects/crickets/_harness/archive/dogfood/wiki-design.baseline.md                        line 9  prd:
```

Worth a moment's thought about whether a template or an authoring path is producing them, rather than just quoting eight scalars.

**4. 529 mining stubs are inert and will stay inert.** `personal/_inbox/workflow-<tool>-<n>.md`, each saying that a tool was invoked N times during some session. They carry `status: promoted`, so the status gate spares them from the fragment penalty, and they have no subject anyone could ask for. They rank normally and contain nothing. Aliasing them was declined three times. Deleting or re-demoting them is a separate decision this session did not take.

**5. The corpus moved while this ran** — 8,958 → 8,971 notes, and the new arrivals are the session's own memory hooks depositing miner fragments into `personal/_inbox/`. This is the exact hazard week 1's report named when it said to freeze the corpus before measuring. **Session 4 must snapshot the vault and run against the snapshot**, or its delta is not attributable.

**6. The 134 machine artifacts remain unaliased by choice.** If the `_moc/` link indexes are ever meant to be retrievable, that is a different mechanism — they are regenerated wholesale, so anything written into them is transient.

## For session 4

The gold set was never opened. Selection was classifier-driven across the whole vault, with no reference to the week-1 miss lists, so the retest's delta is attributable to the backfill rather than to targeting. The `fields` lexical variant — the one that measured identical to baseline because only 5.5% of the corpus had anything in that column — is now the variant with something to measure. Six of the week-1 misses were traced to vocabulary the notes never contained; that is the population this was aimed at, without knowing which notes they were.

Reproduce the census at any time:

```bash
python3 scripts/alias_backfill.py survey --create-frontmatter
```
