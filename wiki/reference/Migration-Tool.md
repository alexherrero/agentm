# Migration tool reference

Command-line reference for `migrate-to-user-scope.sh` (POSIX) and `migrate-to-user-scope.ps1` (Windows / PowerShell 7+). Shipped in agentm v4.5.0 as the closing piece of ROADMAP-V4 item #30 (Global install — default to user scope).

## ⚡ Quick Reference

| Task | Command |
|---|---|
| Preview migration (default; read-only) | `bash scripts/migrate-to-user-scope.sh <target>` |
| Apply migration | `bash scripts/migrate-to-user-scope.sh --apply <target>` |
| Apply + force operator-edited files | `bash scripts/migrate-to-user-scope.sh --apply --force <target>` |
| Reverse a migration | `bash scripts/migrate-to-user-scope.sh --rollback <target>` |
| Remove empty install subdirs (post-verify) | `bash scripts/migrate-to-user-scope.sh --cleanup <target>` |
| Print help | `bash scripts/migrate-to-user-scope.sh --help` |

## Synopsis

```
migrate-to-user-scope.sh [OPTIONS] [TARGET]
migrate-to-user-scope.ps1 [-Apply | -Rollback | -Cleanup] [OTHER OPTIONS] [-Target TARGET]
```

`TARGET` defaults to `$PWD` when omitted. The migration tool is **always idempotent + reversible**: rerunning an apply is a no-op; rollback undoes a prior apply step-by-step.

## Flags

| Flag (bash) | Flag (pwsh) | Effect |
|---|---|---|
| (none — default) | (none — default) | **Preview mode.** Read-only classification + table output. No filesystem mutation. |
| `--apply` | `-Apply` | Execute the migration. Classifies, prompts for confirmation, removes `safe_to_migrate` entries, writes `.agentm-migrate-record.json`, then invokes `bash install.sh --scope user` (idempotent) and `repo_registry register` (unless `--no-register`). |
| `--rollback` | `-Rollback` | Reverse a prior migration via `.agentm-migrate-record.json`. Restores `safe_to_migrate` entries from source clones; restores `force_migrated` entries from `.agentm-migrate-backup/`; removes the record + backup directory on full restore. Idempotent — re-running rollback after rollback is a no-op (record is gone). |
| `--cleanup` | `-Cleanup` | Opt-in destructive removal of empty `.claude/{skills,hooks,agents,commands}/` subdirs after byte-identical verification. **Shape-agnostic**: any non-symlink non-dotfile content remaining under those subdirs refuses cleanup (operator content with arbitrary extensions is sacred — `.py`, `.txt`, files with no extension all protected). |
| `--force` | `-Force` | When used with `--apply`: migrate `operator_edited` files anyway, backing them up to `.agentm-migrate-backup/<rel_path>` so rollback can restore. Without `--force`, operator-edited files are skipped (the migration tool's default protection against accidental data loss). |
| `--no-register` | `-NoRegister` | Skip the auto-registry step at end of `--apply`. By default the migration tool auto-calls `python3 scripts/repo_registry.py register <slug> --root <target>` so the repo shows up in [`/list-plans`](Use-Auto-Context-In-Harness-Phases) + other cross-repo views. Pass `--no-register` for explicit opt-out (e.g. for ephemeral test targets). |
| `--registry-slug NAME` | `-RegistrySlug NAME` | Override the slug used for auto-registration. Default: inferred from `<target>/.harness/project.json`'s `vault_project` or `slug` field, with fallback to `basename <target>`. |
| `--agentm PATH` | `-AgentmPath PATH` | Override the agentm source-clone path. Default: `~/Antigravity/agentm`. Useful for non-default dev setups. |
| `--crickets PATH` | `-CricketsPath PATH` | Override the crickets source-clone path. Default: `~/Antigravity/crickets`. |
| `--yes`, `-y` | `-Yes` | Skip interactive confirmation prompts. Use for CI / scripted invocations. |
| `--ci-override` | `-CiOverride` | Allow the migration tool to run when `$CI=true` env detected. Default behavior refuses to run inside CI (CI runners use per-project installs by design per [Use-Per-Project-Install](Use-Per-Project-Install)). |
| `--help`, `-h` | `Get-Help <script>` | Print the header help block. |

`--apply`, `--rollback`, and `--cleanup` are **mutually exclusive**. Passing more than one is a usage error.

## State matrix

The migration tool detects 4 starting states for any given target. The current state is printed in the banner output of every invocation.

| State | Detection | Default behavior |
|---|---|---|
| **`no-claude`** | No `<target>/.claude/{skills,hooks,agents,commands}/` content. | Graceful no-op exit 0; suggests `bash install.sh --scope user <target>` for fresh installs. Bypassed when `--rollback` or `--cleanup` is set. |
| **`pre-v4.3`** | `.claude/` content present + no `.claude/.agentm-install-state.json`. | Primary migrate path — classify + apply works directly. |
| **`explicit-project`** | `.agentm-install-state.json` present with `mode=project`. | Requires explicit confirmation prompt before applying (the `mode=project` setting may be intentional per [Use-Per-Project-Install](Use-Per-Project-Install)). Skipped under `--yes`. |
| **`already-user`** | `.agentm-install-state.json` present with `mode=user`. | Graceful no-op exit 0; "already user-scope; nothing to migrate." Bypassed when `--rollback` or `--cleanup` is set. |

## Classification matrix

For each entry under `<target>/.claude/{skills,hooks,agents,commands}/`, the tool emits exactly one of:

| Classification | Detection | `--apply` action | `--apply --force` action |
|---|---|---|---|
| **`safe_to_migrate`** | Byte-identical (SHA256) to source-clone canonical path. | Remove from target; record action. | Same. |
| **`already_symlinked`** | Target is a symlink (either to source-clone in source-mode install, or to user-scope post-prior-migration). | No-op (already migrated). | Same. |
| **`operator_edited`** | Exists in source-clone mapping but SHA differs (operator made local edits). | **Skip with warn** — protected by default. | Migrate anyway; back up to `.agentm-migrate-backup/<rel_path>`. |
| **`unrecognized`** | No source-clone mapping entry (operator's own customization or stale artifact). | No-op (operator content sacred). | Same. |

Dir bundles (skill bundles, hook bundles) are hashed via SHA256 of sorted `(rel_path, file_sha256)` pairs. Dotfile-noise (`.DS_Store`, `.git/`, editor `.swp`) is filtered from the hash input — macOS Finder won't trick the tool into false `operator_edited` classifications.

## `.agentm-migrate-record.json` schema (v1)

Lives at `<target>/.agentm-migrate-record.json` (NOT under `.claude/` — survives the `--cleanup` step). Drives rollback semantics.

```json
{
  "version": 1,
  "target_root": "/path/to/project",
  "migrated_at": "2026-05-27T18:00:00Z",
  "source_clones_used": {
    "agentm":  "~/Antigravity/agentm",
    "crickets": "~/Antigravity/crickets"
  },
  "registered_slug": "myproject",
  "actions": [
    {
      "kind": "safe_to_migrate",
      "rel_path": "agents/memory-idea-researcher.md",
      "source_clone": "agentm",
      "source_path": "/path/to/agentm/harness/agents/memory-idea-researcher.md",
      "sha256": "..."
    },
    {
      "kind": "force_migrated",
      "rel_path": "agents/customized.md",
      "backup_path": ".agentm-migrate-backup/agents/customized.md",
      "target_sha_before": "...",
      "source_sha": "..."
    },
    {
      "kind": "operator_edited_skipped",
      "rel_path": "agents/edited.md",
      "target_sha": "...",
      "source_sha": "...",
      "backup_collision": false
    }
  ]
}
```

Field reference:

| Field | Type | Meaning |
|---|---|---|
| `version` | int | Record schema version. v1 since v4.5.0. Forward-compat additions (new fields) won't break existing rollback. |
| `target_root` | string | Resolved absolute path of the target at apply time. |
| `migrated_at` | string | UTC ISO8601 timestamp. Preserved across re-apply runs (first-migration timestamp wins on merge). |
| `source_clones_used` | object | Snapshot of which clones were used for SHA compare. Useful for forensics if rollback fails. |
| `registered_slug` | string \| null | Slug recorded for downstream registry integration. `null` if `--no-register` was passed. |
| `actions` | array | Per-entry action records. Merged by `(rel_path, kind)` tuple on re-apply (so distinct-kind reattempts for the same rel_path are both recorded). |

Action kinds:

| Kind | Reversible by | Notes |
|---|---|---|
| `safe_to_migrate` | Copy from `source_path` back to `.claude/<rel_path>`. | Rollback refuses if `.claude/<rel_path>` already exists (operator may have re-staged something). |
| `force_migrated` | Move `<backup_path>` back to `.claude/<rel_path>`. | Same dest-clobber refusal as above. |
| `operator_edited_skipped` | No-op (file was never moved). | Includes optional `backup_collision: true` flag set when a second `--apply --force` run hit a pre-existing backup file. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (or graceful no-op state). |
| 1 | Argument / environment error (target not a dir; source clones missing; required helper not found). |
| 2 | Usage error (mutually exclusive flags; unknown option). |
| 3 | Python helper failed (install_migrate.py returned non-zero). |
| 4 | Refused due to CI guard ($CI=true detected without `--ci-override`). |
| 5 | `--cleanup` refused because operator content remains under `.claude/{...}/`. |

## Related

- [Use-Per-Project-Install](Use-Per-Project-Install) — when to deliberately keep `--scope project` and not run the migration tool
- [Install-Into-Project](Install-Into-Project) — the default install workflow
- [Installer-CLI](Installer-CLI) — `install.sh` flag reference
- ADR 0012 § 6 — dev-setup invisibility policy (load-bearing assumption preserved)
- `lib/install/python/install_migrate.py` — the primitive that powers all four modes
