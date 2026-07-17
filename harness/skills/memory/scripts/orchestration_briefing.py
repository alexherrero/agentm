#!/usr/bin/env python3
"""orchestration_briefing.py — the SessionStart pending-state briefing (V4 #23 task 3,
sub-item b).

Scans the vault for "things worth your attention" and emits a tight (1-3 line)
briefing block, but ONLY when something has shifted since it was last shown AND
the cooldown allows — the anti-fatigue guard from auto_orchestration.py. The
`memory-recall-session-start` hook appends this block to the SessionStart context
after the always-load recall.

Signals (each defensive — absent/empty/malformed source → 0, never raises):
  - inbox          : count of <vault>/personal/_inbox/*.md (operator-curatable
                     staging)
  - watchlist_high : _skill-watchlist entries with evaluator_classification HIGH
                     + status pending-review (awaiting `/memory watchlist`)
  - incubator      : count of _idea-incubator/<slug>/ dirs (ideas in research)
  - idea_ledger    : "## YYYY-MM-DD:" entries in the Ideas surface older than the
                     configured stale-months (GC-eligible)
  - digest_stale_days : days since the newest <vault>/_briefs/*-digest-*.md note
                     (a deadman check on the observability digest ladder, 0 if
                     the ladder was never configured on this vault at all)

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
    d = Path(vault) / "personal" / "_inbox"
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
    root = Path(vault) / "personal" / "_skill-watchlist"
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


_DIGEST_SLUG_RE = re.compile(r"^(\d{8})-digest-")


def latest_digest_date(vault: Path) -> "datetime | None":
    """The most recent date any `<vault>/_briefs/*-digest-*.md` note landed,
    parsed from the filename slug (`inbox_digest.digest_slug()`'s
    `YYYYMMDD-digest-<cadence>.md` shape) -- avoids reading every candidate
    file's frontmatter just to find the newest one. `None` if `_briefs/`
    doesn't exist or holds no digest note (as opposed to a park-job note,
    which shares the same directory but not the `-digest-` slug segment)."""
    d = Path(vault) / "_briefs"
    if not d.is_dir():
        return None
    best = None
    try:
        for p in d.glob("*-digest-*.md"):
            if not p.is_file():
                continue
            m = _DIGEST_SLUG_RE.match(p.name)
            if not m:
                continue
            try:
                dt = datetime(int(m.group(1)[0:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8]), tzinfo=timezone.utc)
            except ValueError:
                continue
            if best is None or dt > best:
                best = dt
    except OSError:
        return best
    return best


def count_digest_stale_days(vault: Path, now: datetime) -> int:
    """Days since the most recent observability digest note landed --
    the SessionStart half of the honesty surface for a silently stalled
    digest ladder (2026-07-17 finding: the runner that drives
    inbox_digest.py can go dark for days with no error, no crash, and a
    "status": "done" marker indistinguishable from success). A vault that
    has never had the digest ladder configured at all returns 0 rather than
    a large number -- this is a deadman check on a ladder that WAS running,
    not a nag for an install that never opted in."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    latest = latest_digest_date(vault)
    if latest is None:
        return 0
    return max(0, (now - latest).days)


def count_incubator_pending(vault: Path) -> int:
    root = Path(vault) / "_idea-incubator"
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


# ── nudge signals (V4 #23 task 6: sub-items f + g) ──────────────────────────
_IDEA_TITLE_RE = re.compile(r"^##\s+\d{4}-\d{2}-\d{2}:\s*(.+?)\s*$")


def _parse_iso_ts(s: str) -> datetime | None:
    """Tolerant ISO-8601 parse → aware UTC datetime, or None. Handles a trailing
    `Z`, full timestamps, and date-only strings; never raises."""
    s = (s or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def count_promote_suggest(threshold: int) -> int:
    """(f) Count distinct ideas surfaced ≥ `threshold` times in the Ideas ledger.

    The reflection sidecar appends one `## YYYY-MM-DD: <Title>` section per
    surfacing (append-only, no dedup), so an idea mined from N session
    transcripts appears N times. A title at/above the mention threshold is a
    "you keep having this idea — promote it?" signal. Never raises → 0."""
    p = _ideas_surface_path()
    if not p.is_file():
        return 0
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    counts: dict[str, int] = {}
    for line in text.splitlines():
        m = _IDEA_TITLE_RE.match(line)
        if not m:
            continue
        title = m.group(1).strip().lower()
        if not title:
            continue
        counts[title] = counts.get(title, 0) + 1
    thr = max(1, int(threshold))
    return sum(1 for c in counts.values() if c >= thr)


def count_stale_promoted(vault: Path, stale_days: int, now: datetime) -> int:
    """(g) Count `_skill-watchlist/` entries marked `status: promoted` whose
    `promoted_at` is older than `stale_days` — the operator said "I'll author
    this skill" N days ago and hasn't. The safety-rail nudge. Never raises → 0."""
    root = Path(vault) / "personal" / "_skill-watchlist"
    if not root.is_dir():
        return 0
    cutoff = max(0, int(stale_days))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    n = 0
    try:
        for source_dir in root.iterdir():
            if not source_dir.is_dir() or source_dir.name == "_archive":
                continue
            for entry in source_dir.glob("*.md"):
                if not entry.is_file():
                    continue
                fm = _read_frontmatter(entry)
                if fm.get("status", "").strip().lower() != "promoted":
                    continue
                pa = _parse_iso_ts(fm.get("promoted_at", ""))
                if pa is None:
                    continue
                if (now - pa).days >= cutoff:
                    n += 1
    except OSError:
        return n
    return n


def count_staged_adapt(vault: Path) -> int:
    """Count staged Pass-1 adapt candidates awaiting a Pass-2 verdict — JSONs at
    `_meta/skill-discovery-cache/adapt-state/<source>/<pattern>.json` that have
    NO corresponding `_skill-watchlist/<source>/<pattern>.md` entry yet. This
    closes the discover→adapt→evaluate loop: the idle chain stages Pass-1
    candidates, this surfaces them so the operator runs `/memory adapt-skills`
    (which dispatches the adapt-evaluator, Pass-2). Excluding already-watchlisted
    candidates means the count clears as the operator evaluates. Never raises → 0.
    (The sibling root-level `evaluated.json` is a file, not a source dir, so the
    `is_dir()` guard skips it.)"""
    root = Path(vault) / "_meta" / "skill-discovery-cache" / "adapt-state"
    if not root.is_dir():
        return 0
    wl_root = Path(vault) / "personal" / "_skill-watchlist"
    n = 0
    try:
        for source_dir in root.iterdir():
            if not source_dir.is_dir():
                continue
            for j in source_dir.glob("*.json"):
                if not j.is_file():
                    continue
                if not (wl_root / source_dir.name / f"{j.stem}.md").exists():
                    n += 1
    except OSError:
        return n
    return n


# ── gather + render ─────────────────────────────────────────────────────────
def gather_signals(vault: Path, config: dict, now: datetime) -> dict:
    sig = {
        "inbox": count_inbox(vault),
        "watchlist_high": count_watchlist_high_pending(vault),
        "incubator": count_incubator_pending(vault),
        "idea_ledger": count_idea_ledger_stale(now, int(config.get("idea_ledger_stale_months", 6))),
        "staged_adapt": count_staged_adapt(vault),
        "digest_stale_days": count_digest_stale_days(vault, now),
    }
    # Nudges (task 6) — gated by their own toggles so a disabled nudge computes
    # 0 (no wasted parse) and stays out of the shifted-state snapshot.
    sig["promote_suggest"] = (
        count_promote_suggest(int(config.get("promote_mention_threshold", 3)))
        if config.get("enable_promote_suggest", True) else 0
    )
    sig["stale_promoted"] = (
        count_stale_promoted(vault, int(config.get("stale_promotion_days", 30)), now)
        if config.get("enable_stale_promotion_nudge", True) else 0
    )
    return sig


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
    if signals.get("staged_adapt", 0) >= 1:  # candidates awaiting Pass-2 eval
        out["staged_adapt"] = signals["staged_adapt"]
    if signals.get("digest_stale_days", 0) >= int(config.get("digest_ladder_stale_days_threshold", 2)):
        out["digest_stale_days"] = signals["digest_stale_days"]
    # Nudges (task 6) — the mention/stale thresholds are applied inside the
    # counters, and the toggle inside gather_signals, so any non-zero count here
    # is already a qualifying, enabled signal.
    if signals.get("promote_suggest", 0) >= 1:
        out["promote_suggest"] = signals["promote_suggest"]
    if signals.get("stale_promoted", 0) >= 1:
        out["stale_promoted"] = signals["stale_promoted"]
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
    if "staged_adapt" in active:
        n = active["staged_adapt"]
        parts.append(f"{n} skill candidate{'' if n == 1 else 's'} staged for adapt-evaluation (`/memory adapt-skills`)")
    if "incubator" in active:
        n = active["incubator"]
        parts.append(f"{n} incubator idea{'' if n == 1 else 's'} in research")
    if "idea_ledger" in active:
        n = active["idea_ledger"]
        parts.append(f"{n} idea-ledger entr{'y' if n == 1 else 'ies'} >{config.get('idea_ledger_stale_months', 6)}mo (GC-eligible)")
    if "promote_suggest" in active:
        n = active["promote_suggest"]
        thr = config.get("promote_mention_threshold", 3)
        parts.append(f"{n} idea{'' if n == 1 else 's'} surfaced ≥{thr}× — consider `/memory promote`")
    if "stale_promoted" in active:
        n = active["stale_promoted"]
        d = config.get("stale_promotion_days", 30)
        parts.append(f"{n} skill-watchlist {'pattern' if n == 1 else 'patterns'} promoted >{d}d ago without action — author or dismiss (`/memory watchlist`)")
    if "digest_stale_days" in active:
        n = active["digest_stale_days"]
        parts.append(f"observability digest ladder: no digest in {n}d — stalled (`/console` for detail)")
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
        # First-use: materialize the operator-tunable config so they can find and
        # edit it (idempotent — a no-op once it exists; a re-seed never clobbers).
        # The whole push-surface works on defaults without it, but the operator
        # can't tune what they can't see — and the SessionStart briefing is the
        # earliest, most reliable "first run" moment across the chains.
        ao.seed_config(vault)
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
