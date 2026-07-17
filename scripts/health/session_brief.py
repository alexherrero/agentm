#!/usr/bin/env python3
"""session_brief.py — the VISIBLE session-start observability line
(`wiki/designs/agentm-autonomy.md`'s "Delivery → Session-start line", the
workhorse channel).

The autonomy design's delivery layer rests on one line at every session open:
the day's digest headline, how long ago the last cycle ran, a needs-your-eye
count, and — the point — a **deadman** variant that says so when the digest
ladder has gone quiet ("no digest in N days — ladder stalled"). The 2026-07-17
diagnosis found the line had never actually appeared: the only session-start
briefing (`harness/skills/memory/scripts/orchestration_briefing.py`) is
tail-appended to the memory-recall hook's multi-KB always-load dump, which the
host collapses into a single unread `<persisted-output>` blob — so a briefing
line there is emitted into context but never *seen*. #320 added the deadman
*logic* (`orchestration_briefing.py`'s `digest_stale_days` signal) but on that
same invisible surface.

This module is the visible half: a tight one-liner emitted by the ALREADY-VISIBLE
`harness-context-session-start` hook (the "[agentm] Project state" line the
operator does see), reading the digest ladder's own delivered artifacts —
`<vault>/_briefs/*-digest-*.md` (the notes `inbox_digest.py` writes) and the
park-state files (`window_park.py`) — never a new telemetry source.

Contract, mirroring every other hook-invoked script here:
  - Never raises. Any unexpected error yields an empty line (the hook `|| true`s
    around it anyway; belt and suspenders).
  - Honest-quiet: a vault that never ran the digest ladder (no digest note AND
    no digest-history rows) emits nothing — a deadman is for a ladder that WAS
    running, not a nag for an install that never opted in.
  - Anti-fatigue: a short cooldown + a semantic signature so a `/clear` or
    resume in the same working window doesn't repeat the line, while a genuine
    change (a new digest, the stall day-count ticking, a run parking) shows
    immediately. State lives in a small device-local file, never the vault.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The digest-note slug shape `inbox_digest.digest_slug()` writes:
# `YYYYMMDD-digest-<cadence>` (park notes share `_briefs/` but not `-digest-`).
_DIGEST_SLUG_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})-digest-([a-z0-9]+)$")
# Finest cadence first — "the day's headline" prefers the daily digest.
_CADENCE_PRIORITY = {"daily": 0, "3day": 1, "weekly": 2, "monthly": 3}

_SPEND_RE = re.compile(r"^- Spend:\s*\$([0-9][0-9.]*)", re.MULTILINE)
_EVENTS_RE = re.compile(r"^- Events:\s*([0-9]+)", re.MULTILINE)
_MONTHLY_TOTAL_RE = re.compile(r"\*\*Total spend, last 30 days:\s*\$([0-9][0-9.]*)")
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)

_DEFAULT_DEADMAN_DAYS = 2  # matches #320's digest_ladder_stale_days_threshold
_DEFAULT_COOLDOWN_HOURS = 4.0


# ── resolution (device-local telemetry + vault) ──────────────────────────────
def _cache_root() -> Path:
    return Path.home() / ".cache" / "agentm" / "telemetry"


def default_park_dir() -> Path:
    return _cache_root() / "park"


def default_history_path() -> Path:
    return _cache_root() / "digest-history.jsonl"


def default_state_path() -> Path:
    return _cache_root() / "session-brief-state.json"


def resolve_vault(arg_path: "str | None" = None) -> "Path | None":
    """arg → $MEMORY_VAULT_PATH → ~/.claude/.agentm-config.json → None.

    Mirrors `memory-recall-session-start.sh`'s own resolver: the host does not
    inject MEMORY_VAULT_PATH into the hook environment, so fall back to the
    config's dual key (the V5-8 `plugins.obsidian-vault.vault_path`, then the
    legacy flat `vault_path`)."""
    if arg_path:
        p = Path(arg_path).expanduser()
        return p if p.is_dir() else None
    env = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p
    prefix = os.environ.get("AGENTM_INSTALL_PREFIX", str(Path.home() / ".claude"))
    cfg = Path(prefix) / ".agentm-config.json"
    if cfg.is_file():
        try:
            d = json.loads(cfg.read_text(encoding="utf-8", errors="replace"))
            v = (d.get("plugins.obsidian-vault.vault_path") or d.get("vault_path") or "").strip()
            if v:
                p = Path(v).expanduser()
                return p if p.is_dir() else None
        except (OSError, ValueError, AttributeError):
            return None
    return None


# ── digest / park / history readers (each defensive → benign default) ────────
def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def latest_digest(vault: Path) -> "dict | None":
    """The newest delivered digest note under `<vault>/_briefs/`, or None.

    Newest by slug date; ties (same date, multiple cadences) resolve to the
    finest cadence — "the day's headline" is the daily digest when one landed.
    Returns {date, cadence, slug, path, mtime, spend, events, headline}."""
    d = Path(vault) / "_briefs"
    if not d.is_dir():
        return None
    best = None  # (date, -cadence_priority_inverted) tuple for max()
    best_key = None
    try:
        for p in d.glob("*-digest-*.md"):
            if not p.is_file():
                continue
            m = _DIGEST_SLUG_RE.match(p.stem)
            if not m:
                continue
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            except ValueError:
                continue
            cadence = m.group(4)
            # Sort key: later date wins; on a tie, finer cadence (lower priority
            # number) wins → negate so max() picks it.
            key = (dt, -_CADENCE_PRIORITY.get(cadence, 99))
            if best_key is None or key > best_key:
                best_key = key
                best = (p, dt, cadence)
    except OSError:
        pass
    if best is None:
        return None
    path, dt, cadence = best
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    body = _safe_read(path)
    spend = None
    events = None
    if cadence == "monthly":
        mt = _MONTHLY_TOTAL_RE.search(body)
        if mt:
            spend = _to_float(mt.group(1))
    else:
        sm = _SPEND_RE.search(body)
        if sm:
            spend = _to_float(sm.group(1))
        em = _EVENTS_RE.search(body)
        if em:
            events = _to_int(em.group(1))
    headline = _build_headline(cadence, spend, events, body)
    return {
        "date": dt, "cadence": cadence, "slug": path.stem, "path": path,
        "mtime": mtime, "spend": spend, "events": events, "headline": headline,
    }


def _to_float(s: str) -> "float | None":
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _to_int(s: str) -> "int | None":
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _build_headline(cadence: str, spend: "float | None", events: "int | None", body: str) -> str:
    if cadence == "monthly" and spend is not None:
        return f"monthly digest: ${spend:,.2f} over 30 days"
    if spend is not None:
        tail = f", {events} events" if events is not None else ""
        return f"{cadence} digest: ${spend:,.2f}{tail}"
    # Fall back to the note's own H1 if the body shape changed.
    m = _H1_RE.search(body)
    if m:
        return m.group(1).strip()
    return f"{cadence} digest"


def count_parked(park_dir: Path) -> int:
    """Live parked runs awaiting the operator's one-paste resume — the
    observability-native 'needs your eye' signal (`window_park.py`)."""
    if not Path(park_dir).is_dir():
        return 0
    try:
        return sum(1 for p in Path(park_dir).glob("*-park-state.json") if p.is_file())
    except OSError:
        return 0


def history_latest_date(history_path: Path) -> "datetime | None":
    """The newest `date` recorded in the digest-history ledger — evidence the
    ladder *computed* a digest even when no note reached `_briefs/`. The gap
    between this and `latest_digest()` is the note-delivery stall #320 fixed
    at the runner; surfacing it here composes the two diagnoses in one line."""
    p = Path(history_path)
    if not p.is_file():
        return None
    best = None
    for line in _safe_read(p).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        raw = (row.get("date") or "").strip() if isinstance(row, dict) else ""
        if not raw:
            continue
        try:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if best is None or dt > best:
            best = dt
    return best


# ── age formatting ───────────────────────────────────────────────────────────
def _age_phrase(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _days_between(later: datetime, earlier: datetime) -> int:
    return max(0, (later.date() - earlier.date()).days)


# ── the line + its anti-fatigue signature ───────────────────────────────────
def build_brief(
    *, vault: Path, now: datetime, deadman_days: int = _DEFAULT_DEADMAN_DAYS,
    park_dir: "Path | None" = None, history_path: "Path | None" = None,
) -> "dict | None":
    """Compose the observability line, or None when there is honestly nothing
    to say (the ladder was never configured on this vault). Returns
    {"line": str, "signature": str}; the signature omits fine-grained age so the
    anti-fatigue guard suppresses only genuinely-unchanged repeats."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    park_dir = Path(park_dir) if park_dir is not None else default_park_dir()
    history_path = Path(history_path) if history_path is not None else default_history_path()

    digest = latest_digest(vault)
    hist_latest = history_latest_date(history_path)
    if digest is None and hist_latest is None:
        return None  # honest-quiet: ladder never ran here

    parked = count_parked(park_dir)
    parked_clause = ""
    if parked > 0:
        parked_clause = f" · {parked} run{'s' if parked != 1 else ''} parked, awaiting resume"

    hist_str = hist_latest.strftime("%Y-%m-%d") if hist_latest is not None else None

    if digest is not None:
        stale_days = _days_between(now, digest["date"])
        if stale_days < deadman_days:
            # Fresh — the workhorse headline + how long ago the cycle ran.
            if digest["mtime"] is not None:
                age = _age_phrase(now.timestamp() - digest["mtime"])
            else:
                age = f"{stale_days}d ago" if stale_days else "today"
            line = f"[agentm] Observability — {digest['headline']} (last cycle {age}){parked_clause}."
            signature = f"fresh|{digest['slug']}|{parked}"
            return {"line": line, "signature": signature}
        # Deadman — a note exists but the ladder has gone quiet.
        extra = ""
        if hist_str and hist_latest is not None and hist_latest > digest["date"]:
            extra = f" Digests computed through {hist_str} but not delivered — see runner."
        last = digest["date"].strftime("%Y-%m-%d")
        line = (
            f"[agentm] ⚠ Observability — no digest in {stale_days} days, "
            f"ladder stalled (last: {last}).{extra}{parked_clause}"
        )
        signature = f"deadman|{last}|{stale_days}|{hist_str or '-'}|{parked}"
        return {"line": line, "signature": signature}

    # No digest note at all, but the ledger shows the ladder ran before → the
    # note-delivery path is broken (exactly the current stall's shape).
    line = (
        f"[agentm] ⚠ Observability — no digest note delivered to _briefs/ "
        f"(ladder last computed {hist_str}, but no note reached the vault — see runner)"
        f"{parked_clause}."
    )
    signature = f"deadman-nonote|{hist_str}|{parked}"
    return {"line": line, "signature": signature}


# ── anti-fatigue state (device-local, never the vault) ───────────────────────
def load_state(state_path: Path) -> dict:
    p = Path(state_path)
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8", errors="replace") or "{}")
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def should_show(state: dict, signature: str, now: datetime, cooldown_hours: float) -> bool:
    """Show unless the identical line was shown within the cooldown window.
    A changed signature (new digest, ticked stall count, a run parked) always
    shows; only an unchanged repeat inside the window is suppressed."""
    if state.get("signature") != signature:
        return True
    if cooldown_hours <= 0:
        return True
    last = state.get("shown_ts")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last))
    except (ValueError, TypeError):
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - last_dt).total_seconds() / 3600.0 >= cooldown_hours


def record_shown(state_path: Path, signature: str, now: datetime) -> None:
    p = Path(state_path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"signature": signature, "shown_ts": now.isoformat()}, sort_keys=True) + "\n"
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def emit(
    *, vault: "Path | None", now: "datetime | None" = None,
    deadman_days: int = _DEFAULT_DEADMAN_DAYS, cooldown_hours: float = _DEFAULT_COOLDOWN_HOURS,
    park_dir: "Path | None" = None, history_path: "Path | None" = None,
    state_path: "Path | None" = None,
) -> str:
    """The hook entry point. Returns the line to print (no trailing newline), or
    "" when there is nothing to say or the cooldown suppresses a repeat.
    Records a shown line so the next boot stays quiet until something changes.
    Swallows any unexpected error → "" (never blocks session boot)."""
    if now is None:
        now = datetime.now(timezone.utc)
    if vault is None:
        return ""
    state_path = Path(state_path) if state_path is not None else default_state_path()
    try:
        brief = build_brief(
            vault=vault, now=now, deadman_days=deadman_days,
            park_dir=park_dir, history_path=history_path,
        )
        if brief is None:
            return ""
        state = load_state(state_path)
        if not should_show(state, brief["signature"], now, cooldown_hours):
            return ""
        record_shown(state_path, brief["signature"], now)
        return brief["line"]
    except Exception:
        return ""


def _main(argv: "list[str]") -> int:
    parser = argparse.ArgumentParser(prog="session_brief.py", add_help=True)
    parser.add_argument("--vault-path", default=None)
    parser.add_argument("--deadman-days", type=int, default=None)
    parser.add_argument("--cooldown-hours", type=float, default=None)
    parser.add_argument("--park-dir", default=None)
    parser.add_argument("--history-path", default=None)
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--no-cooldown", action="store_true",
                        help="ignore the anti-fatigue guard (for a one-shot check)")
    args = parser.parse_args(argv[1:])

    vault = resolve_vault(args.vault_path)
    deadman = args.deadman_days if args.deadman_days is not None else int(
        os.environ.get("AGENTM_DIGEST_DEADMAN_DAYS", _DEFAULT_DEADMAN_DAYS)
    )
    cooldown = 0.0 if args.no_cooldown else (
        args.cooldown_hours if args.cooldown_hours is not None else float(
            os.environ.get("AGENTM_SESSION_BRIEF_COOLDOWN_HOURS", _DEFAULT_COOLDOWN_HOURS)
        )
    )
    line = emit(
        vault=vault, deadman_days=deadman, cooldown_hours=cooldown,
        park_dir=Path(args.park_dir) if args.park_dir else None,
        history_path=Path(args.history_path) if args.history_path else None,
        state_path=Path(args.state_path) if args.state_path else None,
    )
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
