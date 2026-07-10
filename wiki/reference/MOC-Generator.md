# MOC generator reference

> [!NOTE]
> **Status: implemented** — shipped in `harness/skills/memory/scripts/moc_generator.py` (`PLAN-v6-15-v6-18-typed-object-moc` task 3, V6-18), governed by [AgentM Memory Index](../designs/agentm-memory-index). Covered by 12 tests in `scripts/test_moc_generator.py` (`TestBuildKindGroups`, `TestGenerate`).

> [!IMPORTANT]
> **Not yet run against the real vault.** Unlike task 1/2's read-only audits, `generate()` actually writes new files — one page per distinct `kind`, roughly 40 at the real vault's current kind-frequency spread — into `<vault>/_moc/`. That's a visible side effect inside the operator's personal Obsidian vault, so it was deliberately left for the operator to trigger via the CLI rather than run silently as part of this build task. See [Running it for the first time](#running-it-for-the-first-time) below.

`moc_generator.py` builds browse-first MOCs (Maps of Content) over the vault — one generated Markdown page per `kind`, each listing wikilinks to every note of that kind, so an operator can browse a kind's entries in Obsidian without a search. It depends on the [kind-taxonomy registry](Kind-Taxonomy-Registry) (task 1) to label each group known vs. unrecognized.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What generates the MOCs? | `harness/skills/memory/scripts/moc_generator.py` — `build_kind_groups(vault_path)` (`moc_generator.py:89`) + `generate(vault_path)` (`moc_generator.py:116`). |
| How do I run it? | `python3 harness/skills/memory/scripts/moc_generator.py --vault <path>` (CLI-invokable; no hook or scheduling wiring in this plan). |
| Where do the generated pages live? | `<vault>/_moc/<kind>.md` — one page per distinct `kind` (`_OUTPUT_DIRNAME`, `moc_generator.py:30`). |
| Does it read the whole vault, or something narrower? | The same read-only walk `vec_index.py`'s `full_sync` uses — `personal/`, `projects/`, `_idea-incubator/` (`_WALK_SUBDIRS`, `moc_generator.py:28`). Deliberately wider than `frontmatter_validator.py`'s DC-4-exempt walk (task 2) — MOCs should cover everything the memory engine indexes, incubator included. |
| Is it safe to re-run? | Yes — idempotent. Regenerating overwrites only the `_moc/*.md` pages it owns; it never touches source notes. Confirmed byte-identical by `test_idempotent_regeneration_is_byte_identical` and never-mutates-sources by `test_never_touches_source_notes` (`scripts/test_moc_generator.py`). |
| What order are entries listed in? | Newest-first by `created`, within each kind group. |
| Does it label unrecognized kinds? | Yes, via the [kind-taxonomy registry](Kind-Taxonomy-Registry)'s `is_known()` — an unrecognized kind's page header reads `<kind> (unrecognized kind)`. |
| What happens to a malformed (non-kebab) `kind` value? | Skipped entirely — no page is written for it. See [Malformed kinds are skipped](#malformed-kinds-are-skipped-not-flagged) below. |
| Has this been run against the real vault yet? | No — see the callout above. |
| Related pages | [Kind-taxonomy registry](Kind-Taxonomy-Registry) · [AgentM Memory Index](../designs/agentm-memory-index) |

## Shipped surface

| Function | Signature | Purpose |
|---|---|---|
| `build_kind_groups(vault_path)` | `build_kind_groups(vault_path: Path \| str) -> dict[str, list[tuple[str, str, dict]]]` (`moc_generator.py:89`) | Read-only scan. Returns `{kind: [(rel_path_str, created, fm), ...]}`, sorted newest-first by `created` within each group. Groups by the raw frontmatter `kind` value with no filtering — including unrecognized or malformed values; `generate()` is what decides what to render. |
| `_render_moc(kind, entries)` | `_render_moc(kind: str, entries: list[tuple[str, str, dict]]) -> str` (`moc_generator.py:108`) | Renders one kind's page body: an `# MOC — <kind>` (or `<kind> (unrecognized kind)`) header, an entry count, then one `- [[slug]]` line per entry, newest-first. |
| `generate(vault_path)` | `generate(vault_path: Path \| str) -> list[str]` (`moc_generator.py:116`) | Writes one page per kind under `<vault>/_moc/<kind>.md`. Returns the list of kind values a page was actually written for (malformed kinds excluded). |
| CLI | `python3 harness/skills/memory/scripts/moc_generator.py --vault <path>` (`moc_generator.py:141-153`) | Runs `generate()` against the given vault path and prints a summary line plus one line per kind page written. |

## Wikilink target and page shape

Each bullet links to the bare slug, matching the real vault's own existing MOC convention at `personal/preferences/_index.md` — `[[slug]]`, not a full relative path (`_wikilink_target`, `moc_generator.py:80-86`). The target is the note's `slug:` frontmatter value, falling back to the file's stem if `slug` is absent.

A rendered page looks like:

```markdown
# MOC — fix

[← wiki Home](https://github.com/alexherrero/agentm/wiki/Home)

2 entries, newest-first by `created`.

- [[new-slug]]
- [[old-slug]]
```

The Home-backlink (CONS-1) is a plain markdown link, not an Obsidian wikilink — the vault has no "Home" note of its own to link to, so each generated MOC page instead orients the operator back to the project's actual documentation entry point.

An unrecognized kind's header instead reads `# MOC — made-up-kind (unrecognized kind)` (`_render_moc`, `moc_generator.py:109`), using the [kind-taxonomy registry](Kind-Taxonomy-Registry)'s `is_known()`.

## Walk roots — deliberately wider than the validator's (task 2)

`_WALK_SUBDIRS` (`moc_generator.py:28`) is `("personal", "projects", "_idea-incubator")` — matching `vec_index.py`'s `full_sync` walk exactly. This is a deliberate difference from `frontmatter_validator.py`'s narrower DC-4-exempt walk (task 2, which excludes `_idea-incubator` among other dirs): browse-first MOCs should cover everything the memory engine actually indexes, incubator included. `test_includes_idea_incubator` in `scripts/test_moc_generator.py:56-64` is the regression test for this.

The walk also skips any path with an `_archive` or `_moc` path segment (`_walk_notes`, `moc_generator.py:56-65`), so a regeneration never folds its own prior output back in as a source note, and skips `PLAN.archive.*` files, mirroring `kind_registry.py`'s own walk excludes.

## Malformed kinds are skipped, not flagged

`generate()` calls `is_kebab(kind)` (from `kind_registry.py`) per group and silently omits any group whose kind fails the kebab-case shape check — no page is written, and no error is raised (`moc_generator.py:132-134`). A MOC filename must itself be a legal kebab-case name, and a malformed kind has no other legitimate slot to file under. Flagging a malformed value for a human to fix is `kind_registry.py`'s `audit()` job (task 1), not this generator's. `test_malformed_kind_is_skipped_not_crashed` in `scripts/test_moc_generator.py:118-131` confirms both the empty return and that `_moc/` itself is never created when every group is malformed.

An **unrecognized** (valid kebab-case, just not in `KNOWN_KINDS`) kind is different — it still gets a page, just labeled `(unrecognized kind)` in the header. `test_unrecognized_kind_still_gets_a_page` (`scripts/test_moc_generator.py:133-140`) is the regression test for that distinction.

## Running it for the first time

> [!IMPORTANT]
> This has not yet been run against the real vault. `generate()` writes real files — creating roughly 40 new pages under `<vault>/_moc/` is a visible, non-trivial change to the operator's personal Obsidian vault, and running it is the operator's own call to make, not something to trigger silently as part of a build task.

To run it:

```bash
python3 harness/skills/memory/scripts/moc_generator.py --vault <path-to-vault>
```

This prints a summary line (`wrote N MOC page(s) under <vault>/_moc`) followed by one line per kind page written. Re-running is safe at any time — regeneration only overwrites the `_moc/*.md` pages this module owns and never touches a source note (see the idempotency row in the Quick Reference above).

## Scope boundaries (this plan)

- **CLI-invokable only.** No hook wiring, no scheduled/automatic regeneration. A follow-up once the generator proves useful in practice.
- **No pagination.** High-frequency kinds (`preferences` alone was 993 notes at the plan's frequency audit) will produce a single long MOC page with no pagination design — a named follow-up, not solved here.
- **Read-only over source notes.** The generator can overwrite its own `_moc/*.md` output; it never mutates a note it's cataloging.

## Related

- [Kind-taxonomy registry](Kind-Taxonomy-Registry) — the known/unrecognized-kind labeling this generator depends on.
- [AgentM Memory Index](../designs/agentm-memory-index) — the governing design (V6-18).
- [Audit the vault](../how-to/Audit-The-Vault) — the sibling read-only vault tool pattern this generator follows.
