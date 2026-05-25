# `lib/install/` — shared install plumbing contract

This directory holds install primitives shared **byte-identically** between `agentm` and `crickets`. Both repos commit the same files here; CI in each repo verifies its local copies against `lib/install/.checksums.txt`. Cross-repo sync is performed by `scripts/sync-lib.sh`.

## Why a shared lib

Both `agentm` and `crickets` are installers — they read content from their own source tree and copy it into a target project under host-specific paths (`.claude/`, `.agent/`, `.gemini/`). The mechanics of "copy a managed file with update-mode overwrite semantics" or "wipe-and-recreate a managed directory on `--update`" are identical between the two. Duplicating that logic invites drift; sharing via copy-the-lib avoids it without coupling the two repos' release cadence.

## Contents

```
lib/install/
├── bash/primitives.sh           # POSIX (bash 4+) install primitives
├── pwsh/primitives.ps1          # PowerShell 7+ install primitives
├── CONTRACT.md                  # this file
└── .checksums.txt               # SHA-256 of every file above; verified by check-lib-parity
```

## Caller contract

Before sourcing `primitives.sh` (bash) or dot-sourcing `primitives.ps1` (pwsh), the caller must set:

| bash variable | pwsh variable | Type | Meaning |
|---|---|---|---|
| `UPDATE_MODE` | `$Update` | int 0/1, switch | Managed-copy functions overwrite when set; skip-if-exists when not |
| `BOUNDARY_ROOTS` | `$BoundaryRoots` | array of absolute paths | `ensure_boundary_src` / `Ensure-BoundarySrc` accept a source only if it lives under one of these |

The caller is also responsible for argument parsing (the lib doesn't touch argv), error display, the install flow itself, and any repo-specific decisions (which dirs to wipe on `--update`, which subdirs to walk, etc.). The lib provides primitives — atomic operations — not the install flow.

## Functions exposed

### bash (`primitives.sh`)

- `ensure_boundary_src <src>` — exits 1 if `<src>` is not under any `BOUNDARY_ROOTS` entry
- `cp_user <src> <dst>` — file-level copy, skip-if-exists
- `cp_managed <src> <dst>` — file-level copy, overwrite-on-update
- `cp_user_walk <src_root> <dst_root>` — recursive cp_user per file
- `cp_managed_dir <src> <dst>` — dir-level copy, wipe-and-recreate on update
- `sync_managed_parents <managed_dirs...> -- <empty_parent_candidates...>` — `--update` wipe block

### pwsh (`primitives.ps1`)

- `Ensure-BoundarySrc <src>` — same as bash
- `Copy-UserFile <src> <dst>` — same as bash `cp_user`
- `Copy-ManagedFile <src> <dst>` — same as bash `cp_managed`
- `Copy-UserWalk <srcRoot> <dstRoot>` — same as bash `cp_user_walk`
- `Copy-ManagedDir <src> <dst>` — same as bash `cp_managed_dir`
- `Copy-AdapterFiles <srcDir> <glob> <dstDir>` — convenience iterator (pwsh-only; bash uses inline loops)
- `Copy-AdapterDirs <srcDir> <dstDir>` — convenience iterator (pwsh-only)
- `Sync-ManagedParents @($managedDirs) @($emptyParentCandidates)` — `-Update` wipe block

## Behavior invariants (must not change without major-version coordination)

These are the contracts both repos depend on. Changes here are breaking for any consumer.

1. **`ensure_boundary_src` exits 1 (not returns false)** when the source is outside the allowed roots. The caller MUST set `BOUNDARY_ROOTS` before invoking any other function; otherwise every call fails.
2. **`cp_user` never overwrites.** If the destination exists, the function prints "kept" and returns 0. Used for files the user is expected to edit (PLAN.md, init.sh, verify.sh, etc.).
3. **`cp_managed` overwrites only in update mode.** Otherwise behaves like `cp_user`. Used for repo-authored files the user should not edit (skill files, command files, hook scripts).
4. **`cp_managed_dir` wipes the entire destination on update** before recopying. Anything the user added under that dir is destroyed. Use only for fully-managed directories. NEVER use for user-content dirs like `wiki/`.
5. **`cp_user_walk` is the only safe way to drop scaffold into a user-content tree.** It traverses the source per-file and applies `cp_user` (skip-if-exists). Existing user files survive.
6. **`sync_managed_parents` prints `"    wiped N managed dir(s); rebuilding from source"` last.** Existing CI smoke tests grep for this line; changing the wording breaks tests in both repos.

## How to update the lib

Edits to anything under `lib/install/` are **lockstep changes across both repos**:

1. Edit the canonical copy in `agentm/lib/install/`.
2. Run `bash scripts/sync-lib.sh` — copies into `../crickets/lib/install/` + regenerates `.checksums.txt` in both repos.
3. Verify: `bash scripts/check-lib-parity.sh` in both repos returns 0.
4. Commit in both repos with parallel messages (recommended: cross-reference the other repo's commit SHA).
5. Push both. CI in each verifies self-consistency.

If the edit changes a behavior invariant (the list above), bump major version in both repos and document in both `CHANGELOG.md` files.

## Why not a submodule

Submodules add clone-time friction (`--recurse-submodules` flag, lockfile syncing across repos), pwsh on Windows handles them inconsistently, and the lib is small enough that copy-the-lib with a checksum gate is both simpler and offline-friendlier. The cost is the lockstep update discipline; the script `sync-lib.sh` makes that a one-command operation.
