# MOC generator reference

> [!NOTE]
> **Status: implemented** — `harness/skills/memory/scripts/moc_generator.py` now covers two generated indexes: the standards map (`--standards`) and the arc-index pages (`--arcs`). [AgentM Memory System](../designs/agentm-memory-system) governs the arc convention (V6-18, arcs added 2026-07-18); [AgentM Vault](../designs/agentm-vault) governs the maps that replaced this module's per-`kind` pages (agentm-vault plan 07).

> [!IMPORTANT]
> **The per-kind pages retired.** This module once wrote one page per `kind:` value under `<vault>/_moc/<kind>.md` (`build_kind_groups()`, `_render_moc()`, `generate()`). The pages themselves went at the 2026-08-11 rehoming pass; the functions that wrote them — with their `[[Home]]` backlink — were deleted in agentm-vault plan 07, once nothing called them any more. Browsing by memory type works through a different mechanism now: see [What replaced the per-kind pages](#what-replaced-the-per-kind-pages) below.

`moc_generator.py` writes two things today, neither of them a source note: `standards/moc-standards.md`, a generated map of the always-load tier, and the arc-index pages under `Projects/<project>/arcs/`. Passing neither `--standards` nor `--arcs` is refused — there's nothing else left for the CLI to do.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What does this script generate, today? | `standards/moc-standards.md` (`--standards`) and the arc-index pages under `Projects/<project>/arcs/<arc-slug>.md` (`--arcs`). Nothing else. |
| How do I run it? | `python3 harness/skills/memory/scripts/moc_generator.py --vault <path> [--standards] [--arcs]` — at least one flag; both together is fine. Neither given prints `nothing to generate: pass --standards, --arcs or both` to stderr and exits 2. |
| Where did the per-kind pages go? | Retired — see the callout above and [What replaced the per-kind pages](#what-replaced-the-per-kind-pages). |
| Is it safe to re-run? | Yes, for both remaining modes. `--standards` overwrites only `standards/moc-standards.md`. `--arcs` only ever replaces an arc-index page's generated link-list below its marker line; a hand-written header above the marker survives. |
| Does it read the whole vault, or something narrower? | `--arcs` scans `desk/projects/` and the vault-root `Projects/` (unioned) for entries carrying `arc:`. `--standards` reads only `standards/*.md` and `standards/voice/*.md`. Neither walks `memory/` any more — that walk went with the deleted per-kind functions. |
| Related pages | [Memory daemon reference § the dreaming binary](Memory-Daemon#the-dreaming-binary-agentmdream) — where per-type browsing lives now · [Kind-taxonomy registry](Kind-Taxonomy-Registry) · [AgentM Vault](../designs/agentm-vault) · [AgentM Memory System](../designs/agentm-memory-system) |

## What replaced the per-kind pages

Browsing the vault by memory type is now the dreaming binary's job, not this script's. `daemon/internal/dreaming/mocs.go`'s `mocs` job writes a generated page per memory **type** (`type:` — the six values the filing contract registers, not the free-form `kind:` field this module's retired pages grouped by) under `memory/mocs/`, once a type holds at least `moc_min_members` live notes; `moc-memory.md` lists every type, at or past the floor by its page's link and below it in full; `moc-root.md` is the generated entry point, listing every area's map. See [Memory daemon reference § the dreaming binary](Memory-Daemon#the-dreaming-binary-agentmdream) for the mechanism, and [CI gates reference](CI-Gates) for the gates that hold `mocs/` to that shape.

The [kind-taxonomy registry](Kind-Taxonomy-Registry)'s `is_known()` labeling, which gave the retired pages their "unrecognized kind" header, has no reader left in this module. The registry itself is still read by `vault_lint.py`, `frontmatter_validator.py`, `check-vocabulary-membership.py` and `check-kind-taxonomy` (see [CI gates](CI-Gates)).

## Arc-index pages (`--arcs`)

The `--arcs` flag additionally (re)generates one `kind: arc-index` page per `(project, arc)` pair at that project's own tree — the vault-root `Projects/<project>/arcs/<arc-slug>.md` when that space holds the project (filing-v2 part 2b), else `<vault>/desk/projects/<project>/arcs/<arc-slug>.md` — for every entry under either project space carrying an `arc:` frontmatter field (the 2026-07-18 arc-as-metadata convention — see [AgentM Memory System § Arcs](../designs/agentm-memory-system#arcs--temporal-grouping-as-metadata-not-folders)).

| Function | Signature | Purpose |
|---|---|---|
| `build_arc_groups(vault_path)` | `build_arc_groups(vault_path: Path \| str) -> dict[tuple[str, str], list[tuple[str, str, dict]]]` (`moc_generator.py`) | Read-only scan of `desk/projects/` and the vault-root `Projects/` (unioned) for entries carrying `arc:`. Returns `{(project, arc): [(rel_path_str, created, fm), ...]}`, newest-first by `created`. |
| `generate_arc_indexes(vault_path, *, today)` | `generate_arc_indexes(vault_path: Path \| str, *, today: str) -> list[str]` | Writes/updates each `(project, arc)` page. Returns the `project/arc` keys written. |
| CLI | `python3 harness/skills/memory/scripts/moc_generator.py --vault <path> --arcs` | Runs `generate_arc_indexes()`. |

Unlike the fully-generated `_moc/<kind>.md` pages, an arc-index is a real memory entry a human may hand-edit above a marker line (`<!-- BEGIN GENERATED ARC LINKS (moc_generator.py — do not edit below) -->`). Regeneration only ever replaces the generated link-list below that marker — a hand-written header above it survives. A cross-repo arc (the same `arc:` slug stamped in more than one project) gets a full link list in each project that has entries, plus an "also stamped `arc: <arc>` in: …" cross-reference line pointing at the sibling project's page — the canonical-vs-pointer distinction the design names is an editorial call layered on by hand, not a mechanical one.

Like `--arcs` regeneration itself, this is CLI-invokable only — no hook or scheduled wiring, same as the base `--vault`-only mode above.

## The standards map (`--standards`)

The `--standards` flag additionally (re)generates `standards/moc-standards.md` — a map of the always-load tier, added by the memory-root trims (agentm-vault plan 05). It lists the rule files at the top of `standards/` and, separately, the voice library under `standards/voice/`, each as a `[[wikilink]]` by stem with its `title:` (or `trigger:` or `description:`) frontmatter value, falling back to the file's first `# ` heading, then its stem. Every entry is generated fresh — nothing hand-written survives a regeneration.

This is the one file besides `user-preferences.md` and `security-and-secret-governance.md` (the two migration-drafted documents) that plan 05 puts under `standards/`. The recall loader skips every `moc-*` file when it builds the always-load injection (generated navigation carries no standing instruction), so this map is for you and the chat surfaces to browse by, never for the injected tier itself.

| Function | Signature | Purpose |
|---|---|---|
| `render_standards_moc(standards)` | `render_standards_moc(standards: Path) -> str` | Read-only render. Lists the rule files (every `standards/*.md` except `moc-*`), then the voice library (`standards/voice/*.md`), each titled via `_title_of()`. |
| `generate_standards_moc(vault_path)` | `generate_standards_moc(vault_path: Path \| str) -> Path` | Writes `standards/moc-standards.md` (creating `standards/` if absent) and returns its path. |
| CLI | `python3 harness/skills/memory/scripts/moc_generator.py --vault <path> --standards` | Runs `generate_standards_moc()`. |

Like `--arcs`, this is CLI-invokable only — no hook or scheduled wiring. `scripts/migrate/memory_root_trims.py` calls `generate_standards_moc()` directly as the last step of the standards-set migration, rather than shelling out to this CLI.

## Related

- [Memory daemon reference § the dreaming binary](Memory-Daemon#the-dreaming-binary-agentmdream) — where the retired per-kind pages' job — browsing the vault by memory type — lives now.
- [Kind-taxonomy registry](Kind-Taxonomy-Registry) — the `kind:` catalog this module no longer reads; `arc_registry.py`'s own `KNOWN_ARCS` is a separate, sibling registry that `--arcs` does not read either — it groups by the raw `arc:` value with no registry validation.
- [AgentM Memory System](../designs/agentm-memory-system) — the governing design for the arc convention (V6-18; arcs added 2026-07-18).
- [AgentM Vault](../designs/agentm-vault) — the governing design for the maps that replaced the per-kind pages (agentm-vault plan 07).
- [Audit the vault](../how-to/Audit-The-Vault) — this generator follows this sibling read-only vault tool pattern.
