# How to choose and configure the storage backend

> [!NOTE]
> **Status: pending** (part-5 **task 1 of 4** shipped) — describes the **V5-1 part 5** behavior (`.harness/PLAN.md` — `selection-and-fail-loud`). **Shipped (task 1):** the `storage.backend` config key (the `--storage-backend` setter) and the [selection resolver](Storage-Seam#the-selection-resolver). **Not yet in `main`:** the kernel capabilities-read (task 2), the polished install-the-plugin [fail-loud refusal](Storage-Seam#the-fail-loud-guard) (task 3 — the resolver currently raises only a bare placeholder error), and the `doctor` storage preview (task 4). Step 2 below is filled from what shipped; steps 1 and 3 (the `doctor` preview) and the Verify block stay reserved until task 4. Part 5 ships **selection + the fail-loud guard only** — it does **not** route the live memory engine through the chosen backend (the engine cutover is a separate, later step beyond V5-1).
>
> **Goal:** Tell agentm which [storage backend](Storage-Seam) the memory engine should use, and confirm — before any memory operation could refuse — that the named backend's plugin is installed.
> **Prereqs:** agentm with V5-1 part 5 installed; `python3` on `PATH`; the [config CLI](Installer-CLI#config-cli--agentm_configpy) (`scripts/agentm_config.py`).

The selected backend resolves from the on-host `.agentm-config.json` ([config reference](Installer-CLI#config-cli--agentm_configpy)). The resolution chain: an explicit `storage.backend` value wins; otherwise a fresh install (no `vault_path`) resolves to `device-local`; otherwise an existing `vault_path` resolves to the built-in `vault`. If `storage.backend` names a backend whose plugin is **not** installed, the engine **refuses the memory operation with a clear "install the `<name>` backend plugin" error** — it never silently falls back. Use the `doctor` preview to see the selected backend and its plugin-installed state *before* that refusal could bite.

## Steps

1. Inspect what selection resolves today (no config change):

   ```
   _Filled by /work once the doctor storage preview ships (part-5 task 4) — selected
   backend, whether its plugin is registered, device-local root writable._
   ```

2. Set an explicit backend (optional — leave unset to use the fresh/vault default):

   ```sh
   python3 scripts/agentm_config.py --storage-backend <name>
   # e.g.
   python3 scripts/agentm_config.py --storage-backend device-local
   ```

   `<name>` is a registered protocol name (`device-local`, `vault`, or a plugin-provided name). The setter validates the value is non-empty only — it does **not** check the backend is installed, so you can configure a backend whose plugin lands later. It is idempotent (a silent no-op when the value is unchanged) and round-trips through `--get storage.backend` / `--unset storage.backend`. Leave it unset to let the resolver pick: a fresh install (no `vault_path`) resolves `device-local`; an existing `vault_path` resolves `vault`.

3. Re-run the `doctor` storage check to confirm the named backend's plugin is registered before relying on it:

   ```
   _Filled by /work once the doctor storage preview ships (part-5 task 4) — the
   preview again, showing plugin-installed = yes._
   ```

## Verify

_Filled by `/work` — confirm the resolver picks the expected backend, and that a `storage.backend` naming an uninstalled plugin produces the loud "install the `<name>` backend plugin" refusal rather than a silent `device-local` fallback._

## Related

- [Storage seam](Storage-Seam) — the verb-by-verb contract, the `BackendRegistry`, and the [backend selection (part 5)](Storage-Seam#backend-selection-part-5) surface.
- [The memory↔storage seam](Memory-Storage-Seam) — why selection fails loud instead of demoting, and why the engine cutover is deferred beyond V5-1.
- [Installer CLI reference](Installer-CLI#config-cli--agentm_configpy) — the `agentm_config.py` config CLI that stores `storage.backend` alongside `vault_path` / `state_mode`.
- [Run without a vault](Run-Without-A-Vault) — the related `state_mode` / device-local path on a vault-less machine.
