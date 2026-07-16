# Process seam reference

The memory↔process client seam ([`scripts/process_seam.py`](https://github.com/alexherrero/agentm/blob/main/scripts/process_seam.py)) gives you a small, **read-only**, **graceful-no-op** view. You call this seam from a *process* (like the crickets developer-workflows phases today, or the V5-9 MCP server tomorrow). You use it instead of reaching into the memory engine's internals. It exports two functions. It composes these functions only from the DC-7-frozen public memory readers (`resolve_project`, `resolve_active_plan`, `harness_state_dir`, `is_available`). It never uses a write path. The importable Python module forms the contract ([LC-1]). The `python -m` entrypoint gives you a convenience shim for non-Python shell callers.

> [!NOTE]
> **R0.9 (agentmEngine#2):** A third function, `recall_here`, was retired. It went dead when the V5-3 vault-backend removal made it always return `""`. It has no live caller. The crickets' documenter sub-agent uses `harness_memory.py`'s own `documenter-context` CLI verb instead.

## ⚡ Quick Reference

| Function | Signature | Returns | Memory absent → |
|---|---|---|---|
| `offer_save_here` | `offer_save_here(context, candidate)` | `list[dict]` — `[enriched_candidate]` (advisory; never persists) | `[]` |
| `state_path` | `state_path(context, which)` | `Path` — active PLAN/progress path | repo-local `<project_root>/.harness/<file>` (never `None`) |

> [!IMPORTANT]
> **Read-only invariant.** The seam performs no writes. It imports the engine's public readers. It never imports or calls any write path. This makes "the seam is read-only" literally true. The `offer_save_here` function is **advisory** ([LC-2]). It returns save *candidates*. Persistence stays on the existing `/memory save` path (`harness_memory.offer_save` / the `offer-save` CLI verb).

## The shared `context` dict

You pass a `context` dict (or `None`) to both functions. The keys are optional:

| Key | Used by | Meaning | Default |
|---|---|---|---|
| `cwd` | both | project root | the process cwd |
| `phase` | passed through by `offer_save_here` | dev-loop phase, attached to the candidate if present | (none) |
| `plan` | `state_path` | named-plan slug (`"foo"` → `PLAN-foo.md` / `progress-foo.md`) | the active/default plan |

## `offer_save_here(context, candidate)`

You use this to surface what *could* be saved from this context. You do this without saving it ([LC-2]). The function enriches the candidate with the resolved project slug and the vault target. It copies the candidate. It never mutates it. This lets you invoke the existing `/memory save` path.

| Parameter | Type | Detail |
|---|---|---|
| `context` | `dict \| None` | `cwd` selects the project root; `phase` is passed through onto the candidate if present. |
| `candidate` | `dict` (or any) | The caller's proposed save — shaped like the `offer-save` verb's inputs (e.g. `{"kind", "slug", "body", "confidence"?, "confidence_reason"?}`). Passed through, **not validated**: the seam stays minimal and lets the caller decide. A non-dict is wrapped as `{"body": candidate}`. |

| Condition | Result |
|---|---|
| Memory present and a project resolves | `[enriched_candidate]` — the candidate plus `project` (slug), an optional passed-through `phase`, and `target` (the vault path as a string, or `None`). |
| Memory / vault absent, no project resolves, or `candidate` empty | `[]`. |

It **never persists** data. It imports no write path. It calls no write path. You (the caller) or the engine's reflection judge save-worthiness. The seam deliberately avoids judging this.

## `state_path(context, which)`

You use this to resolve the harness state path for `which` in the current context. It wraps `resolve_project`, `resolve_active_plan`, and `harness_state_dir`. This gives you V5-10 named-plan awareness for free.

| Parameter | Type | Detail |
|---|---|---|
| `context` | `dict \| None` | `cwd` selects the project root; `plan` (optional) names a plan via `resolve_active_plan`'s explicit-arg path. |
| `which` | `str` | `"plan"` or `"progress"` — which file of the active pair. |

| Condition | Result |
|---|---|
| Vault-backed memory present | The resolved `Path` under the vault `_harness/` dir. |
| No vault / memory configured | Repo-local degrade `<project_root>/.harness/<file>` ([LC-3]) — **never `None`**. |
| `which` not `"plan"`/`"progress"` | Raises `ValueError` — a caller bug, distinct from the absent-memory degrade. |
| `.harness/active-plan` marker present but dangling / names an unsafe slug | Propagates `harness_memory.ActivePlanError` / `ValueError` — **not** swallowed. |

> [!WARNING]
> The corrupt-marker case acts as a deliberate loud-fail safety property (V5-10 Risk #7). It stays distinct from the absent-memory degrade. You could mis-bind the worker to another plan if it silently degraded there. The seam lets the exception propagate. It does not fall back to repo-local.

## `python -m` entrypoint

This acts as a thin shell shim ([LC-1]). You use it to expose the same two functions to non-Python hosts. It always exits `0` on the graceful-no-op paths. This ensures your process never wedges on a memory-absent seam.

| Subcommand | Flags | Emits |
|---|---|---|
| `state-path` | `which` (positional: `plan`/`progress`), `--cwd`, `--plan` | the resolved path on stdout |
| `offer-save-here` | `--cwd`, `--phase`, `--kind` (req), `--slug` (req), `--body-file` (`-` = stdin), `--confidence`, `--confidence-reason` | the advisory candidate list as indented JSON |

```bash
python3 scripts/process_seam.py state-path plan
python3 scripts/process_seam.py offer-save-here --kind decision --slug foo --body-file -
```

The Python module forms the contract. The entrypoint gives you a convenience for shell callers.

## Related

- [Memory↔process seam](Memory-Process-Seam) — This explains why the seam exists. It details the one-way dependency. It outlines the graceful-no-op philosophy.
- [CI gates](CI-Gates) — This shows the `check-one-way-imports` gate's `process-seam` rule. This rule enforces the one-way edge. (CONS-1 merged the former standalone `check-process-seam-import-direction.sh` into this config-driven Python checker).
- [AgentMemory context payload](AgentMemory-Context-Payload) — This defines the read-only memory contract the seam composes over.
- [AgentM HLD — V5 unbundling](agentm-hld) — This records the decision that introduced the seam concept.
