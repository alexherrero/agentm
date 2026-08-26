---
name: verify-dispatch
description: "PostToolUse hook, registered once machine-wide, that runs the edited file's OWN project verification script. Reads the Write|Edit payload from stdin, resolves the project by walking up from the edited file's path (never the cwd), and runs that project's .harness/verify.sh with the path as $1. Silent no-op when the project has no verify script. Replaces the retired per-project --hooks registration."
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
---

# verify-dispatch — run the edited file's own project verification

Per-project verification survived the retirement of the per-project install, but it had to change shape to do so. This hook is that shape: **one registration for the machine, resolving the project at fire time.**

## What it does

On every `Write` and `Edit`, Claude Code hands the hook a JSON payload on stdin. The hook pulls the edited file's path out of it, walks up from that path until it finds a `.harness/verify.sh`, and runs it with the path as `$1`. If no ancestor has one, it exits 0 and prints nothing.

`templates/verify.sh` in this repo is the reference to copy from. Nothing installs it for you — whether a project has verification, and what it checks, is the operator's call per project.

## Why resolution starts at the file, not the cwd

The predecessor was registered per project and its command hardcoded `.harness/verify.sh` — a **cwd-relative** path. That worked only because the hook and the project were installed into the same directory, so cwd was always the project.

Machine-wide, that assumption breaks. The cwd is whatever directory the session was opened in, which is routinely not the project the edited file belongs to. An agent editing `~/work/api/src/x.ts` from a session opened in `~/work/tools` would have run tools' verify script against api's file — or, far more often, found no `.harness/verify.sh` in tools, done nothing, and said nothing about it.

The edited file's own path is the only anchor that is always right. The walk is bounded by `$HOME` so a stray `~/.harness/verify.sh` cannot capture every edit made anywhere on the machine, and by `/` so it always terminates.

## Three deliberate behaviours

**Silence is the default.** A project with no verify script is the normal case, not a problem. This hook fires on every Write and Edit in every project on the machine; anything it prints on the ordinary path is noise multiplied by every edit you will ever make.

**A non-executable `verify.sh` still runs.** When the file is executable it is exec'd directly, so its own shebang chooses the interpreter and you may write verification in something other than bash. When it is present but not executable, it runs under `bash` anyway rather than being skipped. A verify script that exists and silently never runs because of a missing `+x` bit is the most confusing outcome available here, and the shipped reference is bash.

**A failing verify script fails.** Its exit code and output pass straight through. The predecessor ended its command in `|| true`, which meant a project's typecheck could fail on every single edit and never once say so. Verification that cannot fail is not verification.

## Dependencies

`python3`, to parse one JSON object from stdin. Absent, the hook exits 0.

Deliberately **not** routed through `harness/hooks/lib/resolve-python.sh`. That resolver exists because the memory hooks import native extensions (`sqlite-vec`) that Apple's stock system Python cannot load; reading a JSON payload needs no such thing. This hook takes the plain interpreter and stays outside that resolver's parity contract.

The retired predecessor required `jq`, which is why `install.sh --hooks` listed `jq` as a prerequisite at all. It no longer does.
