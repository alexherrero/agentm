#!/usr/bin/env python3
"""session_notify.py — the opt-in daily on-device notification
(`wiki/designs/agentm-autonomy.md`'s "Delivery" subsection, second channel).

Unlike `session_brief.py` (fires every session boot, cooldown-gated in
hours), this channel is runner-scheduled — once per calendar day — and
fires a native OS notification (macOS `osascript`) carrying the same
headline + needs-your-eye clause `session_brief.build_brief()` already
computes. Absent-by-default: silent, zero-cost no-op unless the operator
explicitly opts in via `agentm_config.py --notify-enabled true`
(`plugins.autonomy.notify_enabled`).

Contract, mirroring `session_brief.py`:
  - Never raises. Any unexpected error is swallowed — a notification
    channel must never block or crash the runner cycle it rides.
  - Graceful on every edge: unconfigured (opt-in absent/false), no vault,
    no digest ever run, `osascript` unavailable (non-macOS) — every case is
    a silent no-op, never a stack trace in the runner log.
  - Calendar-day anti-fatigue, not cooldown-hours: this fires at most once
    per calendar day regardless of how many times the runner invokes it
    that day, mirroring `inbox_digest.py`'s idempotent-per-day contract
    rather than `session_brief.py`'s hours-based cooldown (session-start
    events don't have "once a day" semantics; a runner-scheduled job does).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import session_brief  # noqa: E402 — reuse the canonical digest reader, don't refork it

_NOTIFY_ENABLED_KEY = "plugins.autonomy.notify_enabled"


def _agentm_install_prefix() -> Path:
    """Resolve install prefix: $AGENTM_INSTALL_PREFIX → ~/.claude. Mirrors
    harness_memory.py's `_agentm_install_prefix()` exactly."""
    raw = os.environ.get("AGENTM_INSTALL_PREFIX", "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    return Path.home() / ".claude"


def notify_enabled(install_prefix: "Path | None" = None) -> bool:
    """Read `plugins.autonomy.notify_enabled` from `.agentm-config.json`.
    Graceful-skip to False on any I/O/parse error or absent field — never raises."""
    if install_prefix is None:
        install_prefix = _agentm_install_prefix()
    config_path = install_prefix / ".agentm-config.json"
    if not config_path.is_file():
        return False
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    return data.get(_NOTIFY_ENABLED_KEY) is True


def default_state_path() -> Path:
    return Path.home() / ".cache" / "agentm" / "telemetry" / "notify-state.json"


def _today_str(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.strftime("%Y-%m-%d")


def _already_fired_today(state_path: Path, today: str) -> bool:
    try:
        d = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get("last_fired_date") == today


def _record_fired(state_path: Path, today: str) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"last_fired_date": today}, sort_keys=True) + "\n"
        tmp = state_path.with_name(f"{state_path.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, state_path)
    except OSError:
        pass


def _fire_osascript(body: str, *, title: str = "AgentM") -> bool:
    """Fire a native macOS notification. Returns True iff osascript ran
    successfully. Absent osascript (non-macOS) → False, no exception."""
    binary = shutil.which("osascript")
    if not binary:
        return False
    script = f'display notification {_applescript_quote(body)} with title {_applescript_quote(title)}'
    try:
        result = subprocess.run([binary, "-e", script], capture_output=True, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _applescript_quote(s: str) -> str:
    """Quote a string for embedding in an AppleScript literal — escape
    backslashes and double quotes, matching AppleScript's own escaping."""
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def notify_body(
    vault: Path, *, now: "datetime | None" = None,
    park_dir: "Path | None" = None, history_path: "Path | None" = None,
) -> "str | None":
    """Build the notification body from the same digest reader session_brief
    uses. Returns None when there is honestly nothing to say (ladder never
    ran on this vault)."""
    if now is None:
        now = datetime.now(timezone.utc)
    brief = session_brief.build_brief(vault=vault, now=now, park_dir=park_dir, history_path=history_path)
    if brief is None:
        return None
    # Strip the "[agentm] " prefix session_brief's line carries — a native OS
    # notification already has its own titled chrome.
    line = brief["line"]
    return line[len("[agentm] "):] if line.startswith("[agentm] ") else line


def run(
    *, install_prefix: "Path | None" = None, vault: "Path | None" = None,
    now: "datetime | None" = None, state_path: "Path | None" = None,
    park_dir: "Path | None" = None, history_path: "Path | None" = None,
) -> bool:
    """The entry point. Returns True iff a notification was actually fired.
    Swallows every error → False (never blocks the runner cycle)."""
    try:
        if not notify_enabled(install_prefix):
            return False
        if vault is None:
            vault = session_brief.resolve_vault()
        if vault is None:
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        today = _today_str(now)
        state_path = Path(state_path) if state_path is not None else default_state_path()
        if _already_fired_today(state_path, today):
            return False
        body = notify_body(vault, now=now, park_dir=park_dir, history_path=history_path)
        if not body:
            return False
        fired = _fire_osascript(body)
        if fired:
            _record_fired(state_path, today)
        return fired
    except Exception:
        return False


def main(argv: "list[str] | None" = None) -> int:
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
