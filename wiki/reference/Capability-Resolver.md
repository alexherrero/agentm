# Capability resolver reference

[`scripts/capability_resolver.py`](https://github.com/alexherrero/agentm/blob/main/scripts/capability_resolver.py) is your plugin-capability registry and resolver. It provides the runtime half of crickets' `enhances:` soft-composition. It aggregates the `capabilities:` keys from installed plugins into a per-host registry. You use it to answer the question, "is this capability available, optionally at a version range?" It uses only the standard library. It never imports plugin code. It handles missing or corrupt files by degrading gracefully.

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
| `"version-mismatch"` | The provider is installed but its version does not satisfy the requested range. |

## Public API

### `capability_available(name, *, version=None, registry=None) → bool`

`capability_available` is your boolean surface. Use this primary interface when you only need `True` or `False`.

| Parameter | Type | Detail |
|---|---|---|
| `name` | `str` | The capability key to probe (e.g. `"developer-workflows"`, `"code-review"`). |
| `version` | `str \| None` | Version-range constraint (e.g. `">= 1.2"`). When `None`, any installed version satisfies. Evaluated by `satisfies()` — see [Version matching](#version-matching) below. |
| `registry` | `dict \| None` | Pre-built registry from `build_registry()`. When `None`, builds on each call. Inject when calling in a loop to avoid repeated I/O. |

This function never raises an exception. It returns `False` on any internal error. This implements LC-4. Unavailable is your safe default.

### `capability_resolve(name, *, version=None, registry=None) → dict`

`capability_resolve` provides your richer return form. Use this when you need to log or branch on the reason.

```python
{
    "available": bool,
    "provider":  str | None,   # plugin slug
    "version":   str | None,   # provider's declared version
    "reason":    str,          # one of the four reason codes above
}
```

This takes the same parameters as `capability_available`. It never raises an exception.

### `build_registry(root=None) → dict[str, ProviderEntry]`

`build_registry` is your low-level builder. It returns the raw capability-to-provider map. The `root` parameter overrides your home directory. Tests use this with a temp directory instead of your live `~`. It returns `{}` on any I/O error.

`ProviderEntry` is a frozen dataclass:

| Field | Type | Meaning |
|---|---|---|
| `plugin` | `str` | Plugin slug / name |
| `version` | `str \| None` | Declared version from the marketplace manifest |
| `installed` | `bool` | `True` = enabled on the current host |

## Host read paths

The resolver aggregates two host state directories independently. It then merges them. Claude Code entries win when both claim the same capability.

| Host | Enabled-set source | Capability source |
|---|---|---|
| Claude Code | `~/.claude/plugins/installed_plugins.json` (enabled set) + `~/.claude/plugins/known_marketplaces.json` (resolves each marketplace's `installLocation`) | `<installLocation>/.claude-plugin/marketplace.json` → `plugins[*].capabilities[]` |
| Antigravity | `~/.gemini/config/import_manifest.json` | `~/.gemini/config/plugins/<name>/capabilities.json` sidecar (emitted by the crickets Antigravity generator since V5-8) |

Both paths are **optional**. A missing or corrupt file at any step returns an empty partial registry. The merger then silently skips the absent host. The Antigravity path contributes nothing on a machine with only Claude Code. The reverse is also true.

> [!NOTE]
> The resolver is one-directional. It reads manifests as data. It never imports plugin code. It never writes to any host state. It never raises an exception to your caller, regardless of what it finds.

## Merge semantics

The following rules apply when both hosts declare a provider for the same capability:

1. **Installed provider wins over not-installed.** An enabled Claude Code plugin beats a declared-but-not-installed Antigravity sidecar.
2. **Claude Code wins over Antigravity when both are installed.** You expect the two to be identical in co-installed setups. CC takes priority as your tie-breaker.
3. **First declarant wins within each host.** The first plugin appearing in the manifest order wins if two plugins on the same host declare the same capability.

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

This is your entry point wired by the `agentm capability` shim (Task 3). The importable module is your primary contract. The CLI gives your shell callers a convenience shim.

## Design constraints (V5-8)

| Label | Constraint |
|---|---|
| LC-2 | Capability-keyed: callers name the capability; the resolver finds the provider. |
| LC-3 | Version matching is a single range check, not a solver. Compound specifiers (PEP 440 `a, b` syntax) are rejected — `satisfies()` returns `False` rather than partially evaluating. |
| LC-4 | Unavailable is the safe default; the resolver never raises on absence. |
| LC-6 | No agentm substrate → all capabilities resolve to `"unavailable"` (safe). |

## Version matching

[`scripts/capability_version_match.py`](https://github.com/alexherrero/agentm/blob/main/scripts/capability_version_match.py) implements your version range evaluation. It uses only the standard library. The public API is a single function:

```python
satisfies(installed_version: str | None, range_str: str) -> bool
```

It returns `True` when `installed_version` satisfies the constraint expressed in `range_str`. It returns `False` on any malformed input, a `None` version, or a compound specifier. It never raises an exception. This implements your LC-4 graceful degrade.

### Supported operators

| Operator | Meaning | Example |
|---|---|---|
| `>=` | Greater than or equal | `>= 1.2` |
| `>` | Strictly greater than | `> 1.0` |
| `<=` | Less than or equal | `<= 2.0` |
| `<` | Strictly less than | `< 3.0` |
| `==` | Exact match | `== 1.2.3` |
| `!=` | Not equal | `!= 1.0` |
| `~=` | Compatible release (PEP 440) | `~= 1.2` |

### Compatible release (`~=`) semantics

`~= X.Y` expands to `>= X.Y AND < (X+1)`. This means "any release of the same major version starting at X.Y or later". For example:

| Range | Satisfies | Does not satisfy |
|---|---|---|
| `~= 1.2` | `1.2`, `1.3`, `1.9.99` | `2.0`, `1.1` |
| `~= 2.0` | `2.0`, `2.5` | `3.0`, `1.9` |

This strictly follows PEP 440's compatible release clause.

### Version padding

The script zero-pads versions with fewer components before comparison. It treats `1.2` as `1.2.0`. It then compares them component-wise as integers.

### Graceful degrade (LC-4)

`satisfies()` returns `False` and never raises an exception for:

- `None` installed version — provider's version is undeclared in its manifest.
- Malformed version string — cannot be parsed as dot-separated integers.
- Compound specifier — range strings containing `,` (PEP 440 `a, b` multi-constraint form). The resolver acts as a single-check, not a solver. It rejects compound specifiers entirely.

`capability_resolve` uses the `"version-mismatch"` reason code when `satisfies()` returns `False` for an installed provider.

## Related

- [CI gates](CI-Gates) — the test gate (`scripts/test_capability_resolver.py`, 39 tests) runs in `check-all.sh`.
- [Auto-orchestration](Auto-Orchestration) — the runtime that will consume `capability_available` to gate enhances-declared skill dispatch.
- [AgentM HLD — V5 unbundling](agentm-hld) — the decision that established plugins as the soft-composition surface.
