# Vault lint checks reference

> [!NOTE]
> **Status:** implemented
> **Plan:** `.harness/PLAN.md` task 1 (read-only `vault_lint.py` checks engine — the check registry).

The catalog of read-only checks `vault_lint.py` runs over agent-shaped MemoryVault entries. Each check is `(entry) -> list[Finding]` where a `Finding` carries `check_id`, `severity` (`error` / `warn` / `info`), `entry_path`, `message`, and a `suggestion`. The lint never mutates the vault — it surfaces candidate fixes for operator review (A3). It targets only entries carrying the core frontmatter trio (`kind` + `status` + `created`); the operator's free-form personal notes are skipped.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What runs the checks? | `harness/skills/memory/scripts/vault_lint.py` (the check registry + runner). |
| How do I see findings? | `python3 harness/skills/memory/scripts/vault_lint.py --format text` (or `--format json`). |
| Which entries get linted? | Only agent-shaped entries (core frontmatter trio `kind`+`status`+`created`); free-form personal notes are skipped (DC-3). |
| Does the lint ever edit the vault? | No. Read-only / surface-only (DC-1). It reports + suggests; the operator applies. Auto-fix is deferred to V5-5. |
| Where does the schema come from? | `save.py` — the lint imports its validators + `FRONTMATTER_FIELD_ORDER` / `REQUIRED_FRONTMATTER_FIELDS` so the two can't drift (DC-2). |
| How do I run a full audit report? | See [Audit the vault](../how-to/Audit-The-Vault.md). |
| Related pages | [Audit the vault](../how-to/Audit-The-Vault.md) |

## Checks

Nine checks run over every agent-shaped entry. Severities: `error` (off-spec — needs a fix) · `warn` (drift or smell — review). The lint exits `0` regardless; findings are advisory.

| Check ID | Severity | What it checks | Suggested-fix shape |
|---|---|---|---|
| `required-field` | error | A required frontmatter field (every field except `supersedes`) is missing. | Add the missing field in the locked order. |
| `kebab-case` | error | `kind` / `slug` / `group` path segments / each `tag` are kebab-case (`^[a-z0-9-]+$`; `group` is `/`-joined kebab segments). | Rename the offending value to kebab-case. |
| `field-order` | warn | The present frontmatter fields appear in the locked order (`kind, status, created, updated, tags, group, slug, always_load, supersedes`). | Reorder frontmatter to the locked order. |
| `slug-filename` | warn | The `slug` value matches the filename stem. | Rename the file to `<slug>.md`, or fix the `slug` field. |
| `date-format` | error / warn | `created` / `updated` are `YYYY-MM-DD` (error if malformed); `updated` is on or after `created` (warn if earlier). | Set a valid date / make `updated` ≥ `created`. |
| `placeholder-value` | warn | A frontmatter value still holds an unfilled template option-list (`a \| b \| c`). | Replace with the single chosen value. |
| `schema-drift` | warn | A frontmatter key is not in the locked schema (unknown key). | Remove the key, or confirm an intentional schema addition. |
| `wikilink-resolution` | error | Every `[[link]]` in the body resolves to a file in the enclosing Obsidian vault (stem- or path-wise, vault-wide). | Fix the target, create the note, or remove the link. |
| `supersede-integrity` | error / warn | `supersedes:` resolves to a real entry (error if dangling); the superseded entry is no longer `active` (warn if still `active`). | Fix the reference / set the target's status to `superseded`. |

Anchor files (`_index`, `_summary`) are exempt from the kebab `slug` check. Bespoke shapes — the idea-incubator `_summary.md` + `Ideas.md` — are skipped entirely (DC-4); a dedicated lint for them is a follow-up. Scheduled / unattended runs are deferred to V6.

## Related

- [Audit the vault](../how-to/Audit-The-Vault.md) — the operator recipe that runs these checks and reads the report.
