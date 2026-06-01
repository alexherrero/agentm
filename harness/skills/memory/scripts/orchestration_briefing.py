#!/usr/bin/env python3
"""orchestration_briefing.py — the SessionStart pending-state briefing (V4 #23 task 3,
sub-item b).

Scans the vault for "things worth your attention" and emits a tight (1-3 line)
briefing block, but ONLY when something has shifted since it was last shown AND
the cooldown allows — the anti-fatigue guard from auto_orchestration.py. The
`memory-recall-session-start` hook appends this block to the SessionStart context
after the always-load recall.

Signals (each defensive — absent/empty/malformed source → 0, never raises):
  - inbox          : count of <vault>/_inbox/*.md (operator-curatable staging)
  - watchlist_high : _skill-watchlist entries with evaluator_classification HIGH
                     + status pending-review (awaiting `/memory watchlist`)
  - incubator      : count of _idea-incubator/<slug>/ dirs (ideas in research)
  - idea_ledger    : "## YYYY-MM-DD:" entries in the Ideas surface older than the
                     configured stale-months (GC-eligible)

Each signal surfaces only at/above its configured threshold. If nothing is over
threshold, the briefing is empty (and nothing is recorded/printed).

The whole thing is non-blocking by contract: any unexpected error is swallowed and
yields an empty briefing, so a SessionStart hook can `|| true` around it safely.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

# sibling import (same scripts dir; Python puts the script dir on sys.path[0],
# and tests insert it explicitly)
import auto_orchestration as ao

_CHAIN = "briefing"
_DATE_HDR_RE = re.compile(r"^##\s+(\d{4})-(\d{2})-(\d{2})\b")
_SKIP_INBOX = {"_index.md", "readme.md", "_readme.md"}


# ── signal counters (each returns int; never raises) ────────────────────────
def count_inbox(vault: Path) -> int:
    d = Path(vault) / "_inbox"
    if not d.is_dir():
        return 0
    try:
        return sum(
            1 for p in d.glob("*.md")
            if p.is_file() and p.name.lower() not in _SKIP_INBOX
        )
    except OSError:
        return 0


def _read_frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line.strip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def count_watchlist_high_pending(vault: Path) -> int:
    root = Path(vault) / "personal-private" / "_skill-watchlist"
    if not root.is_dir():
        return 0
    n = 0
    try:
        for source_dir in root.iterdir():
            if not source_dir.is_dir() or source_dir.name == "_archive":
                continue
            for entry in source_dir.glob("*.md"):
                if not entry.is_file():
                    continue
                fm = _read_frontmatter(entry)
                if (
                    fm.get("evaluator_classification", "").strip().upper() == "HIGH"
                    and fm.get("status", "").strip().lower() == "pending-review"
                ):
                    n += 1
    except OSError:
        return n
    return n


def count_incubator_pending(vault: Path) -> int:
    root = Path(vault) / "personal-private" / "_idea-incubator"
    if not root.is_dir():
        return 0
    try:
        return sum(
            1 for d in root.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        )
    except OSError:
        return 0


def _ideas_surface_path() -> Path:
    env = os.environ.get("IDEAS_SURFACE_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / "Obsidian" / "Ideas.md"


def count_idea_ledger_stale(now: datetime, stale_months: int) -> int:
    """Count `## YYYY-MM-DD:` ledger entries older than stale_months."""
    # Normalize a naive `now` → UTC so the offset-aware ledger dates below never
    # raise a TypeError on subtraction (counters must never raise — the contract).
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    p = _ideas_surface_path()
    if not p.is_file():
        return 0
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    cutoff_days = max(0, int(stale_months)) * 30  # ~month; coarse is fine here
    n = 0
    for line in text.splitlines():
        m = _DATE_HDR_RE.match(line)
        if not m:
            continue
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            continue
        if (now - dt).days >= cutoff_days:
            n += 1
    return n


# ── gather + render ─────────────────────────────────────────────────────────
def gather_signals(vault: Path, config: dict, now: datetime) -> dict:
    return {
        "inbox": count_inbox(vault),
        "watchlist_high": count_watchlist_high_pending(vault),
        "incubator": count_incubator_pending(vault),
        "idea_ledger": count_idea_ledger_stale(now, int(config.get("idea_ledger_stale_months", 6))),
    }


def _over_threshold(signals: dict, config: dict) -> dict:
    """Return only the signals that meet/exceed their configured threshold."""
    out = {}
    if signals["inbox"] >= int(config.get("inbox_threshold", 10)):
        out["inbox"] = signals["inbox"]
    if signals["watchlist_high"] >= int(config.get("watchlist_high_threshold", 1)):
        out["watchlist_high"] = signals["watchlist_high"]
    if signals["incubator"] >= int(config.get("incubator_pending_threshold", 1)):
        out["incubator"] = signals["incubator"]
    if signals["idea_ledger"] >= 1:  # any stale ledger entry is worth a nudge
        out["idea_ledger"] = signals["idea_ledger"]
    return out


def build_briefing(signals: dict, config: dict) -> str:
    """Render the briefing block, or "" if nothing is over threshold."""
    active = _over_threshold(signals, config)
    if not active:
        return ""
    parts = []
    if "inbox" in active:
        parts.append(f"{active['inbox']} inbox entr{'y' if active['inbox'] == 1 else 'ies'} to sort")
    if "watchlist_high" in active:
        n = active["watchlist_high"]
        parts.append(f"{n} HIGH skill-watchlist {'pattern' if n == 1 else 'patterns'} to review (`/memory watchlist`)")
    if "incubator" in active:
        n = active["incubator"]
        parts.append(f"{n} incubator idea{'' if n == 1 else 's'} in research")
    if "idea_ledger" in active:
        n = active["idea_ledger"]
        parts.append(f"{n} idea-ledger entr{'y' if n == 1 else 'ies'} >{config.get('idea_ledger_stale_months', 6)}mo (GC-eligible)")
    lines = ["# MemoryVault — pending"]
    for p in parts:
        lines.append(f"- {p}")
    return "\n".join(lines) + "\n"


def emit_briefing(vault: Path, now: datetime | None = None) -> str:
    """The hook entry point. Returns the briefing block to inject, or "".

    Honors: the enable_briefing toggle, the briefing cooldown, and the
    shifted-since-last-shown guard. On a real emission, records the fire +
    the shown snapshot so the next boot stays quiet until something changes.
    Swallows any unexpected error → "" (never blocks SessionStart)."""
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        config = ao.load_config(vault)
        if not config.get("enable_briefing", True):
            return ""
        state = ao.load_state(vault)
        # Gather BEFORE the cooldown gate: detecting "everything cleared" is
        # cooldown-independent bookkeeping, not an emission. The shifted-guard
        # compares the *over-threshold* snapshot, so a count wobbling below
        # threshold doesn't count as a change worth re-surfacing.
        signals = gather_signals(vault, config, now)
        active = _over_threshold(signals, config)
        if not active:
            # Nothing pending right now. Record the cleared snapshot so a later
            # equal-count pile reads as a genuine shift instead of being
            # suppressed by a stale last_shown. This writes the *shown* snapshot
            # only (no fire → cooldown is not consumed), and only when there's a
            # stale snapshot to clear (avoids churning the state file every boot).
            if state.get("last_shown"):
                ao.record_shown(state, {})
                ao.save_state(vault, state)
            return ""
        cooldown = float(config.get("briefing_cooldown_hours", 0) or 0)
        if not ao.should_fire(state, _CHAIN, now, cooldown):
            return ""
        if not ao.state_shifted_since_last_shown(state, active):
            return ""
        block = build_briefing(signals, config)
        if not block:
            return ""
        ao.record_fire(state, _CHAIN, now)
        ao.record_shown(state, active)
        ao.save_state(vault, state)
        return block
    except Exception:
        return ""


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="orchestration_briefing.py")
    parser.add_argument("--vault-path", default=None)
    args = parser.parse_args(argv[1:])
    try:
        vault = ao._resolve_vault_path(args.vault_path)
    except ValueError:
        return 0  # no vault → silent, non-blocking
    block = emit_briefing(vault)
    if block:
        # leading blank line so it separates cleanly from the recall block above
        print("\n" + block, end="")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv))
