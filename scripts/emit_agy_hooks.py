#!/usr/bin/env python3
"""emit_agy_hooks.py — build agentm's Antigravity (`agy`) hook plugin from the installed hooks.

Claude Code runs agentm's recall hooks because `install.sh` merges each hook's
`settings-fragment-bash.json` into `<prefix>/settings.json`. `agy` 1.4 has a
file-based hook surface too — a `hooks.json` at a plugin's root — but a different
contract (its own stdin shape, an answer that is one JSON object on stdout). This
script is the second half of that install: it reads the same installed fragments
and writes a small `agy` plugin whose handlers run the *same* hook scripts through
the vendored crickets adapter (`adapters/antigravity/hooks/_agy_hook_adapter.py`),
which rebuilds Claude's payload from agy's and turns the script's output into
agy's answer. Nothing about a hook is written twice.

A hook is registered on agy when its `hook.md` frontmatter lists `antigravity`
under `supported_hosts`. Only the two events agy has an equivalent for and takes as
a flat handler list are generated: `SessionStart`, and `UserPromptSubmit`, which
runs on agy's `PreInvocation` (it fires before every model call, so the adapter
lets the hook through once per user message). A declared hook with any other event
is an error, not a silent skip: a registration that quietly lacks a handler is
how a guard goes missing.

Every handler is
    [ "${AGENTM_AGY_RECALL:-}" = off ] && echo '{}' || \
        RECALL_TOKEN_BUDGET="${RECALL_TOKEN_BUDGET:-10000}" AGENTM_HOST=antigravity \
        python3 ./hooks/_agy_hook_adapter.py <Event> bash <installed hook script>

`RECALL_TOKEN_BUDGET` is the recall engine's own cap on what one injection carries
(its default is 20,000 estimated tokens, about 80 KB). agy cuts a single injected
message at 49,309 characters whatever it holds, keeping the head and replacing the
rest with `<truncated N bytes>` (measured on agy 1.4.12 with English text and with
hex text: the stored message was the same length both times, so it is a character
cap, not a token cap). Left at 20,000 the engine would hand agy a message it then
cuts mid-entry; at 10,000 (about 40 KB, which leaves room for the header lines) the
engine drops whole entries by its own priority order and says which, in a note the
model reads. Today's real payload is about 25 KB, so nothing is dropped yet. A
`RECALL_TOKEN_BUDGET` already in the environment wins.

`AGENTM_HOST=antigravity` is what tells the hook it is not on Claude Code, so it
leaves out the parts that are Claude's alone (see memory-recall-session-start).
`AGENTM_AGY_RECALL=off` in the environment of an `agy` run turns the recall off for
that run — for a script that shells out to `agy -p` and does not want the operator's
memory in it — and `agy plugin disable agentm` turns it off for good; a re-run of
the installer keeps a disabled plugin disabled.

The handlers point at the hook scripts under `<prefix>/hooks/`, the copies Claude
Code runs, not at copies inside the plugin: one script, one set of siblings
(`../lib/resolve-python.sh`, the context hook project-brief falls back to), and a
source-mode symlink install keeps following the clone.

Usage:
    emit_agy_hooks.py --prefix <install prefix> --out <plugin dir> [--version V]

Exit: 0 wrote the plugin; 3 no installed hook declares antigravity (nothing to
register); 1 a declared hook cannot be registered; 2 argument or setup error.
Stdlib only; Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_SRC = REPO_ROOT / "adapters" / "antigravity" / "hooks" / "_agy_hook_adapter.py"
ADAPTER_REL = "hooks/_agy_hook_adapter.py"

HOST = "antigravity"
PLUGIN_NAME = "agentm"

# Estimated tokens (the engine counts characters / 4) one agy injection may carry:
# about 40 KB, under the 49,309-character cut agy makes. See the module docstring.
AGY_TOKEN_BUDGET = 10_000

# Claude event -> agy event. Both take a flat list of handlers in hooks.json.
EVENT_MAP = {"SessionStart": "SessionStart", "UserPromptSubmit": "PreInvocation"}

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---", re.S)
_HOSTS_RE = re.compile(r"^supported_hosts:\s*\[(.*?)\]\s*$", re.M)


class EmitError(Exception):
    """A declared hook cannot be registered."""


def supported_hosts(hook_md: Path) -> list[str]:
    try:
        text = hook_md.read_text(encoding="utf-8")
    except OSError:
        return []
    fm = _FRONTMATTER_RE.match(text)
    if not fm:
        return []
    m = _HOSTS_RE.search(fm.group(1))
    if not m:
        return []
    return [h.strip().strip("'\"") for h in m.group(1).split(",") if h.strip()]


def handler_command(event: str, script: Path) -> str:
    """The shell line agy runs for one handler (agy runs it with `sh -c`)."""
    return (
        '[ "${AGENTM_AGY_RECALL:-}" = off ] && echo \'{}\' || '
        f'RECALL_TOKEN_BUDGET="${{RECALL_TOKEN_BUDGET:-{AGY_TOKEN_BUDGET}}}" AGENTM_HOST={HOST} '
        f"python3 ./{ADAPTER_REL} {event} bash {shlex.quote(str(script))}"
    )


def build_hooks(prefix: Path) -> dict:
    """hooks.json content: one named hook per installed hook that declares agy."""
    hooks_dir = prefix / "hooks"
    out: dict[str, dict] = {}
    if not hooks_dir.is_dir():
        return out
    for hook_dir in sorted(p for p in hooks_dir.iterdir() if p.is_dir()):
        name = hook_dir.name
        if HOST not in supported_hosts(hook_dir / "hook.md"):
            continue
        script = hook_dir / f"{name}.sh"
        frag_path = hook_dir / "settings-fragment-bash.json"
        if not script.is_file():
            raise EmitError(f"{name}: declares {HOST} but {script} is missing")
        if not frag_path.is_file():
            raise EmitError(f"{name}: declares {HOST} but has no settings-fragment-bash.json")
        try:
            fragment = json.loads(frag_path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise EmitError(f"{name}: settings-fragment-bash.json is not valid JSON ({e})")
        events: dict[str, list] = {}
        for event, entries in (fragment.get("hooks") or {}).items():
            if event not in EVENT_MAP:
                raise EmitError(
                    f"{name}: declares {HOST} but registers on {event}, which this generator "
                    f"has no agy event for (it handles {', '.join(sorted(EVENT_MAP))})")
            handlers = []
            for entry in entries:
                for h in entry.get("hooks", []):
                    if "command" not in h:
                        continue
                    handler = {"type": "command", "command": handler_command(event, script)}
                    if "timeout" in h:
                        handler["timeout"] = h["timeout"]
                    handlers.append(handler)
            if handlers:
                events.setdefault(EVENT_MAP[event], []).extend(handlers)
        if not events:
            raise EmitError(f"{name}: declares {HOST} but its fragment registers no command")
        out[name] = {"enabled": True, **events}
    return out


def emit(prefix: Path, out_dir: Path, version: str) -> int:
    hooks = build_hooks(prefix)
    if not hooks:
        return 3
    if not ADAPTER_SRC.is_file():
        raise EmitError(f"the vendored adapter is missing: {ADAPTER_SRC}")
    # Rebuilt from nothing, so a hook that stopped declaring antigravity does not linger.
    shutil.rmtree(out_dir, ignore_errors=True)
    (out_dir / "hooks").mkdir(parents=True)
    manifest = {
        "name": PLUGIN_NAME,
        "version": version,
        "description": ("agentm's memory recall for Antigravity: the session-start brief and the "
                        "per-message recall, run from the same hooks Claude Code runs."),
        "author": {"name": "alexherrero", "url": "https://github.com/alexherrero/agentm"},
    }
    (out_dir / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out_dir / "hooks.json").write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(ADAPTER_SRC, out_dir / ADAPTER_REL)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--prefix", required=True, help="the agentm install prefix (holds hooks/<name>/)")
    ap.add_argument("--out", required=True, help="directory to write the plugin into (replaced)")
    ap.add_argument("--version", default="0.0.0", help="plugin version (the harness version)")
    args = ap.parse_args(argv)
    # Absolute: agy runs a handler from the plugin's directory, so a relative
    # script path would resolve against the wrong place and the hook would do nothing.
    prefix = Path(os.path.abspath(os.path.expanduser(args.prefix)))
    if not prefix.is_dir():
        print(f"emit_agy_hooks: prefix {prefix} is not a directory", file=sys.stderr)
        return 2
    try:
        rc = emit(prefix, Path(args.out), args.version)
    except EmitError as e:
        print(f"emit_agy_hooks: {e}", file=sys.stderr)
        return 1
    if rc == 3:
        print("emit_agy_hooks: no installed hook declares antigravity in supported_hosts", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
