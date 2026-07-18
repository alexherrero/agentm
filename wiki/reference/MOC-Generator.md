# MOC generator reference

> [!NOTE]
> **Status: implemented** — You can find this shipped in `harness/skills/memory/scripts/moc_generator.py` (`PLAN-v6-15-v6-18-typed-object-moc` task 3, V6-18). [AgentM Memory System](../designs/agentm-memory-system) governs this. 13 tests in `scripts/test_moc_generator.py` (`TestBuildKindGroups`, `TestGenerate`) cover this.

> [!IMPORTANT]
> **Not yet run against the real vault.** You have not run this against the real vault yet. Unlike task 1/2's read-only audits, `generate()` writes new files. It writes one page per distinct `kind` into `<vault>/_moc/`. This totals roughly 40 files at the real vault's current kind-frequency spread. This is a visible side effect inside your personal Obsidian vault. You trigger this via the CLI. You do not run it silently as part of this build task. See [Running it for the first time](#running-it-for-the-first-time) below.

`moc_generator.py` builds browse-first MOCs (Maps of Content) over the vault. You get one generated Markdown page per `kind`. Each page lists wikilinks to every note of that kind. This lets you browse a kind's entries in Obsidian without a search. It depends on the [kind-taxonomy registry](Kind-Taxonomy-Registry) (task 1) to label each group known vs. unrecognized.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What generates the MOCs? | `harness/skills/memory/scripts/moc_generator.py` — `build_kind_groups(vault_path)` (`moc_generator.py:89`) + `generate(vault_path)` (`moc_generator.py:136`). |
| How do I run it? | `python3 harness/skills/memory/scripts/moc_generator.py --vault <path>` (CLI-invokable; no hook or scheduling wiring in this plan). |
| Where do the generated pages live? | `<vault>/_moc/<kind>.md` — one page per distinct `kind` (`_OUTPUT_DIRNAME`, `moc_generator.py:30`). |
| Does it read the whole vault, or something narrower? | The same read-only walk `vec_index.py`'s `full_sync` uses — `personal/`, `projects/`, `_idea-incubator/` (`_WALK_SUBDIRS`, `moc_generator.py:28`). Deliberately wider than `frontmatter_validator.py`'s DC-4-exempt walk (task 2) — MOCs should cover everything the memory engine indexes, incubator included. |
| Is it safe to re-run? | Yes — idempotent. Regenerating overwrites only the `_moc/*.md` pages it owns; it never touches source notes. Confirmed byte-identical by `test_idempotent_regeneration_is_byte_identical` and never-mutates-sources by `test_never_touches_source_notes` (`scripts/test_moc_generator.py`). |
| What order are entries listed in? | Newest-first by `created`, within each kind group. |
| Does it label unrecognized kinds? | Yes, via the [kind-taxonomy registry](Kind-Taxonomy-Registry)'s `is_known()` — an unrecognized kind's page header reads `<kind> (unrecognized kind)`. |
| What happens to a malformed (non-kebab) `kind` value? | Skipped entirely — no page is written for it. See [Malformed kinds are skipped](#malformed-kinds-are-skipped-not-flagged) below. |
| Has this been run against the real vault yet? | No — see the callout above. |
| Related pages | [Kind-taxonomy registry](Kind-Taxonomy-Registry) · [AgentM Memory System](../designs/agentm-memory-system) |

## Shipped surface

| Function | Signature | Purpose |
|---|---|---|
| `build_kind_groups(vault_path)` | `build_kind_groups(vault_path: Path \| str) -> dict[str, list[tuple[str, str, dict]]]` (`moc_generator.py:89`) | Read-only scan. Returns `{kind: [(rel_path_str, created, fm), ...]}`, sorted newest-first by `created` within each group. Groups by the raw frontmatter `kind` value with no filtering — including unrecognized or malformed values; `generate()` is what decides what to render. |
| `_render_moc(kind, entries)` | `_render_moc(kind: str, entries: list[tuple[str, str, dict]]) -> str` (`moc_generator.py:111`) | Renders one kind's page body: an `# MOC — <kind>` (or `<kind> (unrecognized kind)`) header, an entry count, then one `- [[slug]]` line per entry, newest-first. |
| `generate(vault_path)` | `generate(vault_path: Path \| str) -> list[str]` (`moc_generator.py:136`) | Writes one page per kind under `<vault>/_moc/<kind>.md`. Returns the list of kind values a page was actually written for (malformed kinds excluded). |
| CLI | `python3 harness/skills/memory/scripts/moc_generator.py --vault <path>` (`moc_generator.py:161-177`) | Runs `generate()` against the given vault path and prints a summary line plus one line per kind page written. |

## Wikilink target and page shape

Each bullet links to the bare slug. This matches the real vault's existing MOC convention at `personal/preferences/_index.md`. It uses `[[slug]]` rather than a full relative path (`_wikilink_target`, `moc_generator.py:80-86`). The target is the note's `slug:` frontmatter value. It falls back to the file's stem if `slug` is absent.

A rendered page looks like:

```markdown
# MOC — fix

[← wiki Home](https://github.com/alexherrero/agentm/wiki/Home)

2 entries, newest-first by `created`.

- [[new-slug]]
- [[old-slug]]
```

The Home-backlink (CONS-1) is a plain markdown link instead of an Obsidian wikilink. The vault has no "Home" note of its own to link to. Each generated MOC page orients you back to the project's actual documentation entry point instead.

An unrecognized kind's header instead reads `# MOC — made-up-kind (unrecognized kind)` (`_render_moc`, `moc_generator.py:112`). This uses the [kind-taxonomy registry](Kind-Taxonomy-Registry)'s `is_known()`.

## Walk roots — deliberately wider than the validator's (task 2)

`_WALK_SUBDIRS` (`moc_generator.py:28`) is `("personal", "projects", "_idea-incubator")`. This matches `vec_index.py`'s `full_sync` walk exactly. This is a deliberate difference from `frontmatter_validator.py`'s narrower DC-4-exempt walk (task 2, which excludes `_idea-incubator` among other dirs). Browse-first MOCs cover everything the memory engine actually indexes. This includes the incubator. `test_includes_idea_incubator` in `scripts/test_moc_generator.py:56-64` is the regression test for this.

The walk also skips any path with an `_archive` or `_moc` path segment (`_walk_notes`, `moc_generator.py:56-65`). This prevents a regeneration from folding its own prior output back in as a source note. It also skips `PLAN.archive.*` files. This mirrors `kind_registry.py`'s own walk excludes.

## Malformed kinds are skipped, not flagged

`generate()` calls `is_kebab(kind)` (from `kind_registry.py`) per group. It silently omits any group whose kind fails the kebab-case shape check. No page is written. No error is raised (`moc_generator.py:153`). A MOC filename must itself be a legal kebab-case name. A malformed kind has no other legitimate slot to file under. Flagging a malformed value for a human to fix is `kind_registry.py`'s `audit()` job (task 1), not this generator's. `test_malformed_kind_is_skipped_not_crashed` in `scripts/test_moc_generator.py:149-162` confirms both the empty return and that `_moc/` itself is never created when every group is malformed.

An **unrecognized** (valid kebab-case, just not in `KNOWN_KINDS`) kind is different. It still gets a page. It gets labeled `(unrecognized kind)` in the header. `test_unrecognized_kind_still_gets_a_page` (`scripts/test_moc_generator.py:164-171`) is the regression test for that distinction.

## Running it for the first time

> [!IMPORTANT]
> You have not yet run this against the real vault. `generate()` writes real files. Creating roughly 40 new pages under `<vault>/_moc/` is a visible, non-trivial change to your personal Obsidian vault. Running it is your call to make. You should not trigger it silently as part of a build task.

To run it:

```bash
python3 harness/skills/memory/scripts/moc_generator.py --vault <path-to-vault>
```

This prints a summary line (`wrote N MOC page(s) under <vault>/_moc`). This is followed by one line per kind page written. Re-running is safe at any time. Regeneration only overwrites the `_moc/*.md` pages this module owns. It never touches a source note (see the idempotency row in the Quick Reference above).

## Scope boundaries (this plan)

- **CLI-invokable only.** You get no hook wiring, no scheduled regeneration, and no automatic regeneration. You will do a follow-up once the generator proves useful in practice.
- **No pagination.** High-frequency kinds (`preferences` alone was 993 notes at the plan's frequency audit) produce a single long MOC page with no pagination design. This is a named follow-up. You do not solve it here.
- **Read-only over source notes.** The generator can overwrite its own `_moc/*.md` output. It never mutates a note it catalogs.

## Arc-index pages (`--arcs`)

The `--arcs` flag additionally (re)generates one `kind: arc-index` page per `(project, arc)` pair at `<vault>/projects/<project>/arcs/<arc-slug>.md`, for every entry under `projects/` carrying an `arc:` frontmatter field (the 2026-07-18 arc-as-metadata convention — see [AgentM Memory System § Arcs](../designs/agentm-memory-system#arcs--temporal-grouping-as-metadata-not-folders)).

| Function | Signature | Purpose |
|---|---|---|
| `build_arc_groups(vault_path)` | `build_arc_groups(vault_path: Path \| str) -> dict[tuple[str, str], list[tuple[str, str, dict]]]` (`moc_generator.py`) | Read-only scan of `projects/` for entries carrying `arc:`. Returns `{(project, arc): [(rel_path_str, created, fm), ...]}`, newest-first by `created`. |
| `generate_arc_indexes(vault_path, *, today)` | `generate_arc_indexes(vault_path: Path \| str, *, today: str) -> list[str]` | Writes/updates each `(project, arc)` page. Returns the `project/arc` keys written. |
| CLI | `python3 harness/skills/memory/scripts/moc_generator.py --vault <path> --arcs` | Runs `generate()` as normal, then also runs `generate_arc_indexes()`. |

Unlike the fully-generated `_moc/<kind>.md` pages, an arc-index is a real memory entry a human may hand-edit above a marker line (`<!-- BEGIN GENERATED ARC LINKS (moc_generator.py — do not edit below) -->`). Regeneration only ever replaces the generated link-list below that marker — a hand-written header above it survives. A cross-repo arc (the same `arc:` slug stamped in more than one project) gets a full link list in each project that has entries, plus an "also stamped `arc: <arc>` in: …" cross-reference line pointing at the sibling project's page — the canonical-vs-pointer distinction the design names is an editorial call layered on by hand, not a mechanical one.

Like `--arcs` regeneration itself, this is CLI-invokable only — no hook or scheduled wiring, same as the base `--vault`-only mode above.

## Related

- [Kind-taxonomy registry](Kind-Taxonomy-Registry) — This generator depends on this registry for known/unrecognized-kind labeling. `--arcs` groups by `arc_registry.py`'s `KNOWN_ARCS` the same way.
- [AgentM Memory System](../designs/agentm-memory-system) — This is the governing design (V6-18; arcs added 2026-07-18).
- [Audit the vault](../how-to/Audit-The-Vault) — This generator follows this sibling read-only vault tool pattern.
