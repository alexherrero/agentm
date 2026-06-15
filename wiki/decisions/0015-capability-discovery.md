<!-- mode: decision -->
# ADR 0015 — Capability discovery: the `enhances:` runtime

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-15

## Context

crickets plugins can declare `capabilities:` (what they provide) and `enhances:` (what they augment, optionally at a version range). The `enhances:` soft-composition model needs a runtime to answer *"is capability X available on this host?"* so a plugin phase can decide whether to activate an optional enhancement.

Before V5-8 the answer was baked into crickets as `capability_probe.py`: a slug-keyed script that hard-coded the mapping from each capability to a specific plugin slug and tested whether that slug was installed. This worked while the plugin set was small and stable, but broke encapsulation — crickets had to know the exact slug for every capability, the mapping was not declarative, and there was no version-range check.

**Open questions this decision resolves:**

- Where should the resolver live — in agentm or in crickets?
- Should the query be capability-keyed or slug-keyed?
- How should version ranges be handled — full solver or single-range check?
- What happens when agentm is not installed (bare-crickets-alone)?
- How is the enabled-plugin state read on Claude Code vs Antigravity?

**The M7 spike (2026-06-15) — gated the build.** The design assumed both hosts persist the marketplace render (carrying `capabilities:`/`enhances:`) locally in a readable, parseable file after install. Claude Code confirmed: `<installLocation>/.claude-plugin/marketplace.json` via `~/.claude/plugins/known_marketplaces.json` + enabled-set in `installed_plugins.json`. Antigravity partial-fail: the host has no marketplace concept; plugins are installed by path; `plugin.json` does not carry `capabilities:`/`enhances:`. Resolution adopted as Option A (see DC-4).

## Decision

### [DC-1] The contract is an importable module; the CLI shim wraps it.

`scripts/capability_resolver.py` exports `capability_available(name, *, version=None) → bool` and `capability_resolve(name) → {available, provider, version, reason}` as the importable surface. The `agentm-capability.sh` shell shim and the `_main` / `__main__` entrypoint make it callable as a subprocess — mirroring `resolve_plan.py`'s idiom so crickets call sites barely change.

**Why not a CLI-only approach:** The module contract (LC-1) lets tests inject a fixture registry; a pure-CLI approach forces subprocess overhead for every test. The shim is a thin `exec python3` wrapper on top.

### [DC-2] The query is capability-keyed, not plugin-keyed (LC-2).

`capability_available("git-review")` — the caller names the *capability*, the resolver finds the *provider*. The hard-coded slug knowledge moves out of crickets and into the registry (built from manifest data). No call site needs to know which plugin slug provides a capability.

**Why not slug-keyed:** The slug-keyed probe was the `capability_probe.py` model — it required crickets to hard-code `"git-review"` → `"crickets-git-review"`. Adding a plugin means editing the probe, which defeats the purpose of the declarative `capabilities:` field.

### [DC-3] Version matching is a single range check, never a solver (LC-3).

`capability_version_match.satisfies(installed_version, range_str) → bool` — one comparison, one installed version, one range string. Supports `>=`, `>`, `<=`, `<`, `==`, `!=`, `~=` (PEP 440 compatible release). Compound specifiers (e.g. `>= 1.0, < 2.0`) are explicitly rejected.

**`enhances ∩ requires = ∅`:** soft `enhances:` and hard `requires:` are disjoint. Hard dependencies are the host's job (`plugin.json` `dependencies:` → `agy install` / Claude Code marketplace). A single range check, never a transitive solver — any transitive ask is a redesign with its own ADR.

**Why not a full semver solver:** a solver is necessary when multiple providers can satisfy a requirement and need to be ranked, or when transitive deps conflict. This use case has at most one provider per capability (first installed wins; not a solver — LC-3), so a single comparison is sufficient and correct. A solver would be engineering overhead with no benefit.

### [DC-4] Antigravity: capabilities.json sidecar (M7 Option A).

For Antigravity (which has no marketplace registry and does not persist the marketplace render), crickets' Antigravity dist generator (`emit_antigravity.py`) emits a `capabilities.json` sidecar alongside each plugin's `plugin.json`. The sidecar carries `version:` + `capabilities:` + `enhances:`, and `agy plugin install <path>` copies it to `~/.gemini/config/plugins/<name>/capabilities.json`. The resolver reads it alongside `~/.gemini/config/import_manifest.json` (the enabled-set).

**Why not Option B (store capabilities in plugin.json):** `agy plugin validate` rejects unknown keys in `plugin.json`; adding `capabilities:`/`enhances:` would break validation for all Antigravity users of crickets plugins. A sidecar is a zero-disruption add-on.

**Why not Option C (derive from the AG marketplace render at install time):** Antigravity has no marketplace concept — plugins are installed individually by path. There is no aggregate manifest to write to.

### [DC-5] Graceful degrade — unavailable is always a valid answer (LC-4).

No host state, missing or corrupt JSON, a missing agentm substrate — all collapse to the same code path: an empty registry, `capability_available` returns False, `capability_resolve` returns `"no-provider"`. The resolver never raises. The CLI exits 1 (unavailable), never 2 (usage error), on a missing host state.

**Why:** the only non-0 exit that changes call-site behavior is 0 vs 1. A crash (or exception propagating to the shell) is a worse outcome than "unavailable" — plugins should degrade cleanly, not error-out.

### [DC-6] agentm defines + ships the resolver; crickets adopts (LC-5).

The resolver lives in agentm (`scripts/capability_resolver.py`). The crickets cutover (replacing the 6 `capability_probe.py` call sites with `agentm capability`, adding the optional `version:` field to `enhances:`, and deleting the probe) is a separate crickets plan, sequenced AFTER agentm ships. The probe stays live until the cutover lands — no flag day.

**Why not ship both together:** a two-repo flag day on a production tool chain is a coordination risk. Sequencing (agentm → crickets cutover) lets each side be verified independently and lets the probe bridge the gap.

## Consequences

**Positive:**
- crickets call sites become capability-keyed — no slug hard-coded in probe logic.
- Version-range checks work out of the box once providers declare `version:`.
- Both hosts (Claude Code + Antigravity) are covered with a minimal read path.
- Graceful degrade means a bare-crickets-alone install (no agentm substrate) silently falls through to `standalone` behavior, not a crash (LC-6, accepted 2026-06-12).

**Negative:**
- Antigravity now requires the `capabilities.json` sidecar to be emitted at dist time and present on disk after install. Plugins built before V5-8 do not have the sidecar — they will be invisible to the resolver until regenerated.
- The cutover (LC-5) is a coordinated two-repo change: agentm must ship first; crickets must be updated and the probe deleted only after. The probe remaining live in the interim is a transitional maintenance burden.

**Load-bearing assumptions with re-audit triggers:**

1. **Claude Code persists the marketplace render at `<installLocation>/.claude-plugin/marketplace.json`.** Re-audit trigger: if Claude Code changes its plugin install path or stops writing the marketplace render locally, the `_read_claude_code` path must be updated.
2. **Antigravity copies the `capabilities.json` sidecar to `~/.gemini/config/plugins/<name>/capabilities.json` during `agy plugin install`.** Re-audit trigger: if `agy plugin install` stops copying sidecars, or the install path changes, the `_read_antigravity` path must be updated.
3. **The enabled-set format for Claude Code is `installed_plugins.json` with `plugins: { "<slug>@<ver>": [{version}] }`.** Re-audit trigger: if the Claude Code plugin registry changes its JSON shape.
4. **The enabled-set format for Antigravity is `import_manifest.json` with `imports: [{name}]`.** Re-audit trigger: if Antigravity changes its import manifest structure.
5. **One provider per capability (resolver is not a solver).** Re-audit trigger: if a future V5.x feature requires multiple providers or conflict resolution, LC-3 becomes a design change and a new ADR is needed.

## Related

- [ADR 0006 — Split customizations into crickets](0006-crickets-split) — C3 (substrate beneath, not plugin host) is the anchoring principle.
- [ADR 0011 — V5 unbundling](0011-v5-unbundling-dev-loop) — context for the agentm/crickets split.
- [ADR 0013 — Storage seam](0013-storage-seam-fail-loud-selection) — sibling seam ADR (storage read is also data-only, never code import).
- [Capability resolver reference](../reference/Capability-Resolver) — the `capability_resolver.py` API.
- [Soft-composition explanation](../explanation/Soft-Composition) — why `enhances ∩ requires = ∅`.
