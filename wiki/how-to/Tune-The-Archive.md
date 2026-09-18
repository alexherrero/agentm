# How to tune the archive

> [!NOTE]
> **Status: implemented** — the per-space lifecycle policy landed in agentm-vault plan 11. The dreaming binary carries the axis; the Python cycle's tidying stage retired in plan 04.
> **Goal:** Understand what ages a memory and what never does, read the contract's four numbers, and bring a note back — from `dormant`, from the archive, or from a deletion the manifest recorded.
> **Prereqs:** None to read this page. Changing a threshold means editing the contract at `standards/storage-rules.md`.

Aging runs on its own. The nightly `agentmdream` pass sinks, archives and — past the last line — deletes, each act journaled, each deletion with a manifest written before it. You do not have to do anything for it to work. This page is for knowing what it touches, and for the times the defaults do not fit.

## Steps

1. **Know what ages and what does not.** The machinery changes a note's state only where you said it may: the three observational classes (`memory/semantic`, `memory/procedural`, `memory/episodic`), the calendar, and the diagnostics. Everything else is permanent — `projects/`, `personal/`, `standards/`, crystallized lessons and the root notes. They rank and they receive enrichment; no pass demotes, archives or deletes them. You supersede or replace them.

   Inside the three classes, four categories are exempt anyway: `lifecycle: pinned`; `type: preference` and `type: convention`, which decay but never sink — a rule unread for a year is still the rule; and any crystallized lesson.

2. **Read the four numbers.** They live in the contract's `thresholds:` block, at `standards/storage-rules.md` (packaged default in `daemon/internal/rules/storage-rules.default.md`). Both ranking arms read the same block.

   | What happens | After | Key |
   |---|---|---|
   | Ranks at full weight through | 180 days | `decay_full_days` |
   | Half weight through | 365 days | `decay_half_days` |
   | An eighth through | 1,095 days | `decay_eighth_days` |
   | The floor (0.0625), from | 1,825 days | `decay_floor_days`, `decay_floor_weight` |
   | Sinks to `dormant`, in place, ×0.30 on top of the curve | 365 days | `dormant_after_days` |
   | Moves to `archive/memory/<class>/`, stamped `archived` | 1,825 days | `archive_after_days` |
   | Deleted, manifest first | 2,555 days **and** the archive wait served | `forget_after_days` |
   | Most notes one pass may sink | 25 | `demotion_cap` |

   A session trace runs on a shorter line — 90 · 365 · 1,095 — set in `lifecycle_overrides.episodic`. The diagnostics have their own `retention:` block, per file kind; migration and purge manifests are deliberately absent from it, because they are the record of what moved and what was forgotten.

3. **Know what the clock reads.** Only a genuine recall resets it. Opening a file, or reading it through a skill, does not. A recall before the archive line returns the note to day 0; after the archive move, serving it on an explicit archive query or moving it back returns it to `active` with its clock reset. A hand edit of `lifecycle:` counts as a touch, so a note you have just reconsidered does not sink the same night.

4. **Bring something back.**
   - **See what is coming.** The morning note's *what needs you* lists *sinking within 30 days* and *archiving within 30 days* with how long each note has been silent. A threshold is easier to argue with while it is still thirty days off. `agentmdream run -force` prints what the next pass would do and writes nothing.
   - **A dormant note** returns on its own: the next genuine recall lifts it.
   - **An archived note** is still a file, at `archive/memory/<class>/<slug>.md`. Bring it back with `/memory revive <slug>`, which moves it to its class folder, sets `active` and resets the clock. Find it first with `/memory search --deep <words>` or `recall.py query "<words>" --include-archive`.
   - **A deleted note** was named in a manifest under `diagnostics/migrations/purge/` before it went, and the vault's git history still holds it. `/memory search --deep <words>` reads both and prints the `git show` that recovers the file.
   - **Pin something so none of this reaches it:** `/memory pin <slug>`.

5. **Change a number, then prove it.** Edit the contract line, then run `bash scripts/check-all.sh`. The decay curve is one curve read by both arms from that block, so a change reaches the daemon and `recall.py` together; `scripts/test_decay_curve_parity.py` is what holds them to it. Before trusting a new curve in live ranking, run the retrieval gate: `python3 scripts/health/eval_v6_retrieval.py --vault-path <path>`.

## Verify

- `scripts/test_decay_curve_parity.py` — the two arms score the same note the same way from the same contract block.
- `scripts/test_lifecycle_transitions.py` — the archive lane moves the file and moves it back, the exempt states, the cap, and the thresholds read from the contract.
- `go test ./internal/dreaming/` from `daemon/` — the sink, the archive move, the manifest-before-deletion rule, and the two clocks a deletion needs.

## Troubleshooting

- **A long-silent note is still `active`.** Check whether it is exempt (step 1), then whether a recall reset its clock: the sidecar lives in `<engine state dir>`, keyed by path. `agentmdream status` reports the last pass; a note sinks only on a pass.
- **A note vanished from its class folder.** It was archived, which moves it now. Look in `archive/memory/<class>/`, or run `/memory search --deep`.
- **`daemon.decay_enabled` is off.** Then the curve computes and nothing ranks by it. It is thrown only with the retrieval gate clean on both arms.

## See also

- [AgentM Vault design § Lifecycle per space](../designs/agentm-vault) — the policy this page tunes, and why each number is what it is.
- [Archive a finished project](Archive-A-Finished-Project) — the same idea for a whole project, which is a different mechanism.
- [Memory daemon reference § the dreaming binary](Memory-Daemon#the-dreaming-binary-agentmdream) — the lifecycle job, its gate and its report.
