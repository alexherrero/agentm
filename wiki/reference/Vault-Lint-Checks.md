# Vault lint checks reference

The catalog of read-only checks `vault_lint.py` runs over agent-shaped MemoryVault entries. Each check is `(entry) -> list[Finding]` where a `Finding` carries `check_id`, `severity` (`error` / `warn` / `info`), `entry_path`, `message`, and a `suggestion`. The lint never mutates the vault — it surfaces candidate fixes for operator review (A3). It targets only entries carrying the core frontmatter trio (`kind` + `status` + `created`); the operator's free-form personal notes are skipped.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What runs the checks? | `harness/skills/memory/scripts/vault_lint.py` (the check registry + runner). |
| How do I see findings? | `python3 harness/skills/memory/scripts/vault_lint.py --format text` (or `--format json`). |
| Which entries get linted? | Only agent-shaped entries (core frontmatter trio `kind`+`status`+`created`); free-form personal notes are skipped (DC-3). The idea cards in `personal/ideas/` are out of scope entirely — [see below](#idea-cards-are-not-linted-here). |
| Does the lint ever edit the vault? | `vault_lint.py` itself never does — read-only / surface-only (DC-1), reports + suggests only. The composed `/memory lint` engine layered on top (`lint.py`, auto-organization part 3 task 7) has one repair, for one narrow, safe case — a mis-cased wikilink with a single unambiguous target — and writes it only when you pass `--apply`. The nightly dream cycle counts those links and repairs none. Every other finding, from either layer, stays surfaced-only by design. |
| Where does the schema come from? | `save.py` — the lint imports its validators + `FRONTMATTER_FIELD_ORDER` / `REQUIRED_FRONTMATTER_FIELDS` so the two can't drift (DC-2). |
| How do I run a full audit report? | See [Audit the vault](Audit-The-Vault). |
| How do I also get orphans, contradictions, and a quality score? | `/memory lint` (`harness/skills/memory/scripts/lint.py`) composes this catalog's `supersede-cycle` / `supersede-fork` / `dangling-supersession` / `kind-taxonomy` checks with `graph_snapshot.orphans()` and a per-note quality score, on demand or via the nightly dream cycle — see the memory skill's `/memory lint` section. |
| Related pages | [Audit the vault](Audit-The-Vault) |

## Checks

14 checks run over every agent-shaped entry. Severities: `error` (off-spec — needs a fix) · `warn` (drift or smell — review). The lint exits `0` regardless; findings are advisory.

| Check ID | Severity | What it checks | Suggested-fix shape |
|---|---|---|---|
| `required-field` | error | A required frontmatter field is missing: `kind`, `status`, `created`, `updated` or `slug` (`save.REQUIRED_FRONTMATTER_FIELDS`). Every other field in the locked order is optional; `tags` became optional with the card backfill (agentm-vault plan 06), which omits an empty list. | Add the missing field in the locked order. |
| `kebab-case` | error | `kind` / `slug` / `group` path segments / each `tag` are kebab-case (`^[a-z0-9-]+$`; `group` is `/`-joined kebab segments). | Rename the offending value to kebab-case. |
| `field-order` | warn | The present frontmatter fields appear in the locked order — the card's order (agentm-vault § The card, the read block then the machine block): `title, kind, summary, why, importance, status, lifecycle, lifecycle_since, filing_confidence, source, source_url, source_id, source_fetched, trust, created, updated, tags, related, supersedes, superseded_by, project, task, arc, group, lifecycle_tier, heat_pin, slug, fingerprint, occurrences, derived_from, via, surface, instructions, review_flags`, read straight from `save.FRONTMATTER_FIELD_ORDER` so the two can't drift. The card backfill (agentm-vault plan 06) brought the corpus to this order. | Reorder frontmatter to the locked order. |
| `slug-filename` | warn | The `slug` value matches the filename stem. | Rename the file to `<slug>.md`, or fix the `slug` field. |
| `date-format` | error / warn | `created` / `updated` are a `YYYY-MM-DD` date or an ISO timestamp on that day — the capture door writes the instant (error if neither); `updated` is on or after `created`, compared by day (warn if earlier). | Set a valid date or timestamp / make `updated`'s day ≥ `created`'s day. |
| `placeholder-value` | warn | A frontmatter value still holds an unfilled template option-list (`a \| b \| c`). | Replace with the single chosen value. |
| `schema-drift` | warn | A frontmatter key is not in the locked schema (unknown key). | Remove the key, or confirm an intentional schema addition. |
| `wikilink-resolution` | error | Every `[[link]]` in the body resolves to a file in the enclosing Obsidian vault — by filename stem, by relative path, or by an `aliases:` entry, vault-wide. | Fix the target, create the note, or remove the link. |
| `supersede-integrity` | error / warn | `superseded_by:` and `supersedes:` each resolve to a real entry (error if dangling; a source version `<id> at <version>` is left alone); a `supersedes:` target still `active` on the lifecycle axis warns. | Fix the reference, or set the target's `lifecycle: superseded` and name the successor in its `superseded_by:`. |
| `supersede-cycle` | error | A `supersedes:` chain loops back on itself (A supersedes B ... supersedes A). | Break the cycle — fix the `supersedes` target on one entry in the chain. |
| `supersede-fork` | warn | Two or more entries both claim `supersedes:` the same target. | Keep exactly one successor; reconcile the others (merge, retarget, or drop the extra `supersedes`). |
| `dangling-supersession` | warn | A superseded memory (`lifecycle: superseded`, or the pre-contract `status: superseded`) with no lineage: no `superseded_by:` of its own, and no entry's `supersedes:` points here. | Add `superseded_by: <successor>` on this note (and `supersedes:` on the successor), or revert the state if nothing replaced it. |
| `kind-taxonomy` | warn | `kind` is not in `kind_registry.py`'s `KNOWN_KINDS` registry. | Use a registered kind, or add this one to `KNOWN_KINDS` if it's a genuine addition. |
| `arc-registry` | error | `arc` (when present — most entries carry none) is kebab-case and a recognized slug in `arc_registry.py`'s `KNOWN_ARCS`. | Rename to kebab-case, or add the slug to `KNOWN_ARCS`. |

Anchor files (`_index`, `_summary`) are exempt from the kebab `slug` check. The `_idea-incubator/` research files and the idea cards in `personal/ideas/` are skipped by *these* checks too, because neither is `save.py`-shaped — see [below](#idea-cards-are-not-linted-here) for what, if anything, covers each one now. Scheduled / unattended runs of this raw check suite are deferred to V6; the nightly *composed* run (orphans + quality score + the four contradiction/taxonomy checks above + a count of the mis-cased links `--apply` would repair) ships today via `dream.py`'s `_stage_lint()`, as a report that applies nothing — see `/memory lint`.

## Idea cards are not linted here

The bespoke idea-ledger pass that used to run here — `incubator_lint.py`, checking the `_idea-incubator/` research files and the hand-kept `Ideas.md` against their own shapes, automatically at `--scope all` or alone via `--scope incubator` — retired in agentm-vault part 13, along with the two surfaces it checked. `--scope incubator` retired with it: `vault_lint.py --scope incubator` now refuses at the argument parser, the same as any other unrecognized `--scope` value, since `incubator` is gone from the set the flag accepts (`all`, `always-load`, `projects`, `memory`).

`Ideas.md` is generated over the idea cards in `personal/ideas/` by the dreaming binary now, and it holds the file's shape itself, with its own tests (`daemon/internal/dreaming/ideas_test.go`) — this catalog was never going to be the thing that kept checking it. The `_idea-incubator/` research files have no lint pass of their own at the moment; the incubator skeleton and its researcher sub-agent are still there, waiting on their own follow-up.

The idea cards themselves are not walked as entries here either — `personal/` sits outside every scope in the table above. One resolution still reaches into the folder: a duplicate note's `superseded_by: personal/ideas/<stem>.md`, pointing at the idea it was folded into, resolves against the folder's filenames (`build_model`, `vault_lint.py:403`) so `supersede-integrity` does not flag it as dangling — added to `model.slugs` only, never linted as an entry in its own right.

## Related

- [Audit the vault](Audit-The-Vault) — the operator recipe that runs these checks and reads the report.
- `harness/skills/memory/SKILL.md`'s `/memory lint` section — the composed engine (orphans, contradictions, quality score, mis-cased-wikilink auto-repair) built on top of this catalog.
