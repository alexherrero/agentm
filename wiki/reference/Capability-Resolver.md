# Capability resolver reference

The plugin-capability registry + resolver ([`scripts/capability_resolver.py`](https://github.com/alexherrero/agentm/blob/main/scripts/capability_resolver.py)) — the runtime half of crickets' `enhances:` soft-composition. It aggregates installed plugins' declared `capabilities:` keys into a per-host registry and answers "is this capability available, optionally at a version range?" Stdlib-only; never imports plugin code; gracefully degrades on any missing or corrupt file.

## ⚡ Quick Reference

| Function / command | Signature | Returns | Absent / error → |
|---|---|---|---|
| `capability_available` | `capability_available(name, *, version=None, registry=None) → bool` | `True` iff provider installed + version satisfied | `False` (never raises) |
| `capability_resolve` | `capability_resolve(name, *, version=None, registry=None) → dict` | `{available, provider, version, reason}` | `{available: False, …, reason: "no-provider"}` |
| `build_registry` | `build_registry(root=None) → dict[str, ProviderEntry]` | raw capability → `ProviderEntry` map | `{}` (never raises) |
| CLI | `python3 scripts/capability_resolver.py <capability> [<version-range>]` | exit 0 (available) / 1 (unavailable) / 2 (usage error) | exit 1 |

## Reason codes

| Reason | Meaning |
|---|---|
| `"available"` | A provider is installed and the version constraint (if any) is satisfied. |
| `"no-provider"` | No plugin in any registry declares this capability. |
| `"provider-not-installed"` | A plugin declares the capability but is not enabled on this host. |
| `"version-mismatch"` | The provider is installed but its version does not satisfy the requested range (version-range matching ships in Task 2). |

## Public API

### `capability_available(name, *, version=None, registry=None) → bool`

The boolean surface — the primary interface for callers that only need `True`/`False`.

| Parameter | Type | Detail |
|---|---|---|
| `name` | `str` | The capability key to probe (e.g. `"developer-workflows"`, `"code-review"`). |
| `version` | `str \| None` | Version-range constraint (e.g. `">= 1.2"`). Accepted today; evaluated in Task 2. Any version satisfies in Task 1. |
| `registry` | `dict \| None` | Pre-built registry from `build_registry()`. When `None`, builds on each call. Inject when calling in a loop to avoid repeated I/O. |

Never raises. Returns `False` on any internal error (LC-4: unavailable is the safe default).

### `capability_resolve(name, *, version=None, registry=None) → dict`

Richer form for callers that need to log or branch on the reason.

```python
{
    "available": bool,
    "provider":  str | None,   # plugin slug
    "version":   str | None,   # provider's declared version
    "reason":    str,          # one of the four reason codes above
}
```

Same parameters as `capability_available`. Never raises.

### `build_registry(root=None) → dict[str, ProviderEntry]`

Low-level builder. Returns the raw capability → provider map. `root` overrides the user home directory — used by tests with a temp directory instead of the live `~`. Returns `{}` on any I/O error.

`ProviderEntry` is a frozen dataclass:

| Field | Type | Meaning |
|---|---|---|
| `plugin` | `str` | Plugin slug / name |
| `version` | `str \| None` | Declared version from the marketplace manifest |
| `installed` | `bool` | `True` = enabled on the current host |

## Host read paths

The resolver aggregates two host state directories independently and merges them (Claude Code entries win when both claim the same capability).

| Host | Enabled-set source | Capability source |
|---|---|---|
| Claude Code | `~/.claude/plugins/installed_plugins.json` | `<installLocation>/.claude-plugin/marketplace.json` → `plugins[*].capabilities[]` |
| Antigravity | `~/.gemini/config/import_manifest.json` | `~/.gemini/config/plugins/<name>/capabilities.json` sidecar (emitted by the crickets Antigravity generator since V5-8) |

Both paths are **optional** — a missing or corrupt file at any step returns an empty partial registry, and the merger silently skips the absent host. On a machine with only Claude Code, the Antigravity path contributes nothing, and vice versa.

> [!NOTE]
> The resolver is one-directional: it reads manifests as data. It never imports plugin code, never writes to any host state, and never raises to the caller regardless of what it finds.

## Merge semantics

When both hosts declare a provider for the same capability:

1. **Installed provider wins over not-installed.** An enabled Claude Code plugin beats a declared-but-not-installed Antigravity sidecar.
2. **Claude Code wins over Antigravity when both are installed.** In co-installed setups the two are expected to be identical; CC takes priority as the tie-breaker.
3. **First declarant wins within each host.** If two plugins on the same host declare the same capability, whichever appears first in the manifest order wins.

## CLI shim

```bash
python3 scripts/capability_resolver.py <capability>
python3 scripts/capability_resolver.py <capability> <version-range>
```

| Exit code | Meaning |
|---|---|
| `0` | Capability available (and version satisfied, if given) |
| `1` | Capability unavailable |
| `2` | Usage error (wrong number of arguments) |

This is the entry point wired by the `agentm capability` shim (Task 3). The importable module is the primary contract; the CLI is a convenience shim for shell callers.

## Design constraints (V5-8)

| Label | Constraint |
|---|---|
| LC-2 | Capability-keyed: callers name the capability; the resolver finds the provider. |
| LC-3 | Version matching is a single range check, not a solver. Stub in Task 1; implemented in Task 2. |
| LC-4 | Unavailable is the safe default; the resolver never raises on absence. |
| LC-6 | No agentm substrate → all capabilities resolve to `"unavailable"` (safe). |

## Related

- [CI gates](CI-Gates) — the test gate (`scripts/test_capability_resolver.py`, 32 tests) runs in `check-all.sh`.
- [Auto-orchestration](Auto-Orchestration) — the runtime that will consume `capability_available` to gate enhances-declared skill dispatch.
- [ADR 0011 — V5 unbundling](0011-v5-unbundling-dev-loop) — the decision that established plugins as the soft-composition surface.
