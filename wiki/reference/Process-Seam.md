# Process seam reference

The memory↔process client seam ([`scripts/process_seam.py`](https://github.com/alexherrero/agentm/blob/main/scripts/process_seam.py)) — a small, **read-only**, **graceful-no-op** view a *process* (the crickets developer-workflows phases today; the V5-9 MCP server tomorrow) calls instead of reaching into the memory engine's internals. It exports three functions composed only from the DC-7-frozen public memory readers (`resolve_project`, `phase_recall`, `resolve_active_plan`, `harness_state_dir`, `is_available`) — never a write path. The importable Python module is the contract ([LC-1]); the `python -m` entrypoint is a convenience shim for non-Python shell callers.

## ⚡ Quick Reference

| Function | Signature | Returns | Memory absent → |
|---|---|---|---|
| `recall_here` | `recall_here(context, *, query=None, limit=None)` | `str` — budgeted recall markdown | `""` (never raises) |
| `offer_save_here` | `offer_save_here(context, candidate)` | `list[dict]` — `[enriched_candidate]` (advisory; never persists) | `[]` |
| `state_path` | `state_path(context, which)` | `Path` — active PLAN/progress path | repo-local `<project_root>/.harness/<file>` (never `None`) |

> [!IMPORTANT]
> **Read-only invariant.** The seam performs no writes. It imports the engine's public readers and never imports or calls any write path — that is what makes "the seam is read-only" literally true. `offer_save_here` is **advisory** ([LC-2]): it returns save *candidates*; persistence stays on the existing `/memory save` path (`harness_memory.offer_save` / the `offer-save` CLI verb).

## The shared `context` dict

All three functions take a `context` dict (or `None`). Keys are optional:

| Key | Used by | Meaning | Default |
|---|---|---|---|
| `cwd` | all three | project root | the process cwd |
| `phase` | `recall_here`, passed through by `offer_save_here` | dev-loop phase scoping the recall | `"work"` |
| `plan` | `state_path` | named-plan slug (`"foo"` → `PLAN-foo.md` / `progress-foo.md`) | the active/default plan |

## `recall_here(context, *, query=None, limit=None)`

Recall memory context relevant to the current working context. Resolves `cwd` → project slug, then calls the engine's public `phase_recall` scoped to that project, returning the budgeted markdown summary.

| Parameter | Type | Detail |
|---|---|---|
| `context` | `dict \| None` | `cwd` selects the project root; `phase` selects the recall scope. |
| `query` | `str \| None` | **Reserved / forward-compat.** The frozen public recall is a phase+project read with no free-text/semantic query; that arrives with the V6 vector index. Accepted today so callers can be written against the final signature, but it is a documented **no-op** — passing it neither errors nor filters. |
| `limit` | `int \| None` | Recall budget in tokens (maps to `phase_recall`'s `budget`); `None` uses the phase default. |

| Condition | Result |
|---|---|
| Memory present | Budgeted recall markdown for the resolved phase + project. |
| Memory / vault absent | `""` (empty string). |
| Unknown / absent `phase` | Degrades to `"work"` scope — **never raises**. |

Valid phases are the dev-loop set: `setup`, `plan`, `work`, `review`, `release`, `bugfix`, `documenter`. Any other value degrades to `"work"`.

## `offer_save_here(context, candidate)`

Surface what *could* be saved from this context — without saving it ([LC-2]). Enriches the candidate (copy, never mutate) with the resolved project slug + vault target so the caller can invoke the existing `/memory save` path.

| Parameter | Type | Detail |
|---|---|---|
| `context` | `dict \| None` | `cwd` selects the project root; `phase` is passed through onto the candidate if present. |
| `candidate` | `dict` (or any) | The caller's proposed save — shaped like the `offer-save` verb's inputs (e.g. `{"kind", "slug", "body", "confidence"?, "confidence_reason"?}`). Passed through, **not validated**: the seam stays minimal and lets the caller decide. A non-dict is wrapped as `{"body": candidate}`. |

| Condition | Result |
|---|---|
| Memory present and a project resolves | `[enriched_candidate]` — the candidate plus `project` (slug), an optional passed-through `phase`, and `target` (the vault path as a string, or `None`). |
| Memory / vault absent, no project resolves, or `candidate` empty | `[]`. |

It **never persists** — it imports and calls no write path. Save-worthiness is the caller's (or the engine's reflection's) call, deliberately not judged here.

## `state_path(context, which)`

Resolve the harness state path for `which` in the current context. Wraps `resolve_project` + `resolve_active_plan` (so V5-10 named-plan awareness is free) + `harness_state_dir`.

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
> The corrupt-marker case is a deliberate loud-fail safety property (V5-10 Risk #7), distinct from the absent-memory degrade. Silently degrading there could mis-bind the worker to another plan, so the seam lets the exception propagate rather than falling back to repo-local.

## `python -m` entrypoint

A thin shell shim ([LC-1]) exposing the same three functions to non-Python hosts. Always exits `0` on the graceful-no-op paths so a process never wedges on a memory-absent seam.

| Subcommand | Flags | Emits |
|---|---|---|
| `recall-here` | `--cwd`, `--phase`, `--query`, `--limit` | the recall markdown on stdout (nothing on the empty degrade) |
| `state-path` | `which` (positional: `plan`/`progress`), `--cwd`, `--plan` | the resolved path on stdout |
| `offer-save-here` | `--cwd`, `--phase`, `--kind` (req), `--slug` (req), `--body-file` (`-` = stdin), `--confidence`, `--confidence-reason` | the advisory candidate list as indented JSON |

```bash
python3 scripts/process_seam.py recall-here --phase work
python3 scripts/process_seam.py state-path plan
python3 scripts/process_seam.py offer-save-here --kind decision --slug foo --body-file -
```

The importable module is the first-class contract; the entrypoint is a convenience shim, **not** a first-class CLI.

## Related

- [Memory↔process seam](Memory-Process-Seam) — why the seam exists, the one-way dependency, and the graceful-no-op philosophy.
- [CI gates](CI-Gates) — the `check-process-seam-import-direction` gate that enforces the one-way edge.
- [AgentMemory context payload](AgentMemory-Context-Payload) — the read-only memory contract the seam composes over.
- [AgentM HLD — V5 unbundling](agentm-hld) — the decision that introduced the seam concept.
