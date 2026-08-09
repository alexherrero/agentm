# Paste-ready prompt — coordinator session

Copy everything below the line.

---

The alias backfill is done and merged. You are picking up sequencing from here — read this as state, not as a task list I want executed in order.

## What ran

Dreaming's first real job, run by hand before dreaming exists to run it on a schedule. Week 1 traced six questions that missed under every ranking configuration to the write side: the words the operator would ask with were never in the note. The daemon indexes an `aliases`+`tags` column weighted above body, and it measured as a no-op because 56 notes of 8,958 carried an alias.

Merged as `f680013` on `agentm` `main` (PR #422), CI green across the OS matrix, `check-all.sh` 37/37 on post-merge main. Nothing is still running.

**In the vault:** 1,930 notes aliased — 1,535 into existing frontmatter, 395 into a frontmatter block created for them. 8,112 alias strings, mean 4.20 per note. The column went from 56 notes to 1,986. 70% of aliases carry a content word the note never contained.

**In the repo:** `agentmd classify --json` (per-note verdicts from the daemon's own classifier), `scripts/alias_backfill.py` (`survey` / `run` / `revert`), `scripts/test_alias_backfill.py` (30 tests pinning the write path against hand-written literals).

**Skipped and counted:** 6,311 in a penalized class; 532 indeterminable (529 mining stubs with no subject to ask for, the same set across three passes, plus two empty files and an unfilled template); 134 machine artifacts with no frontmatter; 56 already aliased; 8 refused for pre-existing invalid YAML.

Re-derive the census any time:

```bash
python3 scripts/alias_backfill.py survey --create-frontmatter
```

## What needs your judgement

**1. Session 4 must measure against a frozen snapshot.** The corpus moved 8,958 → 8,971 during this session, and the new arrivals are the session's own memory hooks depositing miner fragments into `personal/_inbox/` — the exact hazard week 1's report named. Run the retest with `--vault-path <snapshot>` or the delta is not attributable. This is the one item I would not let slip.

**2. The revert journal is the only rollback for ~1,900 frontmatter edits, and it is not backed up.** `~/alias-backfill-journal.jsonl`, 3,689 lines, one record per write with before and after hashes. `python3 scripts/alias_backfill.py revert --journal ~/alias-backfill-journal.jsonl` restores exact bytes, handles both write shapes, and refuses any file edited since. If that file is lost, this change stops being cleanly reversible. Decide whether it gets copied somewhere durable before it matters.

**3. The vault is still not a git repository.** `agentm-rescope-topology.md` sequences the git-transport migration after week 4, in the "then two weeks of just living in it" bucket, coupled to the Syncthing spike — a `.git/objects` tree under a DriveFS mount is two sync engines over one tree. The daemon refuses to `git init` on its own initiative by design (`daemon/internal/vcs/git.go:82`, pinned by `daemon/e2e/git_test.go:125`). Nothing here argues for pulling it forward. It is worth recording that the missing undo was the binding constraint twice in one session.

**4. Eight notes carry invalid YAML frontmatter, pre-existing and unrelated to this work.** All the same defect — an unquoted `title:` / `status:` / `prd:` / `inputs:` value containing a colon-space, which YAML reads as a nested mapping. They are broken for Obsidian's property panel too. The write guard refused them and they are unchanged. Quoting eight scalars is trivial; the question worth asking first is whether a template or an authoring path keeps producing them.

```
projects/agentm/_harness/archive/designs/consolidation-review/CONSOLIDATION-VERDICT.md    line 5   inputs:
projects/agentm/_harness/designs/architecture-governance/area-taxonomy.md                 line 1   title:
projects/agentm/_harness/designs/architecture-governance/worktree-native-verdict-draft.md line 3   status:
projects/agentm/_harness/designs/friday/F1-REAUDIT.md                                     line 3   status:
projects/agentm/_harness/designs/post-ag-frontload/FRIDAY-PRE-VERDICT.md                  line 3   status:
projects/agentm/_harness/designs/v8-proving/PROVING-LEDGER.md                             line 3   status:
projects/crickets/_harness/archive/dogfood/continuous-integration.baseline.md             line 9   prd:
projects/crickets/_harness/archive/dogfood/wiki-design.baseline.md                        line 9   prd:
```

**5. 529 mining stubs are inert and stay inert.** `personal/_inbox/workflow-<tool>-<n>.md`, each recording that a tool was invoked N times in some session. They carry `status: promoted`, so the status gate spares them from the fragment penalty; they rank normally and contain nothing anyone could ask for. Aliasing them was declined three times over. Whether they get deleted, re-demoted, or left is a call this session did not take.

**6. 134 machine artifacts were left without frontmatter on purpose** — scraped upstream README diffs, the `_moc/` regenerated link indexes, dream-staging digests, dated lint reports. If the `_moc/` indexes are ever meant to be retrievable that needs a different mechanism, since they are regenerated wholesale and anything written into them is transient.

## Protect this before session 4

The retrieval gold set was never opened. Selection ran off the classifier across the whole vault with no reference to the week-1 miss lists, which is what keeps the retest's delta attributable to the backfill rather than to targeting. If anyone proposes topping up coverage on specific notes before the retest, that is the thing it would destroy.
