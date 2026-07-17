#!/usr/bin/env python3
"""session_email.py — the opt-in daily digest email
(`wiki/designs/agentm-autonomy.md`'s "Delivery" subsection, third channel).

A once-daily email carrying the daily digest, for a read away from the
machine. Rides a first-party mail path the operator configures — their own
SMTP relay or an on-device mail agent — never a third-party push service.
Absent that configuration, the channel graceful-skips and the other two
channels (the SessionStart line, the on-device notification) carry
delivery on their own.

Contract, mirroring `session_notify.py`:
  - Never raises. An email channel must never block or crash the runner
    cycle it rides.
  - Graceful on every edge: unconfigured (either `email_to` or
    `email_smtp_url` absent), no vault, no digest ever run, SMTP send
    failure — every case is a silent no-op, never a stack trace in the
    runner log.
  - Calendar-day anti-fatigue, same shape as `session_notify.py`'s — at
    most one email per calendar day regardless of how many times the
    runner invokes this that day.
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import session_brief  # noqa: E402 — reuse the canonical digest reader, don't refork it

_EMAIL_TO_KEY = "plugins.autonomy.email_to"
_EMAIL_SMTP_URL_KEY = "plugins.autonomy.email_smtp_url"


def _agentm_install_prefix() -> Path:
    """Resolve install prefix: $AGENTM_INSTALL_PREFIX → ~/.claude. Mirrors
    harness_memory.py's `_agentm_install_prefix()` exactly."""
    raw = os.environ.get("AGENTM_INSTALL_PREFIX", "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    return Path.home() / ".claude"


def email_config(install_prefix: "Path | None" = None) -> "tuple[str, str] | None":
    """Read (`email_to`, `email_smtp_url`) from `.agentm-config.json`.
    Returns None unless BOTH are present and non-empty — either one absent
    means the channel is unconfigured and graceful-skips. Never raises."""
    if install_prefix is None:
        install_prefix = _agentm_install_prefix()
    config_path = install_prefix / ".agentm-config.json"
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    to_addr = data.get(_EMAIL_TO_KEY)
    smtp_url = data.get(_EMAIL_SMTP_URL_KEY)
    if not isinstance(to_addr, str) or not to_addr.strip():
        return None
    if not isinstance(smtp_url, str) or not smtp_url.strip():
        return None
    return to_addr.strip(), smtp_url.strip()


def default_state_path() -> Path:
    return Path.home() / ".cache" / "agentm" / "telemetry" / "email-state.json"


def _today_str(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.strftime("%Y-%m-%d")


def _already_sent_today(state_path: Path, today: str) -> bool:
    try:
        d = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get("last_sent_date") == today


def _record_sent(state_path: Path, today: str) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"last_sent_date": today}, sort_keys=True) + "\n"
        tmp = state_path.with_name(f"{state_path.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, state_path)
    except OSError:
        pass


def email_body(vault: Path) -> "tuple[str, str] | None":
    """Build (subject, body) from the same digest reader session_brief uses
    (`latest_digest()` — the newest delivered note, regardless of staleness;
    staleness/deadman handling is the SessionStart line's job, not email's).
    Returns None when there is honestly nothing to say (ladder never ran on
    this vault)."""
    digest = session_brief.latest_digest(vault)
    if digest is None:
        return None
    subject = f"AgentM daily digest — {digest['headline']}"
    body_lines = [digest["headline"], ""]
    try:
        body_lines.append(digest["path"].read_text(encoding="utf-8"))
    except OSError:
        pass
    return subject, "\n".join(body_lines)


def _send_smtp(smtp_url: str, to_addr: str, subject: str, body: str) -> bool:
    """Send via the configured first-party SMTP relay/mail agent. Parses
    `smtp://[user@]host[:port]`. Returns True iff the send completed
    without raising. Never a third-party push service — the operator's own
    URL is the only destination this ever talks to."""
    try:
        parsed = urlparse(smtp_url)
        host = parsed.hostname
        if not host:
            return False
        port = parsed.port or 25
        from_addr = parsed.username or to_addr
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError, ValueError):
        return False


def run(
    *, install_prefix: "Path | None" = None, vault: "Path | None" = None,
    now: "datetime | None" = None, state_path: "Path | None" = None,
) -> bool:
    """The entry point. Returns True iff an email was actually sent.
    Swallows every error → False (never blocks the runner cycle)."""
    try:
        cfg = email_config(install_prefix)
        if cfg is None:
            return False
        to_addr, smtp_url = cfg
        if vault is None:
            vault = session_brief.resolve_vault()
        if vault is None:
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        today = _today_str(now)
        state_path = Path(state_path) if state_path is not None else default_state_path()
        if _already_sent_today(state_path, today):
            return False
        built = email_body(vault)
        if built is None:
            return False
        subject, body = built
        sent = _send_smtp(smtp_url, to_addr, subject, body)
        if sent:
            _record_sent(state_path, today)
        return sent
    except Exception:
        return False


def main(argv: "list[str] | None" = None) -> int:
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
