# Design governance reference — `governs:` frontmatter + resolver

This gives you the AG-track substrate for grounding the loop (design-doc §6). You define a small frontmatter convention on living designs. You use a resolver at [`scripts/governs_resolver.py`](https://github.com/alexherrero/agentm/blob/main/scripts/governs_resolver.py). The resolver answers *"which living design governs this file or area?"*. It reads the `wiki/designs/` frontmatter in your local repo. The crickets `agentm_bridge.py` bridge targets this module's contract with its `governing-design` verb. This works exactly like its `capability` verb targets [`capability_resolver.py`](Capability-Resolver). You only need the standard library. You never import design or plugin code. You fail safe to `greenfield` on any absence.

## ⚡ Quick Reference

| Function / command | Signature | Returns | Absent / error → |
|---|---|---|---|
| `resolve_governing_design` | `resolve_governing_design(target, *, root=None, include_proposed=False) → dict` | `{governed, design, area, reason}` | `{governed: False, …, reason: "greenfield"}` (never raises) |
| `designs_in` | `designs_in(area, *, root=None, include_proposed=False) → list[str]` | sorted design paths whose `area:` == `area` (the area-keyed lookup; planned consumer for SessionStart inject / index, not yet wired) | `[]` (never raises) |
| `build_index` | `build_index(root=None, *, include_proposed=False) → list[GovernsEntry]` | one entry per `governs:` pattern of each launched design (area-only designs get one empty-pattern entry) | `[]` (never raises) |
| CLI | `python3 scripts/governs_resolver.py [--json] [--root DIR] [--include-proposed] <file-or-area>` | exit 0 (governed) / 1 (greenfield/overlap) / 2 (usage) | exit 1 |
| Shim | `scripts/agentm-governs.sh <file-or-area>` | same exit codes as the CLI | exit 1 |

## Exit / reason codes (the bridge contract)

| Exit | `reason` | Meaning | stdout |
|---|---|---|---|
| `0` | `"governed"` | A living design governs the target. | the design's repo-relative path (or full dict with `--json`) |
| `1` | `"greenfield"` | No design governs the target — clean none. | empty (a note goes to stderr) |
| `1` | `"overlap"` | Two designs match at equal specificity — **fail-loud, not a guess** (the no-overlap rule was violated; narrow one `governs:` glob). | empty (a note goes to stderr) |
| `1` | `"error"` | An internal error occurred; treated as not-governed (fail-safe). | empty (note to stderr) |
| `2` | — | Usage error (no target given). | usage to stderr |

You pass `--json` to print the full result dict to stdout instead of the bare path. The exit code remains unchanged. The crickets C3 bridge discovers this surface by path-fallback. It shells out to it using `agentm_bridge.py`'s `governing-design` verb. It graceful-skips when agentm is absent.

## The frontmatter convention

You add machine-readable altitude and ownership to every living design under `wiki/designs/`. A reader sees what a design governs without opening it. The resolver sees this too.

| Key | Domain | Values | Used by the resolver? |
|---|---|---|---|
| `governs:` | non-root designs | list of repo-relative path globs the design owns (e.g. `scripts/storage_seam.py`, `scripts/**`). Root/area-only designs omit it. | **yes** — the match surface |
| `area:` | every design | one value from the two-level controlled vocabulary (below) | **yes** — area-name resolution + grouping |
| `scope:` | design docs | altitude: `arc › feature › sub-feature › tweak` | no (metadata / human navigation) |
| `kind:` | any artifact | artifact type: `design` \| `research` | no (metadata; keeps `research/` separate from `design`) |
| `shape:` | **primitives only** | Axis-A SHAPE: `skill · hook · agent · slash-command · persona · script · service` | no |

> **`shape:` is not stamped on design docs.** It is the Pillar-1 SHAPE axis for *host-loaded primitives* (a skill, a hook, an agent def). A design document is not a host-loaded primitive. It carries no honest `shape:` value. You define the key here for the primitives the spine classifies. You never overload it onto `kind:`. See design-doc §4 (classification spine). This is not yet published as a standalone linkable page in this repo.

### The `area:` vocabulary (two-level `<root>/<domain>`)

You use a controlled vocabulary. You assign one **owning design** per area. You point other designs in the area up as children. You use these agentm-side values:

| Root | Areas |
|---|---|
| `shared/` | `shared/foundations` (area-only — governs no code) |
| `agentm/` | `agentm/architecture` · `agentm/memory` · `agentm/memory-index` · `agentm/storage` · `agentm/experience` · `agentm/opinions` · `agentm/opinion-registry` · `agentm/personas` · `agentm/capability-resolution` · `agentm/model-effort-routing` · `agentm/phase-contract` · `agentm/mcp` · `agentm/vault-taxonomy` · `agentm/runner` · `agentm/autonomy` |
| `crickets/` | `crickets/architecture` + one per capability (`crickets/development-lifecycle`, `crickets/wiki`, …) |
| `governance/` | `governance` (the AG machinery: design-doc, this index, the grounding hooks, the ADR model) |

An `area:` outside the vocabulary should fail a valid-area lint. The honesty lints mirror the capability lints. The lints themselves are a follow-on to this resolver. You read the canonical source in the AG `area-taxonomy.md` spec.

### Status filter

Only `status: launched` designs participate in resolution by default. The SessionStart paths-only inject also filters on this live truth. You exclude `status: proposed` designs. You include them only if you pass `include_proposed=True` or `--include-proposed`. You use a two-state status enum. The states are `proposed → launched` (design-doc §9.5).

## Resolution semantics

1. **Area-name input** — You check if `target` exactly equals a known `area:` value. You return that area's design if it matches. For example, `shared/foundations` returns the Foundations HLD even though it governs no file. You reach area-only roots here. You never match them to a path.
2. **Path input** — You treat `target` as a repo-relative path otherwise. You find the **most-specific** matching `governs:` glob.
   - You match the exact file (`target == pattern`).
   - You match the directory prefix (`target` under `pattern + "/"`).
   - You match the glob (`fnmatch`, incl. `scripts/**`).
   - **Longest matching pattern wins.** A more-specific child wins over the broad `agentm/architecture` fallback (`scripts/**`). For example, `memory-storage-seam` governing `scripts/storage_seam.py` wins. This is the area-walk-up. A file with no specific owner lands on its area's broad-fallback glob.
   - **An exact-specificity tie between *different* designs is `overlap` (fail-loud), never a guess**. You fail loud because the no-overlap rule was violated. You narrow one glob. Multiple globs from the *same* design at equal specificity are fine.
3. **No match** → You return `greenfield` (exit 1). Children lift in Phase 3. They stamp narrower `governs:` globs. Resolution refines automatically. You need no resolver change.

## Public API

### `resolve_governing_design(target, *, root=None, include_proposed=False) → dict`

```python
{
    "governed": bool,
    "design":   str | None,   # repo-relative path to the governing design
    "area":     str | None,   # the governing design's area
    "reason":   str,          # "governed" | "greenfield" | "overlap" | "error"
}
```

| Parameter | Type | Detail |
|---|---|---|
| `target` | `str` | A repo-relative file path (e.g. `scripts/storage_seam.py`) or a known area name (e.g. `memory`). |
| `root` | `Path \| None` | Repo-root override (tests inject a temp dir). Defaults to the parent of `scripts/`. |
| `include_proposed` | `bool` | Also index `status: proposed` designs. Default `False` (launched only). |

You never raise an exception. You collapse any internal error to `{governed: False, design: None, area: None, reason: "error"}`. You fail safe. You mirror `capability_resolve`.

### `build_index(root=None, *, include_proposed=False) → list[GovernsEntry]`

You perform a low-level scan of `wiki/designs/**/*.md`. You yield one `GovernsEntry(pattern, design, area, status)` per pattern. The qualifying design declares these in `governs:`. You return `[]` on any I/O error.

## Design constraints

You mirror [`capability_resolver.py`](Capability-Resolver). All constraints are non-negotiable:

- **One-directional** — You read frontmatter as *data*. You never import design or plugin code. Designs are markdown. You have nothing to import.
- **Fail-safe** — You use `greenfield` as the safe default. You never raise on absence.
- **Bounded** — You run a single index scan of `wiki/designs/`. You use no arbitrary-tree recursion. You use no network. You use no third-party deps.
- **Stdlib-only** — You parse frontmatter with a minimal in-module reader. You do not use PyYAML.

## See also

[Capability resolver](Capability-Resolver) · [AgentM HLD](agentm-hld) · [Foundations](agentm-foundations-hld) · [CI gates](CI-Gates)
