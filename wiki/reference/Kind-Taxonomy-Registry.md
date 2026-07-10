# Kind-taxonomy registry + frontmatter validator reference

> [!NOTE]
> **Status: implemented** — `PLAN-v6-15-v6-18-typed-object-moc` (governed by [AgentM Memory Index](../designs/agentm-memory-index)) ships this reference across four sub-deliverables, all shipped. Task 1, the registry (`kind_registry.py`). Task 2, the validator (`frontmatter_validator.py`). Task 3, the [MOC generator](MOC-Generator) (its own reference page). Task 4, the advisory `check-kind-taxonomy` step in `check-all.sh`. See the per-section status notes below.

`kind_registry.py` formalizes the vault's existing free-form `kind:` frontmatter taxonomy into a recognized-set catalog + a read-only audit CLI. `frontmatter_validator.py` adds a check-only validator on top of it — the same read-only, report-not-mutate shape as [`vault_lint.py`](Vault-Lint-Checks), scoped narrowly to the `kind` value and the universal required fields rather than the full nine-check frontmatter sweep.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What enumerates known `kind` values? | `harness/skills/memory/scripts/kind_registry.py` — `known_kinds()` (`kind_registry.py:67`) + `is_known(kind)` (`kind_registry.py:72`). |
| Is the registry a closed enum? | No. It's a recognized-set catalog over a free-form field — an unrecognized `kind` is flagged, never rejected. |
| What checks a single note's frontmatter? | `frontmatter_validator.py`'s `validate(note_path)` (`frontmatter_validator.py:67`) — returns a list of violation strings, empty means clean. |
| Does either script ever rewrite a file? | No — both are read-only by contract. `kind_registry.py`'s `audit()` (`kind_registry.py:95`) is confirmed by `test_audit_never_writes_to_the_vault` in `scripts/test_kind_registry.py:68`; `frontmatter_validator.py`'s `validate()` / `validate_vault()` are confirmed by `test_validate_never_writes_to_the_file` and `test_validate_vault_never_writes_to_any_file` in `scripts/test_frontmatter_validator.py`. |
| How do I run an audit? | `python3 harness/skills/memory/scripts/kind_registry.py audit <vault_path>` — works from any working directory; the `vault` argument is passed straight to `pathlib.Path()` with no cwd-relative resolution (`kind_registry.py:164-165`). |
| How do I check one note or the whole vault? | `python3 harness/skills/memory/scripts/frontmatter_validator.py --check <path>` (one note) / `--check-vault <vault_path>` (walks `personal/` + `projects/`, excluding the DC-4 dirs) (`frontmatter_validator.py:120-149`). |
| Is this wired into CI as a hard gate? | No — advisory/report-only. See [Advisory kind-taxonomy check](#advisory-kind-taxonomy-check-in-check-allsh) below. |
| Related pages | [Audit the vault](../how-to/Audit-The-Vault) · [Vault lint checks](Vault-Lint-Checks) · [AgentM Memory Index](../designs/agentm-memory-index) |

## Registry (task 1)

> [!NOTE]
> **Status: implemented** — shipped in `harness/skills/memory/scripts/kind_registry.py`, covered by 13 tests in `scripts/test_kind_registry.py` (`TestKnownKinds`, `TestIsKebab`, `TestAudit`).

`kind_registry.py` (`kind_registry.py:1`) is a stdlib-only module. `KNOWN_KINDS` (`kind_registry.py:33-46`, a `frozenset[str]`) is seeded from two sources: reserved values shipped code already references (`failure-incident`, `session-cost`, `crystallized`), and a frequency audit of the real vault's `personal/` + `projects/` trees taken at authoring time (2026-07-10). Near-duplicate values are kept as **separate, distinct entries deliberately** — for example both `convention` and `conventions` are known kinds — because canonicalizing near-synonyms is an explicit operator judgment call parked as its own backlog item, [agentm issue #273](https://github.com/alexherrero/agentm/issues/273), not decided by this module.

`REQUIRED_UNIVERSAL_FIELDS` (`kind_registry.py:59-65`, a `tuple[str, ...]`: `kind, status, created, updated, tags, group, slug`) also exists in this module; it is now consumed directly by `frontmatter_validator.validate()` (task 2, see below).

Shipped surface:

| Function / mode | Signature | Purpose |
|---|---|---|
| `is_kebab(value)` | `is_kebab(value: str) -> bool` (`kind_registry.py:62`) | True iff `value` matches the kebab-case shape `save.py` itself enforces (`^[a-z0-9-]+$`). |
| `known_kinds()` | `known_kinds() -> frozenset[str]` (`kind_registry.py:67`) | Returns `KNOWN_KINDS`, the recognized-set catalog. |
| `is_known(kind)` | `is_known(kind: str) -> bool` (`kind_registry.py:72`) | Exact-match, case-sensitive lookup against `KNOWN_KINDS` — a differently-cased duplicate (e.g. `Fix`) is a distinct, unrecognized value by design. |
| `audit(vault_path)` | `audit(vault_path: Path \| str) -> dict` (`kind_registry.py:95`) | Read-only scan of a vault's `kind:` values. Returns `{"by_kind": {kind: count}, "malformed": [(path, raw_kind)], "unrecognized": [(path, raw_kind)], "total_files": int}`. Walks `personal/`, `projects/`, `_idea-incubator/` (`kind_registry.py:59`), mirroring `vec_index.py`'s `full_sync` walk roots; skips `_archive/` dirs and `PLAN.archive.*` files. `"malformed"` is a raw value failing `is_kebab()`; `"unrecognized"` is valid kebab-case but absent from `KNOWN_KINDS`. A note with no extractable `kind:` at all still counts toward `total_files` but appears in no other bucket. |
| CLI: `audit <vault>` | `python3 harness/skills/memory/scripts/kind_registry.py audit <vault_path>` (`kind_registry.py:160-173`) | Runs `audit()` against the given vault path and prints a human-readable report (`_print_report`, `kind_registry.py:145`) — total files scanned, known-kind counts by frequency, then unrecognized and malformed lists. |

The two kinds reserved by [AgentM Memory Index](../designs/agentm-memory-index) — `session-cost` and `failure-incident` — are recognized values in this same catalog, not a separate list.

## Validator (task 2)

> [!NOTE]
> **Status: implemented** — shipped in `harness/skills/memory/scripts/frontmatter_validator.py`, covered by 13 tests in `scripts/test_frontmatter_validator.py` (`TestValidateSingleNote`, `TestValidateVault`).

`validate(note_path)` (`frontmatter_validator.py:67`, signature `validate(note_path: Path | str) -> list[str]`) checks one note's frontmatter and returns a list of violation strings (empty = clean). It never writes to `note_path`:

- `kind` is a known value (via `kind_registry.is_known`, `kind_registry.py:72`), flagged as `"kind {kind!r} is not a recognized kind (unrecognized, not rejected)"` rather than silently accepted or hard-rejected.
- `kind` is valid kebab-case (via `kind_registry.is_kebab`, `kind_registry.py:62`), flagged as `"kind {kind!r} is not valid kebab-case"` when malformed.
- The universal required fields are present, checked against `kind_registry.REQUIRED_UNIVERSAL_FIELDS` (`kind_registry.py:59-65`, imported directly rather than re-derived) — one `"missing required field \`{field_name}\`"` violation per absent field.
- A note with no frontmatter block at all (text doesn't open with a `---` delimiter pair) returns `["no frontmatter block found"]` via a minimal stdlib-only `_parse_frontmatter()` (`frontmatter_validator.py:38-64`) that mirrors `vault_lint.py`'s parse contract (key: raw-value pairs, no nested structures, no PyYAML).

`validate_vault(vault_path, *, scope_dirs=("personal", "projects"))` (`frontmatter_validator.py:95`, signature `validate_vault(vault_path: Path | str, *, scope_dirs=_DEFAULT_SCOPE_DIRS) -> dict[str, list[str]]`) walks every `*.md` file under the vault's scope dirs and returns `{rel_path: [violations]}` for notes with at least one violation — clean notes are omitted entirely. It skips `_archive/` dirs and `PLAN.archive.*` files, matching `kind_registry.py`'s `audit()` walk.

**DC-4 exclusion dirs.** `_EXCLUDE_DIRS` (`frontmatter_validator.py:35`, a `frozenset`: `_idea-incubator, _meta, _harness, _inbox, _dream-staging`) mirrors `vault_lint.py`'s own `_EXCLUDE_DIRS` exactly (the DC-4 convention). These subdirectories carry non-memory-entry content — harness state, dev-loop infra, staging areas — that was never meant to satisfy the universal frontmatter contract. This exclusion is load-bearing: the first draft's walk didn't apply it, and a real-vault run flooded ~1800 false `"no frontmatter block found"` violations against plain harness state files like `projects/<repo>/_harness/PLAN.md` and `progress.md`, which carry no frontmatter at all by design. `test_excludes_harness_meta_inbox_dream_staging_dirs` in `scripts/test_frontmatter_validator.py:119-132` is the regression test for that bug.

CLI (`frontmatter_validator.py:120-153`): `--check <path>` validates one note and prints its violations (exit 1) or `<path>: clean` (exit 0); `--check-vault <vault_path>` runs `validate_vault()` over the whole vault and prints `<rel_path>:` + violation lines per dirty note, or `clean: no violations found` (exit 0) when nothing is flagged. The two flags are mutually exclusive and one is required.

## How this differs from `vault_lint.py`

| | `vault_lint.py` | `kind_registry.py` + `frontmatter_validator.py` (both shipped) |
|---|---|---|
| Scope | Nine checks across the full frontmatter shape (field order, dates, wikilinks, supersede integrity, etc.) | `kind` value + the universal required-field trio only |
| `kind` handling | Assumes `kind` is already valid; doesn't catalog known values | The catalog itself — known vs. unrecognized, kebab-case malformed (`known_kinds()`, `is_known()`, `is_kebab()`), consumed directly by `frontmatter_validator.validate()` |
| Mutation | Read-only, report-only | Read-only, report-only (same contract; confirmed for `kind_registry.py` by `test_audit_never_writes_to_the_vault`, and for `frontmatter_validator.py` by `test_validate_never_writes_to_the_file` / `test_validate_vault_never_writes_to_any_file`) |
| Schema source | Imports `save.py`'s `FRONTMATTER_FIELD_ORDER` / `REQUIRED_FRONTMATTER_FIELDS` | `kind_registry.py` keeps its own standalone `REQUIRED_UNIVERSAL_FIELDS` tuple (no import-time dependency on `save.py`); `frontmatter_validator.py` imports it directly |
| Exclusion dirs | N/A | `_EXCLUDE_DIRS` (`frontmatter_validator.py:35`) mirrors `vault_lint.py`'s own `_EXCLUDE_DIRS` exactly (DC-4): `_idea-incubator, _meta, _harness, _inbox, _dream-staging` |

[Audit the vault](../how-to/Audit-The-Vault) is the natural place to point operators at whichever tool answers "is this note's `kind` legitimate" versus "is this note's frontmatter shape correct."

## Downstream consumer

The [MOC generator](MOC-Generator) (task 3, V6-18) depends on this registry to label each generated Map-of-Content page's group as a known kind or an unrecognized one.

## Advisory kind-taxonomy check in check-all.sh (task 4)

A `check-kind-taxonomy` step (`scripts/check-kind-taxonomy.sh`) is wired into `scripts/check-all.sh`, running the task-1 `audit()` against `$MEMORY_VAULT_PATH` when it's set — graceful-skip (exit 0) when unset or not a directory, the same pattern other vault-dependent checks use. Like the existing `check-slop.py --report wiki` step, it is **report-only, never a failing gate**: the real vault's 47-value mess can't be a hard PASS/FAIL yet without an operator judgment call on normalization (parked as [agentm issue #273](https://github.com/alexherrero/agentm/issues/273)), so violations print as a report and the step's own exit code stays 0 regardless of findings — confirmed by `scripts/test_check_kind_taxonomy.py`'s subprocess-driven tests (unset path, nonexistent path, clean vault, and a vault with real violations all exit 0). See [CI Gates](CI-Gates) for the gate table entry.

## Related

- [Audit the vault](../how-to/Audit-The-Vault) — the operator recipe for the sibling `vault_lint.py` tool.
- [Vault lint checks reference](Vault-Lint-Checks) — the nine-check catalog this plan's registry+validator complements rather than duplicates.
- [AgentM Memory Index](../designs/agentm-memory-index) — the governing design (V6-15).
- [CI Gates](CI-Gates) — where the advisory `check-kind-taxonomy` step will be documented once shipped.
