# How to choose and configure the storage backend

> [!NOTE]
> **Status: implemented** (part-5 **tasks 1–4 of 4 shipped**) — describes the **V5-1 part 5** behavior (`.harness/PLAN.md` — `selection-and-fail-loud`). **Shipped:** the `storage.backend` config key (the `--storage-backend` setter, task 1), the [selection resolver](Storage-Seam#the-selection-resolver) (task 1), the kernel [capabilities-read](Storage-Seam#the-capabilities-type) (task 2), the polished install-the-plugin [fail-loud refusal](Storage-Seam#the-fail-loud-guard) (task 3), and — task 4, this page — the [`doctor` storage preview](#what-okwarnfail-mean). Part 5 ships **selection + the fail-loud guard + the doctor preview only** — the engine cutover (routing `harness_state_dir` / `read_state_file` / `write_state_file` / `phase_recall` / `resolve_documenter_context` to device-local exclusively) shipped separately in **V5-3 ([ADR 0018](0018-v5-3-storage-cutover))** as part of v5.5.0.
>
> **Goal:** Tell agentm which [storage backend](Storage-Seam) the memory engine should use, and confirm — before any memory operation could refuse — that the named backend's plugin is installed.
> **Prereqs:** agentm with V5-1 part 5 installed; `python3` on `PATH`; the [config CLI](Installer-CLI#config-cli--agentm_configpy) (`scripts/agentm_config.py`).

The selected backend resolves from the on-host `.agentm-config.json` ([config reference](Installer-CLI#config-cli--agentm_configpy)). The resolution chain: an explicit `storage.backend` value wins; otherwise an existing `vault_path` resolves to the built-in `vault`; otherwise a fresh install (no `vault_path`) resolves to `device-local`. If `storage.backend` names a backend whose plugin is **not** installed, the engine **refuses the memory operation with a clear "install the `<name>` backend plugin" error** — it never silently falls back. Use the `doctor` preview to see the selected backend and its plugin-installed state *before* that refusal could bite.

## Steps

1. Inspect what selection resolves today (no config change), via the read-only `doctor` storage preview:

   ```sh
   python3 scripts/backend_selection.py --doctor
   # or, in a host that ships the doctor skill:
   /doctor
   ```

   It prints a single status line — `storage [OK] …`, `storage [WARN] …`, or `storage [FAIL] …` — naming the selected backend, where the selection came from (explicit `storage.backend`, an existing `vault_path`, or the fresh-install default), and whether that backend's plugin is registered. The preview is **read-only**: it resolves the selection *without constructing a backend* (construction would `mkdir` the `device-local` root), so running it never mutates anything. See [what `[OK]`/`[WARN]`/`[FAIL]` mean](#what-okwarnfail-mean) below. Exit code is `1` only on `[FAIL]`; `[OK]` and `[WARN]` exit `0`.

2. Set an explicit backend (optional — leave unset to use the fresh/vault default):

   ```sh
   python3 scripts/agentm_config.py --storage-backend <name>
   # e.g.
   python3 scripts/agentm_config.py --storage-backend device-local
   ```

   `<name>` is a registered protocol name (`device-local`, `vault`, or a plugin-provided name). The setter validates the value is non-empty only — it does **not** check the backend is installed, so you can configure a backend whose plugin lands later. It is idempotent (a silent no-op when the value is unchanged) and round-trips through `--get storage.backend` / `--unset storage.backend`. Leave it unset to let the resolver pick: a fresh install (no `vault_path`) resolves `device-local`; an existing `vault_path` resolves `vault`.

3. Re-run the `doctor` storage check to confirm the named backend's plugin is registered before relying on it:

   ```sh
   python3 scripts/backend_selection.py --doctor
   ```

   A clean result names the backend you just set and reports it registered:

   ```
   storage [OK] selected backend 'device-local' (configured (storage.backend)) — registered; root ~/.agentm/memory writable
   ```

   If instead you set a `storage.backend` whose plugin is **not** installed, the preview returns `[FAIL]` with the install-the-plugin message — the same one the engine would raise at the first memory operation:

   ```
   storage [FAIL] storage backend 'foo' is configured (storage.backend) but no installed plugin registers it. Install the plugin that provides the 'foo' backend, or set storage.backend to an installed backend (currently registered: device-local, vault).
   ```

   The fix is in the message: install the named plugin, or set `storage.backend` back to a registered name with the setter from step 2.

## What `[OK]`/`[WARN]`/`[FAIL]` mean

The preview emits exactly one status row. The same `--doctor` exit code drives whether the `doctor` skill reports a hard failure:

| Status | Exit | When | What to do |
|---|---|---|---|
| `[OK]` | `0` | Selected backend is registered and ready — `vault` seeded from the resolved `vault_path`, a writable `device-local` root, or a registered third-party protocol. | Nothing. Selection will succeed. |
| `[WARN]` | `0` | `device-local` is selected but its root (`~/.agentm/memory/`) is not writable. Preventive, never build-blocking — the engine will still try, but the write would fail. | Fix the directory permissions before relying on memory writes. |
| `[FAIL]` | `1` | The selected backend has no registered plugin, **or** `vault` is selected with no resolvable `vault_path` to seed it, **or** the config file exists but is unparseable / names a non-string `storage.backend`. | Follow the printed message — install the plugin, set `vault_path`, or correct the config value. |

> [!IMPORTANT]
> **The `[FAIL]` preview is byte-identical to the engine's live refusal.** The preview and the runtime [fail-loud guard](Storage-Seam#the-fail-loud-guard) both build the install-the-plugin message from the same `_install_plugin_message` helper, so the preview cannot drift from what the engine would actually refuse with. `doctor`'s storage check is the *one* structural check that legitimately `[FAIL]`s — it is the fail-loud preview shown **before** the engine itself refuses, not a second-guess of a separate enforcer.

## Verify

```sh
# 1. Default selection on a vault-configured host resolves 'vault', registered + seeded:
python3 scripts/backend_selection.py --doctor   # → storage [OK] selected backend 'vault' (existing vault_path) — registered; seeded from <vault>; exit 0

# 2. An uninstalled plugin fails loud (no silent device-local fallback):
python3 scripts/agentm_config.py --storage-backend foo
python3 scripts/backend_selection.py --doctor   # → storage [FAIL] storage backend 'foo' … Install the plugin …; exit 1
python3 scripts/agentm_config.py --unset storage.backend   # restore
```

The `[FAIL]` row proving the preview never silently demotes to `device-local` is pinned by `test_no_silent_device_local_fallback` (the `TestFailLoud` class) for the engine guard and by `TestStoragePreview` for the preview surface ([`scripts/test_backend_selection.py#L324`](https://github.com/alexherrero/agentm/blob/main/scripts/test_backend_selection.py#L324)).

## Implementation

| Surface | Where |
|---|---|
| `StoragePreview` NamedTuple (`status` / `protocol` / `line`) | [`scripts/backend_selection.py#L227`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L227) |
| `storage_preview(...)` — read-only resolution, never constructs a backend | [`scripts/backend_selection.py#L255`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L255) |
| `[FAIL]` message reuses the guard's `_install_plugin_message` (byte-identical) | [`#L293`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L293) (guard at [`#L196`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L196)) |
| `device-local` root writability probe (`_root_is_writable`) | [`scripts/backend_selection.py#L240`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L240) |
| `--doctor` CLI entry + status→exit mapping (`_doctor_main`) | [`scripts/backend_selection.py#L341`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L341), runnable via `if __name__ == "__main__"` at [`#L365`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L365) |
| `doctor` skill structural check `4d` | [`harness/skills/doctor.md`](https://github.com/alexherrero/agentm/blob/main/harness/skills/doctor.md) (Claude Code adapter: `adapters/claude-code/skills/doctor/SKILL.md`) |
| Tests — `TestStoragePreview` (6 cases) | [`scripts/test_backend_selection.py#L324`](https://github.com/alexherrero/agentm/blob/main/scripts/test_backend_selection.py#L324) |

## Related

- [Storage seam](Storage-Seam) — the verb-by-verb contract, the `BackendRegistry`, and the [backend selection (part 5)](Storage-Seam#backend-selection-part-5) surface.
- [The memory↔storage seam](Memory-Storage-Seam) — why selection fails loud instead of demoting, and why the engine cutover is deferred beyond V5-1.
- [Installer CLI reference](Installer-CLI#config-cli--agentm_configpy) — the `agentm_config.py` config CLI that stores `storage.backend` alongside `vault_path` / `state_mode`.
- [Run without a vault](Run-Without-A-Vault) — the related `state_mode` / device-local path on a vault-less machine.
