# Design governance reference — `governs:` frontmatter + resolver

The AG-track substrate for grounding the loop (design-doc §6): a small frontmatter convention on living designs plus a resolver ([`scripts/governs_resolver.py`](https://github.com/alexherrero/agentm/blob/main/scripts/governs_resolver.py)) that answers *"which living design governs this file or area?"* by reading the local repo's `wiki/designs/` frontmatter. The crickets `find_governing_design.py` bridge targets this module's contract, exactly as `find_capability.py` targets [`capability_resolver.py`](Capability-Resolver). Stdlib-only; never imports design or plugin code; fails safe to `greenfield` on any absence.

## ⚡ Quick Reference

| Function / command | Signature | Returns | Absent / error → |
|---|---|---|---|
| `resolve_governing_design` | `resolve_governing_design(target, *, root=None, include_proposed=False) → dict` | `{governed, design, area, reason}` | `{governed: False, …, reason: "greenfield"}` (never raises) |
| `build_index` | `build_index(root=None, *, include_proposed=False) → list[GovernsEntry]` | one entry per `governs:` pattern of each launched design | `[]` (never raises) |
| CLI | `python3 scripts/governs_resolver.py [--json] [--root DIR] [--include-proposed] <file-or-area>` | exit 0 (governed) / 1 (greenfield) / 2 (usage) | exit 1 |
| Shim | `scripts/agentm-governs.sh <file-or-area>` | same exit codes as the CLI | exit 1 |

## Exit / reason codes (the bridge contract)

| Exit | `reason` | Meaning | stdout |
|---|---|---|---|
| `0` | `"governed"` | A living design governs the target. | the design's repo-relative path (or full dict with `--json`) |
| `1` | `"greenfield"` | No design governs the target — clean none. | empty (a note goes to stderr) |
| `1` | `"error"` | An internal error occurred; treated as not-governed (fail-safe). | empty (note to stderr) |
| `2` | — | Usage error (no target given). | usage to stderr |

`--json` prints the full result dict to stdout instead of the bare path; the exit code is unchanged. This is the surface the crickets C3 bridge (`find_governing_design.py`) discovers by path-fallback and shells out to, graceful-skipping when agentm is absent.

## The frontmatter convention

Every living design under `wiki/designs/` carries machine-readable altitude + ownership. A reader (and the resolver) sees what a design governs without opening it.

| Key | Domain | Values | Used by the resolver? |
|---|---|---|---|
| `governs:` | design docs | list of repo-relative path globs / dir-prefixes the design owns (e.g. `harness/principles.md`, `scripts`) | **yes** — the match surface |
| `area:` | design docs | taxonomy bucket: `foundations · agentm · memory · experience · opinions · personas` | **yes** — area-name resolution + grouping |
| `scope:` | design docs | altitude: `arc › feature › sub-feature › tweak` | no (metadata / human navigation) |
| `kind:` | any artifact | artifact type: `design` \| `research` | no (metadata; keeps `research/` separate from `design`) |
| `shape:` | **primitives only** | Axis-A SHAPE: `skill · hook · agent · slash-command · persona · script · service` | no |

> **`shape:` is not stamped on design docs.** It is the Pillar-1 SHAPE axis for *host-loaded primitives* (a skill, a hook, an agent def). A design document is not a host-loaded primitive, so it carries no honest `shape:` value — the key is defined here for the primitives the spine classifies, never overloaded onto `kind:`. See the [AgentM HLD](agentm-hld) classification spine.

### Status filter

Only `status: launched` designs participate in resolution by default (the live truth the SessionStart paths-only inject also filters on). `status: proposed` designs are excluded unless `include_proposed=True` / `--include-proposed` is passed. The status enum is two states — `proposed → launched` (design-doc §9.5).

## Resolution semantics

1. **Area-name input** — if `target` exactly equals a known `area:` value, return the launched design with that area (ties break on sorted design path).
2. **Path input** — otherwise treat `target` as a repo-relative path and find the **most-specific** matching `governs:` pattern:
   - exact file match (`target == pattern`),
   - directory prefix (`target` under `pattern + "/"`),
   - glob (`fnmatch`, incl. `pattern/*`).
   - **Longest matching pattern wins**; equal specificity breaks on lexicographically-first design path. So `harness/principles.md` (governed by [Foundations](agentm-foundations-hld)) wins over the broader `harness` (governed by the [AgentM HLD](agentm-hld)).
3. **No match** → `greenfield` (exit 1). As children lift in Phase 3 and stamp narrower `governs:` patterns, resolution refines automatically — no resolver change needed.

## Public API

### `resolve_governing_design(target, *, root=None, include_proposed=False) → dict`

```python
{
    "governed": bool,
    "design":   str | None,   # repo-relative path to the governing design
    "area":     str | None,   # the governing design's area
    "reason":   str,          # "governed" | "greenfield" | "error"
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
