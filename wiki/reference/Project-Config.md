# Project config reference

You configure each project in `.harness/project.json`. This file holds `vault_project`, `github`, and `env`. It also adds an *enablement block*. This block includes `type`, `skills`, `hooks`, `registered_at`, `registered_via`, `operator_overrides`, and `last_redetect_at`. It records your repo's detected and approved configuration. You keep the verification ledger in a separate file. You leave `features.json` and its `passes` untouched.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| Where does enablement config live? | `.harness/project.json` — NOT `features.json` (locked DC-1). |
| Who writes it? | [`scripts/project_config.py`](https://github.com/alexherrero/agentm/blob/main/scripts/project_config.py) — `register()` calls `merge_enablement()`, which preserves every other key (`vault_project` / `github` / `env`) and overwrites only the enablement keys. |
| Where does the file resolve to? | Vault-resident post-V4 #26: `<vault>/projects/<slug>/_harness/project.json`. The writer routes through `harness_memory.write_state_file` (`.project-mode`-aware — routes to the repo-local home `<repo>/.harness/` for local-mode projects, which `write_state_file` now writes with no vault configured; distinct from the migrate-nagging legacy fallback). |
| What counts as "registered"? | `is_registered()` = `project.json` carries a non-empty `skills` block **OR** the repo has a `repo_registry` entry. |
| What's the default `type`? | `"coding"` (the type taxonomy is deferred to V5). |
| Related pages | [Detection rules](Detection-Rules), [Configure a new project](Configure-A-New-Project), [Repo layout](Repo-Layout) |

## Enablement block schema

The `build_enablement_block` function emits 7 keys. The `merge_enablement` function overwrites these keys. You preserve everything else in `project.json` verbatim during the merge.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `type` | string | `"coding"` | Project type. The taxonomy beyond `"coding"` is deferred to V5. |
| `skills` | object | the 6 enableable skills, all enabled | Map `<name> → TargetState`. Names: `memory`, `design`, `diataxis-author`, `pii-scrubber`, `ship-release`, `dependabot-fixer`. |
| `hooks` | object | the 7 enableable hooks, all enabled | Map `<name> → TargetState`. Names: `kill-switch`, `steer`, `commit-on-stop`, `memory-recall-session-start`, `memory-recall-prompt-submit`, `memory-reflect-idle`, `memory-reflect-stop`. |
| `registered_at` | string (ISO-8601 UTC) | now, at write time | When the block was first written, e.g. `2026-05-29T14:03:00Z`. |
| `registered_via` | string | `"auto-detect"` | How the repo was registered — `"auto-detect"` (the flow) or `"manual"`. |
| `operator_overrides` | array | `[]` | `[{at, skill_or_hook, action, reason}]` — one entry per opt-out recorded by `apply_override` (action defaults to `"disabled-at-registration"`). |
| `last_redetect_at` | string \| null | `null` | Reserved; stays `null` (the `/setup --redetect` flow is deferred). |

### `TargetState` shape (each entry in `skills` / `hooks`)

| Field | Type | Meaning |
|---|---|---|
| `enabled` | bool | Whether the skill/hook is active for this project (`false` after an opt-out). |
| `auto_detected` | bool | `true` if a rule matched this target; `false` if it carries the default rationale only. |
| `rationale` | string | Why this target is relevant — a detection reason (when matched) or a default reason. |
| `rule_id` | string \| null | The matching rule's id (e.g. `R-wiki`), or `null` when default-enabled. |
| `operator_action` | string \| null | `null` until an opt-out flips it to `"disabled-at-registration"`. |

## Pre-existing keys (unchanged)

_You add the enablement block. The merge-writer preserves these existing keys._

| Key | Meaning |
|---|---|
| `vault_project` | MemoryVault slug for this repo. |
| `github` | GitHub Projects linkage (`owner`/`number`/`url`/`repo`). See [GitHub Projects integration](GitHub-Projects-Integration). |
| `env` | Per-project environment settings. Pre-existing; untouched by the enablement merge. |

## Related

- [Detection rules](Detection-Rules) — You populate `rationale` and `rule_id` in the `skills` and `hooks` maps here.
- [Configure a new project](Configure-A-New-Project) — You write this block using this flow.
- [Repo layout](Repo-Layout) — You store `.harness/project.json` on disk here.
- [Auto-detect + auto-configure](Auto-Detect-Configure) — You see why config lives here and not in `features.json` (DC-1).
