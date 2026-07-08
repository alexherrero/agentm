# Design governance reference — `governs:` frontmatter + resolver

This is the AG-track substrate for grounding the loop (design-doc §6): a small frontmatter convention on living designs, plus a resolver ([`scripts/governs_resolver.py`](https://github.com/alexherrero/agentm/blob/main/scripts/governs_resolver.py)) that answers *"which living design governs this file or area?"* by reading the local repo's `wiki/designs/` frontmatter. The crickets `find_governing_design.py` bridge targets this module's contract, exactly as `find_capability.py` targets [`capability_resolver.py`](Capability-Resolver). It is stdlib-only, never imports design or plugin code, and fails safe to `greenfield` on any absence.

## ⚡ Quick Reference

| Function / command | Signature | Returns | Absent / error → |
|---|---|---|---|
| `resolve_governing_design` | `resolve_governing_design(target, *, root=None, include_proposed=False) → dict` | `{governed, design, area, reason}` | `{governed: False, …, reason: "greenfield"}` (never raises) |
| `designs_in` | `designs_in(area, *, root=None, include_proposed=False) → list[str]` | sorted design paths whose `area:` == `area` (the area-keyed lookup for SessionStart inject / index) | `[]` (never raises) |
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

`--json` prints the full result dict to stdout instead of the bare path; the exit code is unchanged. This is the surface the crickets C3 bridge (`find_governing_design.py`) discovers by path-fallback and shells out to, graceful-skipping when agentm is absent.

## The frontmatter convention

Every living design under `wiki/designs/` carries machine-readable altitude + ownership. A reader (and the resolver) sees what a design governs without opening it.

| Key | Domain | Values | Used by the resolver? |
|---|---|---|---|
| `governs:` | non-root designs | list of repo-relative path globs the design owns (e.g. `scripts/storage_seam.py`, `scripts/**`). Root/area-only designs omit it. | **yes** — the match surface |
| `area:` | every design | one value from the two-level controlled vocabulary (below) | **yes** — area-name resolution + grouping |
| `scope:` | design docs | altitude: `arc › feature › sub-feature › tweak` | no (metadata / human navigation) |
| `kind:` | any artifact | artifact type: `design` \| `research` | no (metadata; keeps `research/` separate from `design`) |
| `shape:` | **primitives only** | Axis-A SHAPE: `skill · hook · agent · slash-command · persona · script · service` | no |

> **`shape:` is not stamped on design docs.** It is the Pillar-1 SHAPE axis for *host-loaded primitives* (a skill, a hook, an agent def). A design document is not a host-loaded primitive, so it carries no honest `shape:` value — the key is defined here for the primitives the spine classifies, never overloaded onto `kind:`. See the [AgentM HLD](agentm-hld) classification spine.

### The `area:` vocabulary (two-level `<root>/<domain>`)

This is a controlled vocabulary: one **owning design** per area, with other designs in the area as children pointing up. agentm-side values:

| Root | Areas |
|---|---|
| `shared/` | `shared/foundations` (area-only — governs no code) |
| `agentm/` | `agentm/architecture` · `agentm/memory` · `agentm/storage` · `agentm/experience` · `agentm/opinions` · `agentm/personas` · `agentm/capability-resolution` · `agentm/model-effort-routing` · `agentm/phase-contract` · `agentm/mcp` · `agentm/vault-taxonomy` · `agentm/runner` · `agentm/autonomy` |
| `crickets/` | `crickets/architecture` + one per capability (`crickets/development-lifecycle`, `crickets/wiki`, …) |
| `governance/` | `governance` (the AG machinery: design-doc, this index, the grounding hooks, the ADR model) |

An `area:` not in the vocabulary should fail a valid-area lint (the honesty lints mirror the capability lints; the lints themselves are a follow-on to this resolver). The canonical source is the AG `area-taxonomy.md` spec.

### Status filter

Only `status: launched` designs participate in resolution by default (the live truth the SessionStart paths-only inject also filters on). `status: proposed` designs are excluded unless `include_proposed=True` / `--include-proposed` is passed. The status enum is two states — `proposed → launched` (design-doc §9.5).

## Resolution semantics

1. **Area-name input** — if `target` exactly equals a known `area:` value, return that area's design (e.g. `shared/foundations` → the Foundations HLD, even though it governs no file). Area-only roots are reachable here but never match a path.
2. **Path input** — otherwise treat `target` as a repo-relative path and find the **most-specific** matching `governs:` glob:
   - exact file match (`target == pattern`),
   - directory prefix (`target` under `pattern + "/"`),
   - glob (`fnmatch`, incl. `scripts/**`).
   - **Longest matching pattern wins.** A more-specific child (e.g. `memory-storage-seam` governing `scripts/storage_seam.py`) wins over the broad `agentm/architecture` fallback (`scripts/**`) — this is the area-walk-up: a file with no specific owner lands on its area's broad-fallback glob.
   - **An exact-specificity tie between *different* designs is `overlap` (fail-loud), never a guess** — the no-overlap rule was violated; narrow one glob. (Multiple globs from the *same* design at equal specificity is fine.)
3. **No match** → `greenfield` (exit 1). As children lift in Phase 3 and stamp narrower `governs:` globs, resolution refines automatically — no resolver change needed.

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

Never raises — any internal error collapses to `{governed: False, design: None, area: None, reason: "error"}` (fail-safe, mirroring `capability_resolve`).

### `build_index(root=None, *, include_proposed=False) → list[GovernsEntry]`

Low-level scan of `wiki/designs/**/*.md`, one `GovernsEntry(pattern, design, area, status)` per pattern a qualifying design declares in `governs:`. Returns `[]` on any I/O error.

## Design constraints

Mirrors [`capability_resolver.py`](Capability-Resolver), all non-negotiable:

- **One-directional** — reads frontmatter as *data*; never imports design or plugin code (designs are markdown — nothing to import).
- **Fail-safe** — `greenfield` is the safe default; never raises on absence.
- **Bounded** — a single index scan of `wiki/designs/`; no arbitrary-tree recursion, no network, no third-party deps.
- **Stdlib-only** — frontmatter is parsed by a minimal in-module reader (no PyYAML).

## See also

[Capability resolver](Capability-Resolver) · [AgentM HLD](agentm-hld) · [Foundations](agentm-foundations-hld) · [CI gates](CI-Gates)
