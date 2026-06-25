# The memory↔process seam

Why a *process* — a dev-loop phase, an MCP server — talks to the memory engine through a small stable client rather than reaching into engine internals, and why that client dependency is one-directional and gate-enforced. The seam concept was introduced by the [AgentM HLD — V5 unbundling](agentm-hld); this note explains the shape it took. For the function-by-function contract, see [Process seam](Process-Seam).

## What the seam is for

The V5 unbundling moved the phase loop (Setup · Plan · Work · Review · Release · Bugfix) out of agentm and into the companion crickets plugins, leaving agentm as the durable-state substrate and memory engine those phases run on. That split created a question it did not answer: *how* does a phase — now living in a different repo — read recall context or resolve where `PLAN.md` lives?

The tempting answer is "import `harness_memory` and call whatever you need." That couples every consumer to the engine's private shape: a phase would reach for an internal helper, the engine would refactor it, and the phase would break — across a repo boundary, where the breakage is hardest to see coming. The seam is the deliberate alternative. It is a single small client (`scripts/process_seam.py`) exposing exactly three operations a process actually needs:

- **recall here** — "given where I'm working, what does memory know?"
- **offer save here** — "here's something I might save; tell me where it would go" (advisory only — it never saves).
- **state path here** — "where does this project's `PLAN.md` / `progress.md` live?"

Every one of those composes only the engine's *frozen public readers* (the DC-7 surface: `resolve_project`, `phase_recall`, `resolve_active_plan`, `harness_state_dir`, `is_available`). A consumer that needs something the frozen API lacks is a separate engine change, not a quiet widening of the seam. The engine keeps the freedom to refactor everything behind that public surface; the process only ever sees the seam.

## Why the dependency is one-directional

The seam imports the engine. The engine must **never** import the seam back.

This is not a style preference — it is what keeps the seam a *seam*. The whole point is a clean layering: memory is the lower, stable substrate; the process is the upper, swappable layer that depends on it. A back-edge — any memory-engine module importing `process_seam` — would invert that. It would turn a one-way client dependency into a cycle, and worse, it would let an engine change reach up into process concerns: the exact coupling the seam exists to prevent. Once the engine knows about its callers, "refactor freely behind the public surface" stops being true, and the V5 unbundling's clean substrate/plugin split quietly rots.

```
  process (crickets phases, V5-9 MCP server)
        │  imports
        ▼
  process_seam.py   ──imports──►  harness_memory (frozen public readers)
        ▲                              │
        └──────────  NEVER  ───────────┘
              (engine must not import the seam)
```

So the rule is stated plainly ([LC-4]): **memory never imports the process.** And because a rule that only lives in prose decays, it ships as an executable gate, not a guideline.

## Why that's gate-enforced

[`check-process-seam-import-direction.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-process-seam-import-direction.sh) is the enforcement. It scans agentm's Python automation surfaces for any module that imports `process_seam` and fails on a hit. The reasoning that makes a simple scan sufficient: within agentm, the seam's *only* legitimate importer is its own test suite. The designed consumers — the crickets phases, the future MCP server — live **outside** this repo ([LC-5]). So any in-repo importer that isn't a test is, by construction, the engine reaching for the seam — a forbidden back-edge. The gate excludes the tests (they import by design) and carries an explicit, currently-empty `SEAM_CONSUMERS` allowlist, so the day a reviewed in-repo process-side consumer appears, the back-edge question gets answered in the open rather than by a silent gate edit. It rides the local battery (`check-all.sh`) and runs cross-OS via the auto-discovered unit-tests step, since its subprocess tests live in `test_process_seam.py`. See [CI gates](CI-Gates) for the row.

A subtle pairing reinforces the layering: agentm **defines and ships** the seam, but its consumers **adopt** it crickets-side in a separate plan ([LC-5]). The producer and the consumers are deliberately on opposite sides of the repo boundary — the same boundary the one-way rule protects.

## The graceful-no-op philosophy

The seam is built so a memory-absent install still works. agentm must run on machines without a vault mounted — CI, a fresh device, a contributor who never set one up. If the seam hard-failed when memory was unavailable, every process that called it would wedge, and the optional memory engine would become a hard dependency through the back door.

So **every function degrades** rather than raising on the absent-memory path:

- `recall_here` returns `""` when no engine or vault is configured — and an unknown or absent phase degrades to the `"work"` scope rather than raising.
- `offer_save_here` returns `[]` when memory is absent or there's nothing to offer.
- `state_path` degrades to repo-local `<project_root>/.harness/<file>` ([LC-3]) — never `None` — so a vault-less repo still resolves its plan and progress files.

There is one deliberate exception to the degrade, and it is a safety property, not an oversight: when `state_path` finds a present-but-corrupt `.harness/active-plan` marker — dangling, or naming an unsafe slug — it lets the exception propagate (V5-10 Risk #7). Silently degrading there could mis-bind a worker to the wrong plan, which is worse than a loud failure. The distinction is the whole design in miniature: *absent* memory degrades quietly; *corrupt* state fails loudly.

The other half of the same philosophy is the read-only stance. The seam never writes — `offer_save_here` is advisory, returning candidates the caller hands to the existing `/memory save` path ([LC-2]). A read-only client that degrades gracefully is one a process can call from anywhere without first checking whether memory exists, and without any risk that calling it mutates the vault.

## Related

- [Process seam](Process-Seam) — the function-by-function reference (signatures, degrade contracts, the `python -m` entrypoint).
- [CI gates](CI-Gates) — the `check-process-seam-import-direction` gate enforcing the one-way edge.
- [AgentM HLD — V5 unbundling](agentm-hld) — the decision that split the phase loop out and introduced the seam concept.
- [Single-repo (vault-less) state mode](Single-Repo-State-Mode) — the broader "runs without a vault" stance the seam's graceful degrade participates in.
