<!-- mode: reference -->
# Known Issues

Gotchas worth knowing before you hit them. Each entry is a fixed, non-obvious issue — a reproduction condition, an environmental dependency, or a surprising interaction between features — not a running bug tracker. See [CI gates](CI-Gates) for the deterministic check suite and [Completed features](Completed-Features) for the full shipped-work log.

## ⚡ Quick Reference

| Symptom | Cause | Fixed in |
|---|---|---|
| A launchd/cron-triggered background job silently discovers zero jobs (exit 0, nothing runs, no error anywhere) | `agentm-runner.sh` `cd`s into `scripts/` for a sibling import; `runner.cli`'s `--jobs-dir`/`--harness-dir` default to CWD-relative paths that only resolve from the repo root | [#320](https://github.com/alexherrero/agentm/pull/320) (2026-07-17) |

## A launcher's `cd` can silently break its own CLI's path defaults

`scripts/agentm-runner.sh` (the launchd-invoked entry point for the [runner](../designs/agentm-runner)) has to `cd` into `scripts/` so `runner.cli`'s sibling import of `vault_lock.py` resolves. But `runner.cli`'s own `--jobs-dir`/`--harness-dir` flags default to CWD-relative paths (`.harness/jobs`, `.harness`) that only resolve correctly from the repo root. Under `cwd=scripts/` they silently resolved to a directory that never existed — and `manifest.load_manifests()` treats a missing jobs directory as "fresh install, no jobs configured" (returns `[]`), not an error. Every launchd-triggered cycle since the runner was first built (2026-07-05) ran clean and exited 0 having discovered zero jobs; nothing ever surfaced in a log or an alert.

A second, related bug rode the same blind spot: the launchd plist sets no `MEMORY_VAULT_PATH`, so a job command referencing `$MEMORY_VAULT_PATH` expanded to an empty string — `Path("").is_dir()` is `True` (resolves to cwd), so `inbox_digest.py`'s own directory check passed, and a job could write real output into the repo checkout instead of the vault, again with no error.

**Fix.** `agentm-runner.sh` now passes both paths explicitly, anchored at the repo root regardless of cwd, and resolves/exports `MEMORY_VAULT_PATH` via `harness_memory.vault_path()` when the launcher hasn't already set it. `inbox_digest.py` now rejects an empty/falsy vault path before ever touching `Path()`.

**The durable lesson.** A launcher script that `cd`s for a sibling-import reason silently invalidates any CWD-relative default in the program it then invokes — and if the invoked program treats a missing directory as "nothing configured" rather than an error, the failure has zero signal (exit 0, clean logs). Check this on any new launcher script, or if `agentm-runner.sh` grows another `cd`. See the [runner design](../designs/agentm-runner)'s amendment log for the full incident.

## See also

[Reference](Reference) · [CI gates](CI-Gates) · [Runner design](../designs/agentm-runner)
