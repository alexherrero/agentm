# Vault lint checks reference

The catalog of read-only checks `vault_lint.py` runs over agent-shaped MemoryVault entries. Each check is `(entry) -> list[Finding]` where a `Finding` carries `check_id`, `severity` (`error` / `warn` / `info`), `entry_path`, `message`, and a `suggestion`. The lint never mutates the vault — it surfaces candidate fixes for operator review (A3). It targets only entries carrying the core frontmatter trio (`kind` + `status` + `created`); the operator's free-form personal notes are skipped.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What runs the checks? | `harness/skills/memory/scripts/vault_lint.py` (the check registry + runner). |
| How do I see findings? | `python3 harness/skills/memory/scripts/vault_lint.py --format text` (or `--format json`). |
| Which entries get linted? | Only agent-shaped entries (core frontmatter trio `kind`+`status`+`created`); free-form personal notes are skipped (DC-3). The bespoke idea-ledger shapes get [their own pass](#idea-ledger-checks-bespoke-shapes). |
| How do I lint just the idea ledger? | `python3 harness/skills/memory/scripts/vault_lint.py --scope incubator`. |
| Does the lint ever edit the vault? | `vault_lint.py` itself never does — read-only / surface-only (DC-1), reports + suggests only. The composed `/memory lint` engine layered on top (`lint.py`, auto-organization part 3 task 7) auto-corrects exactly one narrow, safe case — a mis-cased wikilink with a single unambiguous target — revert-logged; every other finding, from either layer, stays surfaced-only by design. |
| Where does the schema come from? | `save.py` — the lint imports its validators + `FRONTMATTER_FIELD_ORDER` / `REQUIRED_FRONTMATTER_FIELDS` so the two can't drift (DC-2). |
| How do I run a full audit report? | See [Audit the vault](Audit-The-Vault). |
| How do I check vec-index freshness? | `python3 harness/skills/memory/scripts/vault_lint.py --check-freshness` (or `--format json`). |
| How do I also get orphans, contradictions, and a quality score? | `/memory lint` (`harness/skills/memory/scripts/lint.py`) composes this catalog's `supersede-cycle` / `supersede-fork` / `dangling-supersession` / `kind-taxonomy` checks with `graph_snapshot.orphans()` and a per-note quality score, on demand or via the weekly dreaming cycle — see the memory skill's `/memory lint` section. |
| Related pages | [Audit the vault](Audit-The-Vault) |

## Checks

14 checks run over every agent-shaped entry. Severities: `error` (off-spec — needs a fix) · `warn` (drift or smell — review). The lint exits `0` regardless; findings are advisory.

| Check ID | Severity | What it checks | Suggested-fix shape |
|---|---|---|---|
| `required-field` | error | A required frontmatter field (every field except the optional ones — `source_url`, `source_fetched`, `fingerprint`, `occurrences`, `supersedes`, `lifecycle_tier`, `derived_from`, `heat_pin`, `arc`) is missing. | Add the missing field in the locked order. |
| `kebab-case` | error | `kind` / `slug` / `group` path segments / each `tag` are kebab-case (`^[a-z0-9-]+$`; `group` is `/`-joined kebab segments). | Rename the offending value to kebab-case. |
| `field-order` | warn | The present frontmatter fields appear in the locked order (`kind, status, created, updated, tags, arc, group, slug, source_url, source_fetched, fingerprint, occurrences, always_load, supersedes, lifecycle_tier, derived_from, heat_pin`). | Reorder frontmatter to the locked order. |
| `slug-filename` | warn | The `slug` value matches the filename stem. | Rename the file to `<slug>.md`, or fix the `slug` field. |
| `date-format` | error / warn | `created` / `updated` are `YYYY-MM-DD` (error if malformed); `updated` is on or after `created` (warn if earlier). | Set a valid date / make `updated` ≥ `created`. |
| `placeholder-value` | warn | A frontmatter value still holds an unfilled template option-list (`a \| b \| c`). | Replace with the single chosen value. |
| `schema-drift` | warn | A frontmatter key is not in the locked schema (unknown key). | Remove the key, or confirm an intentional schema addition. |
| `wikilink-resolution` | error | Every `[[link]]` in the body resolves to a file in the enclosing Obsidian vault — by filename stem, by relative path, or by an `aliases:` entry, vault-wide. | Fix the target, create the note, or remove the link. |
| `supersede-integrity` | error / warn | `supersedes:` resolves to a real entry (error if dangling); the superseded entry is no longer `active` (warn if still `active`). | Fix the reference / set the target's status to `superseded`. |
| `supersede-cycle` | error | A `supersedes:` chain loops back on itself (A supersedes B ... supersedes A). | Break the cycle — fix the `supersedes` target on one entry in the chain. |
| `supersede-fork` | warn | Two or more entries both claim `supersedes:` the same target. | Keep exactly one successor; reconcile the others (merge, retarget, or drop the extra `supersedes`). |
| `dangling-supersession` | warn | `status: superseded` but no entry's `supersedes:` points here. | Add `supersedes: <successor>` on the entry that replaced this one, or revert `status` if nothing did. |
| `kind-taxonomy` | warn | `kind` is not in `kind_registry.py`'s `KNOWN_KINDS` registry. | Use a registered kind, or add this one to `KNOWN_KINDS` if it's a genuine addition. |
| `arc-registry` | error | `arc` (when present — most entries carry none) is kebab-case and a recognized slug in `arc_registry.py`'s `KNOWN_ARCS`. | Rename to kebab-case, or add the slug to `KNOWN_ARCS`. |

Anchor files (`_index`, `_summary`) are exempt from the kebab `slug` check. The bespoke idea-ledger shapes — the incubator files and `Ideas.md` — are still skipped by *these* checks, because they are not `save.py`-shaped; they get their own catalog below. Scheduled / unattended runs of this raw check suite are deferred to V6; the weekly *composed* run (orphans + quality score + the four contradiction/taxonomy checks above + the mis-cased-wikilink auto-repair) already ships today via `dream.py`'s `_stage_lint()` — see `/memory lint`.

## Idea-ledger checks (bespoke shapes)

`incubator_lint.py` runs a second pass over the two idea-ledger surfaces, which deliberately do not use the `save.py` schema. It runs automatically at `--scope all`, or on its own with `--scope incubator`. Same `Finding` shape, same read-only contract.

The ledger has four file roles, keyed off the filename, each with its own `kind`:

| File | `kind` |
|---|---|
| `_index.md` | `idea-incubator` |
| `_summary.md` | `idea-incubator-summary` |
| `research-*.md` | `idea-incubator-research` |
| `runbook-*.md` | `idea-incubator-runbook` |

All four carry the same five-field core — `kind`, `status`, `slug`, `created`, `updated`. Notably **absent**: `tags` and `group`, which `save.py` requires and most ledger files don't have. `slug` holds the *incubator's* slug, not the filename stem, so `_summary.md` correctly carries `slug: home-server-cluster`.

| Check ID | Severity | What it checks | Suggested-fix shape |
|---|---|---|---|
| `incubator-frontmatter` | error | The file has a frontmatter block at all. | Add one carrying the five core fields. |
| `incubator-core-field` | error | Each of `kind` / `status` / `slug` / `created` / `updated` is present. | Add the missing field. |
| `incubator-file-role` | warn | The filename matches one of the four roles above. | Rename the file, or register the new role. |
| `incubator-kind-role` | error | `kind` matches the role the filename declares. | Set the matching `kind`, or rename the file. |
| `incubator-status` | warn | `status` is one of `research-pending`, `research-partial`, `research-complete`, `promoted-to-design`, `deprioritized`, `spec-ready`. | Use a known status, or register a genuinely new one. |
| `incubator-date` | error / warn | `created` / `updated` are `YYYY-MM-DD` (error); `updated` is on or after `created` (warn). | Fix the date. |
| `incubator-backref` | error | A `research-*` / `runbook-*` file's `incubator:` matches its enclosing directory. | Fix `incubator:`, or move the file. |
| `incubator-anchor` | error | Every `<slug>/` directory has an `_index.md`. | Add the anchor. |
| `incubator-summary-missing` | warn | Every `<slug>/` directory has a `_summary.md`, per the `idea-incubator-summary-doc` convention — even as a research-pending placeholder. Directories whose status is `promoted-to-design` are exempt: the idea has left the incubator, so there is no landing spot left to signpost. | Add the placeholder summary. |
| `incubator-slug-agreement` | error | `_index.md` and `_summary.md` in one directory carry the same `slug`. | Reconcile the two. |
| `incubator-status-agreement` | warn | Those two files carry the same `status`. | Reconcile — usually `_index.md` is the stale one, since research updates the summary first. |
| `incubator-wikilink` | error | Every `[[link]]` in a ledger file's body resolves (alias-aware, same resolver as `wikilink-resolution`). | Fix the target, create the note, or remove the link. |
| `ideas-heading` | warn | Every `Ideas.md` entry heading is `## YYYY-MM-DD: <Title>`, or the dismissed form `## ~~YYYY-MM-DD: <Title>~~ — Dismissed YYYY-MM-DD`. | Rename the heading. |
| `ideas-incubator-link` | error | Every `[[_idea-incubator/…]]` link in `Ideas.md` resolves. | Fix the target, or create the note. |

**What is deliberately not checked.** `Ideas.md` has no frontmatter and none is expected — it lives at the Obsidian root, outside the memory vault. And `_summary.md` body structure is not checked at all: the `idea-incubator-summary-doc` convention prescribes five sections (Research scope / Key findings / Recommendations / Open questions / Confidence level), but only one of the five real summaries uses them. Enforcing that shape would flag four good files, so the rules follow the corpus and leave body structure alone.

`Ideas.md` is located by `--ideas-path`, then `$IDEAS_SURFACE_PATH`, then the vault's parent directory — the last only when that parent actually holds a `.obsidian/`, so a scratch vault never adopts an unrelated `Ideas.md` sitting beside it.

## Vault-wide freshness check

`--check-freshness` is a different shape of check from the table above: a single vault-wide ratio, not a per-entry finding. It computes the vec-index freshness ratio via `vec_index.find_drifted_entries()` — `up_to_date / (up_to_date + drifted + not_indexed)` — and reports it in either output format:

- `--format json` — `{"up_to_date": .., "drifted": .., "not_indexed": .., "ratio": ..}`
- `--format text` — a one-line summary

Below a ratio of `0.80` it prints a WARN suggesting `full-sync --rebuild` then `drain` to catch the index back up. Like every other `vault_lint.py` mode, it is advisory — the process exits `0` regardless of the ratio; a behind index is recoverable, not broken.

It is also wired into `doctor`'s default-mode structural checks (item 7 in `harness/skills/doctor.md`) so a drifted index surfaces within a day on the operator's own machine without a manual run.

## Related

- [Audit the vault](Audit-The-Vault) — the operator recipe that runs these checks and reads the report.
- `harness/skills/memory/SKILL.md`'s `/memory lint` section — the composed engine (orphans, contradictions, quality score, mis-cased-wikilink auto-repair) built on top of this catalog.
