# CI gates reference

Every gate that runs on `push` to `main` and on every `pull_request:`, with the invariant each one proves and the script behind it. Three per-OS workflows run in parallel. Run the deterministic subset locally in one command before every commit:

```bash
bash scripts/check-all.sh
```

## ⚡ Quick Reference

| Workflow | Runs on | Jobs |
|---|---|---|
| [`[T] Linux Tests`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-linux.yml) | `ubuntu-latest` | install-smoke + adapter-parity + validate + check-references + check-wiki + unit tests + **verify-v4** + **verify-orchestration-briefing** + **verify-phases** + **verify-memory-roundtrip** + syntax + **lib-parity** + **pii-guardrails** (check-no-pii + gitleaks) + dogfood-workflows |
| [`[T] Mac Tests`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-mac.yml) | `macos-latest` | install-smoke + validate + check-references + unit tests + **verify-v4** + **verify-orchestration-briefing** + **verify-phases** + **verify-memory-roundtrip** + syntax (both shells) + **lib-parity** + **check-no-pii** |
| [`[T] Windows Tests`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-windows.yml) | `windows-latest` | install-smoke (pwsh) + installer-boundary + validate + check-references + pwsh syntax + **lib-parity** + **check-no-pii** + unit tests |

## What each gate proves

| Gate | Invariant | Script |
|---|---|---|
| install-smoke | Fresh install succeeds; re-run is idempotent; `--update` refreshes managed files but preserves user edits to `wiki/` and `AGENTS.md`; test infra never propagates to scratch. | [`scripts/smoke-install-bash.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/smoke-install-bash.sh), [`scripts/smoke-install-pwsh.ps1`](https://github.com/alexherrero/agentm/blob/main/scripts/smoke-install-pwsh.ps1) |
| post-install integrity | Hook-command paths resolve; every `.sh`/`.ps1` parses; bash installer produces bash commands, pwsh installer produces pwsh commands; `settings.json` has the expected schema; `.harness` state files are valid. | [`scripts/check-integrity-bash.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-integrity-bash.sh), [`scripts/check-integrity-pwsh.ps1`](https://github.com/alexherrero/agentm/blob/main/scripts/check-integrity-pwsh.ps1) |
| adapter-parity | Every adapter ships the canonical set of phase-commands, sub-agents, and skills. | [`scripts/check-parity.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-parity.sh) |
| validate | Every TOML, YAML frontmatter, and JSON across `adapters/` and `templates/` parses and has required keys. | [`scripts/validate-adapters.py`](https://github.com/alexherrero/agentm/blob/main/scripts/validate-adapters.py) |
| check-references | Every `harness/<phases\|agents\|skills\|pipelines>/*.md` mentioned in an adapter file exists; phase-spec "dispatch the `<name>` sub-agent / invoke the `<name>` skill" lines point at a canonical spec; `settings-fragment-{bash,pwsh}.json` have matching schemas. | [`scripts/check-references.py`](https://github.com/alexherrero/agentm/blob/main/scripts/check-references.py) |
| check-wiki | Diátaxis structural rules (a–k): mode purity, ADR append-only + `Status: accepted\|superseded\|rejected`, orphan-link detection, globally-unique filenames, no banned-headings-per-mode. Runs `--strict` (blocks PRs) when `wiki/.diataxis` is present; warn-only otherwise. Shipped in v0.9.0 as part of the Diátaxis rollout ([ADR 0004](0004-diataxis-documentation-spec)). | [`scripts/check-wiki.py`](https://github.com/alexherrero/agentm/blob/main/scripts/check-wiki.py) |
| syntax | `bash -n` on every `.sh`; PowerShell AST parse on every `.ps1` across repo root + `scripts/` + `templates/` + `adapters/`. | [`scripts/check-syntax.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-syntax.sh), [`scripts/check-syntax.ps1`](https://github.com/alexherrero/agentm/blob/main/scripts/check-syntax.ps1) |
| unit tests | Every `scripts/test_*.py` (auto-discovered) passes — the memory-script logic in isolation. | `(cd scripts && python3 -m unittest discover -p 'test_*.py')` |
| check-lib-parity | `lib/install/` matches the committed checksums (byte-identical across agentm + crickets). | [`scripts/check-lib-parity.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-lib-parity.sh) |
| check-vault-lock-parity | The two copies of the vault-write protocol — `scripts/vault_lock.py` and its vendored twin `harness/skills/memory/scripts/vault_lock.py` — are sha256-identical, so the memory skill and the harness core share one canonical lock implementation. | [`scripts/check-vault-lock-parity.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-vault-lock-parity.sh) |
| check-multi-plan-naming | Locks the named-plan naming contract three ways: (1) `scripts/harness_memory.py` still exposes the named-plan resolver surface — both `resolve_active_plan` (the session→plan binder) and `harness_state_dir` (the state-dir enumerator); (2) no curated `harness/*.md` doc hard-asserts a singleton via the narrow deny-pattern — definite-article `the PLAN.md` + possessive `PLAN.md's` — which still permits every legitimate mention (a named `PLAN-<name>.md`, a `PLAN*.md` glob, a `<slug>.PLAN.md` queued file, the `vault-state-path PLAN.md` CLI example, and `PLAN.archive.*`); (3) both session-start hook twins (`harness-context-session-start.{sh,ps1}`) still glob `PLAN-*.md`, so they cannot drift apart and silently lose named-plan discovery at session boot (assertion 3, added V5-10 part 1 task 5). Scans 7 curated docs; `design/SKILL.md` is included as a regression guard. See [Named plans](Named-Plans). | [`scripts/check-multi-plan-naming.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-multi-plan-naming.sh) |
| check-worktree-slug | The worktree slug-safety invariant (V5-10 / LC-2): a worker in a `git worktree` can't see the parent's gitignored `.harness/`, so it resolves the vault slug by the **origin basename** alone (Tier 3). If the full-chain slug (an explicit `vault_project` / `github.repo` override) diverges from the origin basename, a worktree worker would silently write plans/progress under the wrong `projects/<slug>/`. Delegates to `vault_project.py check-worktree-slug` (the same resolver the `doctor` probe calls, so gate + probe never drift); no origin remote → warn-only. | [`scripts/check-worktree-slug.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-worktree-slug.sh) |
| check-no-auto-worktree | No agentm automation surface auto-spawns a worktree (V5-10 / LC-3): worktrees are an **operator-initiated** primitive (the spawn helper lives crickets-side). Scans executable surfaces (shell · python · pwsh · CI yaml) for the `git worktree add` spawn verb; read/cleanup subcommands (`list`/`remove`/`prune`) are allowed, tests + this gate's own file excluded. Proves agentm itself never creates a worktree unprompted. | [`scripts/check-no-auto-worktree.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-no-auto-worktree.sh) |
| check-process-seam-import-direction | The memory↔process edge is one-directional (V5-4 / LC-4): the process-seam client (`scripts/process_seam.py`) imports the memory engine, never the reverse. Scans agentm's Python automation surfaces (`scripts harness lib templates .github`) for any module importing `process_seam`; excludes `test_*.py` (tests import by design), `process_seam.py` itself, and the empty `SEAM_CONSUMERS` allowlist (designed consumers live crickets-side, LC-5). A hit is a forbidden back-edge that would turn the one-way client dependency into a cycle. **V5-5 bridge extension (LC-8):** also asserts no kernel toolkit script (`harness/skills/memory/scripts/`) imports `harness_memory` — a back-edge through the orchestration bridge is equally forbidden. The gate's enforcement also runs cross-OS via the auto-discovered `unit tests` step (its subprocess tests live in `test_process_seam.py`), so no separate per-OS workflow step exists. See [Memory↔process seam](Memory-Process-Seam), [Process seam](Process-Seam), and [Orchestration bridge](Orchestration-Bridge). | [`scripts/check-process-seam-import-direction.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-process-seam-import-direction.sh) |
| check-storage-seam-no-path-leak | No `pathlib.Path` crosses the memory↔storage seam (V5-1): every seam verb returns the seam's own `Locator`/`Info`, never a path, so a filesystem assumption can't reach the engine. *Static* (AST, not grep): parses each `scripts/storage_*.py` and flags any seam verb (`resolve`/`read`/`write`/`list`/`exists`/`info`/`mkdir`) whose **return annotation** references a path type, however nested (`Path`, `list[Path]`, `Path | None`, `os.PathLike`). Internal `Path` use is fine — a filesystem backend's `root / key`; only handing one back is the leak. `test_*.py` is out of the glob, so conformance fixtures (which build a `Path`-returning backend to test the gate) don't trip it. The structural sibling of `check-process-seam-import-direction`; runs cross-OS via the auto-discovered `unit tests` step (subprocess tests in `test_storage_seam.py`). See [Memory↔storage seam](Memory-Storage-Seam) and [Storage seam](Storage-Seam). | [`scripts/check-storage-seam-no-path-leak.py`](https://github.com/alexherrero/agentm/blob/main/scripts/check-storage-seam-no-path-leak.py) |
| check-no-pii | The regex PII scanner finds no personal info across the tree (this is a public repo). | [`scripts/check-no-pii.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-no-pii.sh) |
| check-no-hardcoded-vault-path | No tracked non-test file contains a hardcoded absolute vault path: (A) `/Library/CloudStorage/` literals (not shell tilde/variable expansions); (B) the retired pre-V5-3 vault root name as a path component (`/Obsidian/AgentMemory`). Ensures vault-path hygiene survives refactors — callers must resolve the live path at runtime, not bake a machine-specific or stale-root path into source. | [`scripts/check-no-hardcoded-vault-path.py`](https://github.com/alexherrero/agentm/blob/main/scripts/check-no-hardcoded-vault-path.py) |
| verify-v4 | Kernel-owned auto-orchestration contracts: config seed/parse (A), idle-chain dry-run ordering + bounded execution (E), emit gating (shifted-guard + cooldown) + atomic state write + single-writer invariant (G static) — against a throwaway scratch vault. Linux/Mac only. | [`scripts/verify-v4.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/verify-v4.sh) |
| verify-orchestration-briefing | PM-half of the orchestration push surface (V5-5 / LC-9): briefing signals (inbox · HIGH watchlist · incubator · idea-ledger · staged-adapt · both nudges) · staged-adapt surfaces-and-clears. 10 checks against a throwaway scratch vault. Linux/Mac only. Session-marker and phase-dispatch scenarios are in `verify-phases`. | [`scripts/verify-orchestration-briefing.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/verify-orchestration-briefing.sh) |
| verify-phases | A full phase lifecycle (`/setup → /plan → /work → /release`) drives its deterministic seams — state read/write, `progress.md` appends, `features.json` updates, post-phase dispatch plumbing — end-to-end on a throwaway fixture project, run twice: once vault-resident, once repo-local. Also covers session-marker scenarios (no-session / single-marker / ambiguous concurrency-safe) and post-release discover-skills chain (V5-5 / LC-9). Linux/Mac only. | [`scripts/verify-phases.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/verify-phases.sh) |
| verify-memory-roundtrip | The memory engine round-trips on a throwaway fixture vault: stub-mode `embed` (deterministic hash vector, no network/model) → `save` → `recall query` surfaces it by content → `reflect` a synthetic transcript → `vec_index` full-sync/drain builds the index → nearest-neighbor read-back → `vault_lint` clean. 12 checks; a `VERIFY_MEMORY_FAULT=drop-save` injection drives the negative path. The nearest-neighbor sub-check is conditional on the backend — asserted when the Python `sqlite3` supports `enable_load_extension` (Mac/Linux CI `pip install sqlite-vec` to exercise it), logged as SKIPPED (never silently dropped) when it falls back to keyword recall by design. Hermetic, Linux/Mac only. | [`scripts/verify-memory-roundtrip.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/verify-memory-roundtrip.sh) |
| dogfood-workflows | Every workflow the harness ships as a template under `templates/.github/workflows/` is active at the repo root, byte-identical to the template. Mirrored locally by `check-workflow-parity` (below), so a one-sided edit is caught in `check-all.sh` before the push, not as a red Linux run after it. | Inline job in [`tests-linux.yml`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-linux.yml) |
| check-workflow-parity | The local mirror of `dogfood-workflows` (above): every templated `templates/.github/workflows/*.yml` is active at the repo root, byte-identical (`diff -u`, the same comparator CI uses, so the two verdicts cannot diverge). Active workflows without a template twin (e.g. `ci-all.yml`) are out of scope — the invariant is template→active, not the reverse. One deliberate divergence from CI: zero templated workflows is a setup error (exit 2), not the vacuous pass CI's `nullglob` yields — a local gate that checked nothing must not read as green. | [`scripts/check-workflow-parity.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-workflow-parity.sh) |

## `verify-v4.sh` — kernel-contracts integration check

Checks the V4 #23 kernel-owned auto-orchestration contracts. Runs the real scripts via their CLIs against a throwaway scratch vault and asserts deterministic outputs — the integration complement to the per-function unit suite. Trimmed to 85 lines at V5-5 / LC-9 when the PM-half and Developer-half were extracted.

| Property | Detail |
|---|---|
| Isolation | A `mktemp -d` scratch vault + an exported `IDEAS_SURFACE_PATH` — never reads or writes a real vault. |
| No side effects | No network, no transcript mining, no sub-agent dispatch; self-cleans via a `trap`. |
| Coverage | **Segment A:** config seed/parse. **Segment E:** idle-chain dry-run (ordering + bounded `--max-batches`/`--limit`). **Segment G:** emit gating (shifted-guard + cooldown) · atomic state write · static single-writer check: greps `orchestration_*.py` for `^def save_state` and asserts 0 matches — making the V5-5 LC-2 single-writer invariant a gate-checked property. |
| Out of scope | Briefing signals, staged-adapt, nudge triggers, phase-dispatch, and session-marker scenarios — those moved to `verify-orchestration-briefing.sh` (PM-half) and `verify-phases.sh` (Developer-half). |
| Extend it | Add a `check_*` per new kernel-owned contract (drive the real script against `$SV`; assert with `assert_contains`/`assert_equals`/`assert_absent`), keeping each check scratch-isolated. |

## `verify-orchestration-briefing.sh` — PM-half integration check

Checks the PM-owned orchestration signals extracted from `verify-v4.sh` at V5-5 / LC-9. Will travel to the crickets PM-trigger plan when that ships; runs agentm-side until then.

| Property | Detail |
|---|---|
| Isolation | A `mktemp -d` scratch vault — never reads or writes a real vault. |
| No side effects | No network, no transcript mining, no sub-agent dispatch; self-cleans via a `trap`. |
| Coverage | Every briefing signal: inbox · HIGH watchlist · incubator · idea-ledger · staged-adapt · both nudges (idea-promotion + watchlist-authoring) · staged-adapt surfaces-and-clears · phase-dispatch plans + session-marker resolution (no-session / single-marker / ambiguous concurrency-safe). 10 checks. |
| Out of scope | Kernel contracts (config seed, idle chain, emit gating) — those remain in `verify-v4.sh`. |
| Extend it | Add a `check_*` per new PM-half signal, keeping each check scratch-isolated. |

## Reading red CI

```bash
gh run list --workflow "[T] Linux Tests"    --limit 3
gh run list --workflow "[T] Mac Tests"       --limit 3
gh run list --workflow "[T] Windows Tests"   --limit 3
gh run view <run-id> --log-failed             # drill into the failing step
```

Red-on-Windows but green-on-POSIX almost always indicates a path-separator or pwsh-host assumption regression. Red-on-all is usually a canonical-spec or adapter-parity drift — try `bash scripts/check-parity.sh` locally.

## Running the gate set locally

One command runs the deterministic battery — 22 gates: unit tests + every `check-*` gate + the four integration checks (`verify-v4`, `verify-orchestration-briefing`, `verify-phases`, `verify-memory-roundtrip`) — prints a PASS/FAIL table, and exits non-zero on any failure:

```bash
bash scripts/check-all.sh
```

It deliberately omits the heavier `smoke-install` + `gitleaks` (slow / external tooling) that CI runs on every push — run those directly if you need them: `bash scripts/smoke-install-bash.sh` (POSIX) / `pwsh -NoProfile -File scripts/smoke-install-pwsh.ps1` (Windows). `check-all.sh` is the maintained source of truth for the local battery — add a `gate` line as the project grows.

## Related

- [How to cut a release](Cut-A-Release) — CI must be green before invoking `ship-release`.
- [How to refresh an installed harness](Update-Installed-Harness) — what `--update` touches vs. leaves alone.
- [Vault write protocol](Vault-Write-Protocol) — the protocol the `check-vault-lock-parity` gate keeps byte-identical across its two copies.
- [ADR 0002](0002-documentation-convention) — the installer-boundary rule the gates enforce.
