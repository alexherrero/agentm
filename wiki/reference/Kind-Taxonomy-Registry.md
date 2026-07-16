# Kind-taxonomy registry + frontmatter validator reference

> [!NOTE]
> **Status: implemented** — `PLAN-v6-15-v6-18-typed-object-moc` ships this reference across four sub-deliverables. All are shipped. [AgentM Memory System](../designs/agentm-memory-system) governs this plan. You can confirm this via `governs_resolver.py`. Note that `agentm-memory-index.md` is a distinct sibling design. It covers the V6-11 SQLite metadata index. It does not cover this plan. Task 1 ships the registry in `kind_registry.py`. Task 2 ships the validator in `frontmatter_validator.py`. Task 3 ships the [MOC generator](MOC-Generator). You can find its own reference page linked here. Task 4 ships the advisory `check-kind-taxonomy` step in `check-all.sh`. You can see the per-section status notes below.

The `kind_registry.py` script formalizes the vault's existing free-form `kind:` frontmatter taxonomy. It provides a recognized-set catalog. It also provides a read-only audit CLI. The `frontmatter_validator.py` script adds a check-only validator on top of this registry. It has the same read-only, report-not-mutate shape as [`vault_lint.py`](Vault-Lint-Checks). It scopes narrowly to the `kind` value and the universal required fields. It does not perform the full nine-check frontmatter sweep.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What enumerates known `kind` values? | `harness/skills/memory/scripts/kind_registry.py` — `known_kinds()` (`kind_registry.py:73`) + `is_known(kind)` (`kind_registry.py:78`). |
| Is the registry a closed enum? | No. It's a recognized-set catalog over a free-form field — an unrecognized `kind` is flagged, never rejected. |
| What checks a single note's frontmatter? | `frontmatter_validator.py`'s `validate(note_path)` (`frontmatter_validator.py:67`) — returns a list of violation strings, empty means clean. |
| Does either script ever rewrite a file? | No — both are read-only by contract. `kind_registry.py`'s `audit()` (`kind_registry.py:101`) is confirmed by `test_audit_never_writes_to_the_vault` in `scripts/test_kind_registry.py:68`; `frontmatter_validator.py`'s `validate()` / `validate_vault()` are confirmed by `test_validate_never_writes_to_the_file` and `test_validate_vault_never_writes_to_any_file` in `scripts/test_frontmatter_validator.py`. |
| How do I run an audit? | `python3 harness/skills/memory/scripts/kind_registry.py audit <vault_path>` — works from any working directory; the `vault` argument is passed straight to `pathlib.Path()` with no cwd-relative resolution (`kind_registry.py:111`). |
| How do I check one note or the whole vault? | `python3 harness/skills/memory/scripts/frontmatter_validator.py --check <path>` (one note) / `--check-vault <vault_path>` (walks `personal/` + `projects/`, excluding the DC-4 dirs) (`frontmatter_validator.py:120-149`). |
| Is this wired into CI as a hard gate? | No — advisory/report-only. See [Advisory kind-taxonomy check](#advisory-kind-taxonomy-check-in-check-allsh) below. |
| Related pages | [Audit the vault](../how-to/Audit-The-Vault) · [Vault lint checks](Vault-Lint-Checks) · [AgentM Memory System](../designs/agentm-memory-system) |

## Registry (task 1)

> [!NOTE]
> **Status: implemented** — You shipped this in `harness/skills/memory/scripts/kind_registry.py`. You covered it with 13 tests in `scripts/test_kind_registry.py`. The test classes are `TestKnownKinds`, `TestIsKebab`, and `TestAudit`.

The `kind_registry.py` script (`kind_registry.py:1`) is a stdlib-only module. It contains `KNOWN_KINDS` (`kind_registry.py:33-52`, a `frozenset[str]`). You seed this set from two sources. The first source is reserved values that shipped code already references. These are `failure-incident`, `session-cost`, and `crystallized`. The second source is a frequency audit of the real vault's `personal/` and `projects/` trees. You took this audit at authoring time on 2026-07-10.

The module keeps near-duplicate values as **separate, distinct entries deliberately**. For example, both `convention` and `conventions` are known kinds. You must make an explicit operator judgment call to canonicalize near-synonyms. You parked this task as its own backlog item in [agentm issue #273](https://github.com/alexherrero/agentm/issues/273). This module does not decide canonicalization.

The `REQUIRED_UNIVERSAL_FIELDS` tuple (`kind_registry.py:59-61`, a `tuple[str, ...]`: `kind, status, created, updated, tags, group, slug`) also exists in this module. The `frontmatter_validator.validate()` function now consumes it directly. You can see task 2 below.

Shipped surface:

| Function / mode | Signature | Purpose |
|---|---|---|
| `is_kebab(value)` | `is_kebab(value: str) -> bool` (`kind_registry.py:68`) | True iff `value` matches the kebab-case shape `save.py` itself enforces (`^[a-z0-9-]+$`). |
| `known_kinds()` | `known_kinds() -> frozenset[str]` (`kind_registry.py:73`) | Returns `KNOWN_KINDS`, the recognized-set catalog. |
| `is_known(kind)` | `is_known(kind: str) -> bool` (`kind_registry.py:78`) | Exact-match, case-sensitive lookup against `KNOWN_KINDS` — a differently-cased duplicate (e.g. `Fix`) is a distinct, unrecognized value by design. |
| `audit(vault_path)` | `audit(vault_path: Path \| str) -> dict` (`kind_registry.py:101`) | Read-only scan of a vault's `kind:` values. Returns `{"by_kind": {kind: count}, "malformed": [(path, raw_kind)], "unrecognized": [(path, raw_kind)], "total_files": int}`. Walks `personal/`, `projects/`, `_idea-incubator/` (`_WALK_SUBDIRS`, `kind_registry.py:65`), mirroring `vec_index.py`'s `full_sync` walk roots; skips `_archive/` dirs and `PLAN.archive.*` files. `"malformed"` is a raw value failing `is_kebab()`; `"unrecognized"` is valid kebab-case but absent from `KNOWN_KINDS`. A note with no extractable `kind:` at all still counts toward `total_files` but appears in no other bucket. |
| CLI: `audit <vault>` | `python3 harness/skills/memory/scripts/kind_registry.py audit <vault_path>` (`kind_registry.py:166-183`) | Runs `audit()` against the given vault path and prints a human-readable report (`_print_report`, `kind_registry.py:151`) — total files scanned, known-kind counts by frequency, then unrecognized and malformed lists. |

You reserved two kinds in [AgentM Memory Index](../designs/agentm-memory-index). These are `session-cost` and `failure-incident`. They are recognized values in this same catalog. You do not keep them in a separate list.

## Validator (task 2)

> [!NOTE]
> **Status: implemented** — You shipped this in `harness/skills/memory/scripts/frontmatter_validator.py`. You covered it with 13 tests in `scripts/test_frontmatter_validator.py`. The test classes are `TestValidateSingleNote` and `TestValidateVault`.

The `validate(note_path)` function (`frontmatter_validator.py:67`, signature `validate(note_path: Path | str) -> list[str]`) checks one note's frontmatter. It returns a list of violation strings. An empty list means the note is clean. It never writes to `note_path`. It runs the following checks:

- It verifies `kind` is a known value. It uses `kind_registry.is_known` (`kind_registry.py:78`). It flags unrecognized kinds as `"kind {kind!r} is not a recognized kind (unrecognized, not rejected)"`. It does not silently accept or hard-reject them.
- It verifies `kind` is valid kebab-case. It uses `kind_registry.is_kebab` (`kind_registry.py:68`). It flags malformed kinds as `"kind {kind!r} is not valid kebab-case"`.
- It verifies the universal required fields are present. It checks against `kind_registry.REQUIRED_UNIVERSAL_FIELDS` (`kind_registry.py:59-61`). It imports this directly rather than re-deriving it. It returns one `"missing required field \`{field_name}\`"` violation per absent field.
- It checks if a note has a frontmatter block. The text must open with a `---` delimiter pair. If missing, it returns `["no frontmatter block found"]`. It uses a minimal stdlib-only `_parse_frontmatter()` (`frontmatter_validator.py:38-64`). This mirrors the parse contract of `vault_lint.py`. It reads key-value pairs without nested structures or PyYAML.

The `validate_vault(vault_path, *, scope_dirs=("personal", "projects"))` function (`frontmatter_validator.py:95`, signature `validate_vault(vault_path: Path | str, *, scope_dirs=_DEFAULT_SCOPE_DIRS) -> dict[str, list[str]]`) walks every `*.md` file under the vault's scope dirs. It returns `{rel_path: [violations]}` for notes with at least one violation. It omits clean notes entirely. It skips `_archive/` dirs and `PLAN.archive.*` files. This matches the `audit()` walk from `kind_registry.py`.

**DC-4 exclusion dirs.** The `_EXCLUDE_DIRS` constant (`frontmatter_validator.py:35`, a `frozenset`: `_idea-incubator, _meta, _harness, _inbox, _dream-staging`) mirrors the `_EXCLUDE_DIRS` constant in `vault_lint.py` exactly. This is the DC-4 convention. These subdirectories carry non-memory-entry content. This includes harness state, dev-loop infra, and staging areas. You never meant for these to satisfy the universal frontmatter contract. This exclusion prevents false positives. The first draft's walk lacked it. A real-vault run flooded about 1800 false `"no frontmatter block found"` violations. These targeted plain harness state files like `projects/<repo>/_harness/PLAN.md` and `progress.md`. These files carry no frontmatter by design. You wrote `test_excludes_harness_meta_inbox_dream_staging_dirs` in `scripts/test_frontmatter_validator.py:119-132` as the regression test for that bug.

You can run the CLI (`frontmatter_validator.py:120-153`). The `--check <path>` flag validates one note. It prints its violations and exits with 1. It prints `<path>: clean` and exits with 0 on success. The `--check-vault <vault_path>` flag runs `validate_vault()` over the whole vault. It prints `<rel_path>:` and violation lines for each dirty note. It prints `clean: no violations found` and exits with 0 when it finds no issues. The two flags are mutually exclusive. You must provide one.

## How this differs from `vault_lint.py`

| | `vault_lint.py` | `kind_registry.py` + `frontmatter_validator.py` (both shipped) |
|---|---|---|
| Scope | Nine checks across the full frontmatter shape (field order, dates, wikilinks, supersede integrity, etc.) | `kind` value + the universal required-field trio only |
| `kind` handling | Assumes `kind` is already valid; doesn't catalog known values | The catalog itself — known vs. unrecognized, kebab-case malformed (`known_kinds()`, `is_known()`, `is_kebab()`), consumed directly by `frontmatter_validator.validate()` |
| Mutation | Read-only, report-only | Read-only, report-only (same contract; confirmed for `kind_registry.py` by `test_audit_never_writes_to_the_vault`, and for `frontmatter_validator.py` by `test_validate_never_writes_to_the_file` / `test_validate_vault_never_writes_to_any_file`) |
| Schema source | Imports `save.py`'s `FRONTMATTER_FIELD_ORDER` / `REQUIRED_FRONTMATTER_FIELDS` | `kind_registry.py` keeps its own standalone `REQUIRED_UNIVERSAL_FIELDS` tuple (no import-time dependency on `save.py`); `frontmatter_validator.py` imports it directly |
| Exclusion dirs | N/A | `_EXCLUDE_DIRS` (`frontmatter_validator.py:35`) mirrors `vault_lint.py`'s own `_EXCLUDE_DIRS` exactly (DC-4): `_idea-incubator, _meta, _harness, _inbox, _dream-staging` |

You should point operators to [Audit the vault](../how-to/Audit-The-Vault). This page explains which tool answers "is this note's `kind` legitimate" versus "is this note's frontmatter shape correct."

## Downstream consumer

The [MOC generator](MOC-Generator) (task 3, V6-18) depends on this registry. It uses the registry to label each generated Map-of-Content page's group as a known kind or an unrecognized one.

## Advisory kind-taxonomy check in check-all.sh (task 4)

You wired a `check-kind-taxonomy` step (`scripts/check-kind-taxonomy.sh`) into `scripts/check-all.sh`. This runs the task-1 `audit()` against `$MEMORY_VAULT_PATH` when you set it. It performs a graceful skip (exit 0) when unset or not a directory. This follows the pattern of other vault-dependent checks. It acts like the existing `check-slop.py --report wiki` step. It is **report-only, never a failing gate**. You cannot use a hard PASS/FAIL on the real vault's 47-value mess yet. You must first make an operator judgment call on normalization. You parked this in [agentm issue #273](https://github.com/alexherrero/agentm/issues/273). The script prints violations as a report. The step's own exit code stays 0 regardless of findings. You confirmed this via subprocess-driven tests in `scripts/test_check_kind_taxonomy.py`. These tests verify an unset path, nonexistent path, clean vault, and a vault with real violations all exit 0. You can read the gate table entry in [CI Gates](CI-Gates).

## Related

- [Audit the vault](../how-to/Audit-The-Vault) — This provides the operator recipe for the sibling `vault_lint.py` tool.
- [Vault lint checks reference](Vault-Lint-Checks) — This lists the nine-check catalog. This plan's registry and validator complement it rather than duplicate it.
- [AgentM Memory System](../designs/agentm-memory-system) — This provides the governing design (V6-15).
- [AgentM Memory Index](../designs/agentm-memory-index) — This sibling design reserves the `session-cost` and `failure-incident` kinds. You can refer to [Registry (task 1)](#registry-task-1) above.
- [CI Gates](CI-Gates) — This documents the advisory `check-kind-taxonomy` step once shipped.
