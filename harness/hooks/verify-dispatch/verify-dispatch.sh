#!/usr/bin/env bash
# verify-dispatch.sh — run the edited file's OWN project verification script.
#
# A PostToolUse hook, registered once for the machine. Claude Code hands it the
# Write|Edit payload on stdin; it pulls the edited file's path out, walks up
# from THAT path to find the project the file belongs to, and runs that
# project's `.harness/verify.sh` with the path as $1.
#
# WHY IT RESOLVES FROM THE FILE AND NOT THE CWD. The predecessor was registered
# per project and hardcoded `.harness/verify.sh` — a cwd-relative path. That
# worked only because the hook and the project were installed together. Once
# the registration is machine-wide, cwd is whatever directory the session
# happens to be in, which is frequently not the project the edited file lives
# in: an agent editing `~/work/api/src/x.ts` from a session opened in
# `~/work/tools` would have run tools' verify script against api's file, or
# more often run nothing at all and said nothing about it. The edited file's
# own path is the only anchor that is always right.
#
# SILENCE IS THE DEFAULT. A project with no `.harness/verify.sh` is the normal
# case, not an error: exit 0, print nothing. This hook fires on every Write and
# Edit in every project on the machine, so anything it says on the ordinary
# path is noise multiplied by every edit you will ever make.
#
# WHAT IT DOES NOT DO. It does not create, scaffold, or suggest a verify.sh.
# `templates/verify.sh` in this repo is the reference to copy from; choosing to
# have one is the operator's call, per project.

set -uo pipefail

# No python3 → no way to read the payload. Not an error; nothing to verify.
command -v python3 >/dev/null 2>&1 || exit 0

payload="$(cat 2>/dev/null || true)"
[[ -n "$payload" ]] || exit 0

# Both keys are read because the two tools disagree: Write reports the path
# under tool_input.file_path, and some responses carry it as
# tool_response.filePath. The predecessor read both for the same reason.
#
# Stock python3 is fine here, deliberately. The memory hooks route through
# harness/hooks/lib/resolve-python.sh because they import native extensions
# (sqlite-vec) that Apple's system Python cannot load. Parsing one JSON object
# from stdin needs no such thing, so this hook takes the plain interpreter and
# stays out of that resolver's parity contract.
file_path="$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)
ti = d.get("tool_input") or {}
tr = d.get("tool_response") or {}
p = ""
if isinstance(ti, dict):
    p = ti.get("file_path") or ""
if not p and isinstance(tr, dict):
    p = tr.get("filePath") or ""
if isinstance(p, str) and p:
    print(p)
' 2>/dev/null || true)"

[[ -n "$file_path" ]] || exit 0
[[ -e "$file_path" ]] || exit 0

dir="$(cd "$(dirname "$file_path")" 2>/dev/null && pwd)" || exit 0

# Walk up looking for the project that owns this file. Bounded by $HOME so a
# stray ~/.harness/verify.sh cannot capture every edit made anywhere on the
# machine — and bounded by / so the loop always terminates.
home_real="$(cd "${HOME:-/}" 2>/dev/null && pwd || echo "")"
while [[ -n "$dir" && "$dir" != "/" ]]; do
    [[ -n "$home_real" && "$dir" == "$home_real" ]] && break

    candidate="$dir/.harness/verify.sh"
    if [[ -f "$candidate" ]]; then
        # Executable: exec it directly so its own shebang decides the
        # interpreter — an operator is free to write verify.sh in something
        # other than bash.
        #
        # Not executable: run it under bash anyway rather than skipping. A
        # verify script that exists but silently never runs because of a
        # missing +x bit is the single most confusing outcome available here,
        # and the shipped reference template is bash, so this is the right
        # default for the file that is actually there.
        if [[ -x "$candidate" ]]; then
            exec "$candidate" "$file_path"
        fi
        exec bash "$candidate" "$file_path"
    fi

    parent="$(dirname "$dir")"
    [[ "$parent" == "$dir" ]] && break
    dir="$parent"
done

exit 0
