#!/usr/bin/env python3
"""Run a Claude-style crickets hook under Antigravity (`agy`) and answer in agy's hook contract.

crickets hooks are written for Claude Code: they read a Claude payload on
stdin and answer with an exit code (2 = block) or plain stdout text. agy 1.4.x
has a real file-based hook surface (`hooks.json` at the plugin root), but its
contract is different: a different stdin shape, and an answer that must be one
JSON object on stdout ({"decision": ...} for tools, {"injectSteps": [...]} to
add context). Run unchanged, a Claude hook is silently ignored there.

This adapter sits between the two. `emit_antigravity.py` writes it into every
plugin that has hooks, as hooks/_agy_hook_adapter.py, and registers each hook as

    python3 ./hooks/_agy_hook_adapter.py <ClaudeEvent> <command> [args...]

so the original script keeps running as it is. The adapter

  1. reads agy's payload and rebuilds the Claude one the script expects
     (`cwd`, `tool_name`, `tool_input`, `prompt`, ...);
  2. runs the script from the workspace root, the way Claude Code does;
  3. turns its exit code and stdout into the JSON answer agy expects.

Claude event -> agy event: PreToolUse, PostToolUse, SessionStart and Stop map
straight across. UserPromptSubmit has no agy twin, so it runs on agy's
PreInvocation (which fires before every model call, not only before a new
user message); the adapter lets it through once per user message and answers
`{}` for the rest.

"No opinion" is spelled differently per event, and getting it wrong is not
harmless: agy reads a bare `{}` from a PreToolUse hook as a DENY (measured on
agy 1.4.11: the tool call is refused with an empty reason). So a PreToolUse
hook with nothing to say prints nothing at all; the other events take `{}`.

If the adapter itself fails it answers "no opinion" and exits 0, so a broken
hook never stalls the agent. That also means a PreToolUse guard fails open;
the unit tests, not this fallback, are what keep the guard honest.

Stdlib only; Python 3.8+.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

CHILD_TIMEOUT_SECONDS = 25

# agy tool names (the lowercased step type) -> the Claude tool name a script expects.
_CLAUDE_TOOL = {"run_command": "Bash", "view_file": "Read"}


def _workspace(payload: dict) -> str:
    paths = payload.get("workspacePaths")
    if isinstance(paths, list) and paths and isinstance(paths[0], str):
        return paths[0]
    return os.getcwd()


def claude_payload(event: str, p: dict, workspace: str) -> dict:
    """Rebuild the Claude Code stdin payload from agy's."""
    out = {
        "session_id": p.get("conversationId", ""),
        "transcript_path": p.get("transcriptPath", ""),
        "cwd": workspace,
        "hook_event_name": event,
    }
    if event in ("PreToolUse", "PostToolUse"):
        call = p.get("toolCall") or {}
        name = call.get("name", "")
        args = call.get("args") or {}
        if name == "run_command":
            args = {"command": args.get("CommandLine", "")}
        out["tool_name"] = _CLAUDE_TOOL.get(name, name)
        out["tool_input"] = args
    elif event == "UserPromptSubmit":
        out["prompt"] = p.get("lastUserInput", "")
    elif event == "Stop":
        out["stop_hook_active"] = (p.get("executionNum") or 1) > 1
    return out


def _state_file(conversation_id: str) -> Path:
    key = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:16]
    d = Path(tempfile.gettempdir()) / "crickets-agy-hooks"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.last-prompt"


def already_seen_prompt(p: dict) -> bool:
    """True when this user message has already been handed to the hook.

    agy's PreInvocation fires before every model call in a turn; Claude's
    UserPromptSubmit fires once per user message. Record a hash of the
    message per conversation and say "seen" for every call after the first.
    """
    text = p.get("lastUserInput") or ""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    f = _state_file(p.get("conversationId") or "no-conversation")
    try:
        if f.read_text(encoding="utf-8") == digest:
            return True
    except OSError:
        pass
    try:
        f.write_text(digest, encoding="utf-8")
    except OSError:
        pass
    return False


def run_child(cmd: list[str], stdin_json: dict, workspace: str, plugin_dir: str):
    """Run the original hook script the way Claude Code would."""
    resolved = [os.path.join(plugin_dir, a[2:]) if a.startswith("./hooks/") else a for a in cmd]
    env = dict(os.environ, CLAUDE_PROJECT_DIR=workspace, CLAUDE_PLUGIN_ROOT=plugin_dir)
    return subprocess.run(
        resolved, input=json.dumps(stdin_json), capture_output=True, text=True,
        cwd=workspace, env=env, timeout=CHILD_TIMEOUT_SECONDS,
    )


def _json_object(text: str):
    try:
        d = json.loads(text)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _context(stdout: str) -> str:
    """The text a Claude hook wants added to the model's context."""
    s = stdout.strip()
    if not s:
        return ""
    d = _json_object(s)
    if d is None:
        return s
    hso = d.get("hookSpecificOutput") or {}
    for v in (hso.get("additionalContext"), d.get("additionalContext")):
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _tool_decision(code: int, stdout: str, stderr: str) -> dict | None:
    if code == 2:
        return {"decision": "deny", "reason": stderr.strip() or "blocked by a crickets hook"}
    d = _json_object(stdout.strip())
    if d:
        hso = d.get("hookSpecificOutput") or {}
        legacy = {"approve": "allow", "block": "deny"}.get(d.get("decision"))
        verdict = hso.get("permissionDecision") or legacy
        if verdict in ("allow", "deny", "ask"):
            out = {"decision": verdict}
            reason = hso.get("permissionDecisionReason") or d.get("reason")
            if reason:
                out["reason"] = reason
            return out
    return None  # no opinion: print nothing (a bare {} would be read as a deny)


def no_opinion(event: str) -> dict | None:
    """What a hook answers when it has nothing to say (None = print nothing)."""
    return None if event == "PreToolUse" else {}


def translate(event: str, code: int, stdout: str, stderr: str) -> dict | None:
    """Turn a Claude hook's exit code and stdout into agy's JSON answer.

    None means "print nothing": only PreToolUse uses it, for no opinion.
    """
    if event == "PreToolUse":
        return _tool_decision(code, stdout, stderr)
    if event == "PostToolUse":
        return {}
    if event == "SessionStart":
        text = _context(stdout) if code == 0 else ""
        return {"injectSteps": [{"systemMessage": {"systemMessage": text}}]} if text else {}
    if event == "UserPromptSubmit":
        if code == 2:
            msg = stderr.strip() or "This message was blocked by a crickets hook."
            return {"injectSteps": [{"errorMessage": {"userMessage": msg}}],
                    "terminationBehavior": "terminate"}
        text = _context(stdout) if code == 0 else ""
        return {"injectSteps": [{"ephemeralMessage": text}]} if text else {}
    if event == "Stop":
        d = _json_object(stdout.strip()) or {}
        if code == 2 or d.get("decision") == "block":
            return {"decision": "continue", "reason": d.get("reason") or stderr.strip() or ""}
        return {}
    return {}


def _answer(result: dict | None) -> None:
    if result is not None:
        print(json.dumps(result))


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: _agy_hook_adapter.py <ClaudeEvent> <command> [args...]", file=sys.stderr)
        _answer(no_opinion(argv[1]) if len(argv) > 1 else {})
        return 0
    event, cmd = argv[1], argv[2:]
    plugin_dir = str(Path(__file__).resolve().parent.parent)
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        if event == "UserPromptSubmit" and already_seen_prompt(payload):
            _answer(no_opinion(event))
            return 0
        workspace = _workspace(payload)
        proc = run_child(cmd, claude_payload(event, payload, workspace), workspace, plugin_dir)
        _answer(translate(event, proc.returncode, proc.stdout, proc.stderr))
    except Exception:  # fail open: a broken hook must never stall the agent
        traceback.print_exc(file=sys.stderr)
        _answer(no_opinion(event))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
