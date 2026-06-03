# ADR 0009 — On-host state-mode config: where vault-vs-local is decided

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-03

## Context

Hardening I made the harness runnable on a single repo with no MemoryVault — every phase write lands in `<repo>/.harness/` instead of routing through `<vault>/projects/<slug>/_harness/`. Reaching that point forced an unavoidable question: **where does the "is this machine vault-backed or vault-less?" signal live, and who sets it?**

The harness already carried two pieces of state that bear on this. `.agentm-config.json` is the single on-host config file — it holds `vault_path`, the install-`mode` (`source` | `release`), `source_clones`, and `fragments`, and it is read without ever touching a vault. Separately, an earlier iteration wrote a `.project-mode` marker *inside the vault* (`<vault>/_harness/`) and carried a `harness_state_mode` field in the vault-side repo-registry. Both vault-side signals predate the vault-less requirement, and both became liabilities the moment the harness had to resolve its mode on a machine with no vault mounted: you cannot read a marker out of a store you do not have.

**Open questions the decision resolves:**

- Where is the state mode (vault vs. local) configured — on-host, in-vault, or per-repo?
- Reuse the existing `mode` key, or introduce a new one?
- Is the mode set explicitly, or inferred from the environment (e.g. `vault_path == null`)?
- In what order are the candidate signals resolved when more than one is present?

## Decision

### 1. The state mode is configured on-host only — `state_mode` in `.agentm-config.json`, with an optional per-repo marker override

The mode is a device-level `"state_mode"` key (`"local"` | `"vault"`) in `.agentm-config.json`. The repo-local marker `<repo>/.harness/.project-mode` is the optional higher-precedence per-repo override. **No configuration lives in the vault.**

The governing principle: *the vault is where data sits, not how agentm is configured to run.* `.agentm-config.json` is the natural home — it is already the single on-host config file, already read vault-free, and already carries every other run-shaping field. Putting `state_mode` there means the vault-less adopter sets it **once** on the device and is done, rather than re-stating it per repo.

**Why not an in-vault marker.** The retired design read a `.project-mode` marker from `<vault>/_harness/` and stored a `harness_state_mode` field in the vault repo-registry. Both put *configuration inside the data store*, and both were structurally unreachable on a vault-less machine — the exact machine the feature targets. This release removed both: the migrate tool now writes the repo-local marker instead of the in-vault one, and the `harness_state_mode` registry field (which was write-only — never read by any resolution path) was deleted as dead weight.

### 2. A new `state_mode` key, not an overload of the existing `mode`

`mode` already means *install* mode (`source` vs. `release`) — install provenance, a different axis entirely from where state is written. Overloading it would conflate "how this harness was installed" with "where this harness writes state." A dedicated `state_mode` keeps the two axes independent and each self-describing.

**Why not overload `mode`:** it reads cleanly today only because there happen to be two values; the moment a third install mode or a third state location appears, the conflated key becomes ambiguous and every reader has to know which bits mean what. Separate axes, separate keys.

### 3. The mode is an explicit field — never inferred from a missing `vault_path`

Resolution never infers `local` from `vault_path == null`. A null `vault_path` is **ambiguous** between *never configured* and *transiently unreachable* — e.g. Google Drive not yet mounted at session start. Silently flipping a transiently-unreachable session into local mode would split state across two stores: some writes land in the vault, some land repo-local, and the two never reconcile. That is the V4 #35 class of failure. The mode must be an explicit field that a human or the installer sets, so an unreachable vault fails loudly (vault-first + `ValueError` guard) instead of silently degrading.

**Why not infer from the environment:** inference is convenient exactly until the environment is in a transient state, at which point it is actively dangerous. An explicit signal is the only one that distinguishes "I chose local" from "my vault is late to mount."

### 4. Per-repo override is available, but the device default is the primary surface

The earlier status quo (Hardening I task 2) was per-project-only: every repo carried its own marker. That forces per-repo ceremony on the vault-less adopter, for whom the answer is uniformly "local" across every repo on the machine. The device-level `state_mode` makes "this whole machine is vault-less" a one-time setting; the repo-local marker remains for the genuine minority case where a single repo wants a different mode than the device default.

**Why not per-project-only:** it optimizes for the rare mixed-mode machine at the cost of the common uniform-mode machine. Default to the common case; keep the override for the exception.

### 5. Resolution order (DC-2)

`_read_project_mode()` resolves in strict precedence:

1. **Repo-local marker** — `<repo>/.harness/.project-mode`, if present (the per-repo override).
2. **Device `state_mode`** — `.agentm-config.json`, if set (the device default).
3. **Neither** — vault-first, guarded by a `ValueError` so a missing/unreachable vault fails loudly rather than silently flipping to local.

## Consequences

**Positive**

- **The vault-less adopter configures once.** `install.sh --local-state` (or `agentm_config.py --state-mode local`) writes the device default a single time; every repo on the machine inherits it without per-repo setup.
- **Configuration is fully reachable without a vault.** Both layers (`.agentm-config.json` + the repo-local marker) live on-host, so the mode resolves on a machine that has never had a vault mounted.
- **The two axes stay independent.** Install provenance (`mode`) and state location (`state_mode`) never collide; each key is self-describing and independently auditable.
- **Transient vault unreachability fails loudly.** Because the mode is explicit, a vault that is configured-but-not-yet-mounted raises instead of silently splitting state — the V4 #35 failure class is structurally excluded.
- **Dead vault-side config was removed**, not left to rot: the in-vault `.project-mode` marker and the write-only `harness_state_mode` registry field are gone, narrowing the surface a future reader has to reason about.

**Negative**

- **Two layers to reason about.** A reader debugging "why did this repo resolve to local?" has to check the repo-local marker *and* the device default *and* their precedence. Mitigation: the resolution order is fixed and documented (decision #5), and the common case is a single device setting with no marker.
- **The device default is invisible from inside a repo.** A repo carries no record of the machine-level default unless it has its own marker; moving the repo to a differently-configured machine changes its resolved mode. This is intended (the mode describes the machine, not the repo) but can surprise.
- **An explicit setter is required where inference would have been zero-config.** The vault-less adopter must run one command rather than relying on the harness noticing the absence of a vault. The split-brain risk makes this the right trade, but it is a trade.

**Load-bearing assumptions** (re-check on every major-version bump)

- **`.agentm-config.json` remains the single on-host config file, read vault-free.** If config ever fragments across multiple files or a config-resolution layer is introduced, re-audit whether `state_mode` still belongs here.
- **A null `vault_path` stays ambiguous between never-set and transiently-unreachable.** If the config ever gains a way to distinguish those two states unambiguously, re-audit whether inference becomes safe.
- **One vault per device.** The model assumes a single device-level default. If multi-vault-per-device becomes a use case, the device-level `state_mode` is no longer expressive enough.

**Re-audit triggers** (specific events that should fire a fresh look at this ADR)

- A **third config axis** appears that tempts re-overloading `mode` — re-audit the one-key-per-axis discipline.
- **Claude Code (or another host) ships a config-resolution primitive** that changes where on-host config should canonically live — re-audit whether `state_mode` should migrate to it.
- **Multi-vault-per-device** becomes a real use case — re-audit whether a single device-level default suffices or whether the mode needs to be vault-scoped.
- The **per-repo marker proves to be the common path, not the device default** (operators routinely override per-repo) — re-audit whether the device-default-primary framing was the right call.

## Related

- [Single-repo state mode](../Single-Repo-State-Mode) — the feature page: why vault-less mode exists and the full resolution model.
- [Run the harness without a vault](../../how-to/Run-Without-A-Vault) — the operator recipe.
- [Installer CLI reference](../../reference/Installer-CLI) — the `--local-state` flag and the `agentm_config.py --state-mode` setter that write `state_mode`.
- [ADR 0007 — Auto-context into harness phases](0007-auto-context-into-harness-phases) — the V4 #35 split-brain failure class this ADR's explicit-mode requirement guards against.
