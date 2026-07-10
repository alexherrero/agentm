# MOC generator reference

> [!NOTE]
> **Status: pending** — planned in `PLAN-v6-15-v6-18-typed-object-moc` (task 3, V6-18), governed by [AgentM Memory Index](../designs/agentm-memory-index). Not yet built; this page reserves the shape the shipped CLI will fill in.

`moc_generator.py` will build browse-first MOCs (Maps of Content) over the vault — one generated Markdown page per `kind`, each listing wikilinks to every note of that kind, so an operator can browse a kind's entries in Obsidian without a search. It depends on the [kind-taxonomy registry](Kind-Taxonomy-Registry) (task 1) to label each group known vs. unrecognized.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What will generate the MOCs? | `harness/skills/memory/scripts/moc_generator.py`. |
| How will I run it? | `python3 harness/skills/memory/scripts/moc_generator.py --vault <path>` (CLI-invokable; no hook or scheduling wiring in this plan). |
| Where will the generated pages live? | A `_moc/` directory in the vault — one page per distinct `kind`. |
| Does it read the whole vault, or something narrower? | The same read-only walk `vec_index.py`'s `full_sync` uses. |
| Is it safe to re-run? | Yes — idempotent. Regenerating overwrites only the `_moc/*.md` pages it owns; it never touches source notes. |
| What order are entries listed in? | Newest-first by `created`. |
| Does it label unrecognized kinds? | Yes, via the [kind-taxonomy registry](Kind-Taxonomy-Registry)'s `is_known()`. |
| Related pages | [Kind-taxonomy registry](Kind-Taxonomy-Registry) · [AgentM Memory Index](../designs/agentm-memory-index) |

## What it will do

1. Walk the vault read-only (the same traversal `vec_index.py`'s `full_sync` already uses — no new walk logic).
2. Group notes by their `kind:` frontmatter value, using the [kind-taxonomy registry](Kind-Taxonomy-Registry) to label each group as a known kind or an unrecognized one.
3. Generate one MOC page per distinct kind under a `_moc/` directory, each listing a wikilink to every note of that kind, newest-first by `created`.
4. Overwrite only the `_moc/*.md` pages it owns on re-run — idempotent, byte-identical output across repeated runs with no source changes.

## Scope boundaries (this plan)

- **CLI-invokable only.** `python3 moc_generator.py --vault <path>` — no hook wiring, no scheduled/automatic regeneration. A follow-up once the generator proves useful in practice.
- **No pagination.** High-frequency kinds (`preferences` alone was 993 notes at the plan's frequency audit) will produce a single long MOC page with no pagination design — a named follow-up, not solved here.
- **Read-only over source notes.** The generator can overwrite its own `_moc/*.md` output; it never mutates a note it's cataloging.

## Related

- [Kind-taxonomy registry](Kind-Taxonomy-Registry) — the known/unrecognized-kind labeling this generator depends on.
- [AgentM Memory Index](../designs/agentm-memory-index) — the governing design (V6-18).
- [Audit the vault](../how-to/Audit-The-Vault) — the sibling read-only vault tool pattern this generator follows.
