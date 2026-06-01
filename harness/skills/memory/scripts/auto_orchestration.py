#!/usr/bin/env python3
"""auto_orchestration.py — state + config primitives for the V4 #23 memory
push-surface (auto-orchestration).

Two operator-facing artifacts, both vault-resident:

  <vault>/_meta/auto-orchestration-state.json
      Machine state. { "last_fire": {<chain>: <iso8601>},
                       "last_shown": {<signal>: <value>} }
      - last_fire  → cooldown bookkeeping (one timestamp per chain).
      - last_shown → the SessionStart "only when state shifted" guard:
                     a snapshot of the pending-state signal counts last surfaced.

  <vault>/personal-private/auto-orchestration-config.md
      Operator-editable markdown carrying thresholds / cooldowns / chain toggles
      inside a fenced ``` settings block. Auto-seeded with sensible defaults on
      first use; re-seeding NEVER clobbers an existing file (operator edits win).

This module is imported by the SessionStart briefing generator (task 3), the
idle-chain (task 4), the phase-integration dispatch (task 5), and the nudges
(task 6). It is stdlib-only (no pyyaml) so it can run from any hook environment.

The functions are pure-ish (state is passed in / returned); only `save_state`,
`seed_config`, and the `_resolve_vault_path` env read touch the outside world.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# ── defaults ────────────────────────────────────────────────────────────────
# Each key's type here is also the coercion target when parsing the config md.
DEFAULT_CONFIG: dict[str, object] = {
    # chain toggles — turn an emission off entirely
    "enable_briefing": True,
    "enable_idle_chain": True,
    "enable_phase_integration": True,
    "enable_promote_suggest": True,
    "enable_stale_promotion_nudge": True,
    # briefing thresholds — a signal is "worth surfacing" at/above its threshold
    "inbox_threshold": 10,
    "watchlist_high_threshold": 1,
    "incubator_pending_threshold": 1,
    "idea_ledger_stale_months": 6,
    # nudge thresholds
    "promote_mention_threshold": 3,
    "stale_promotion_days": 30,
    # cooldowns (hours) — minimum gap between fires of a chain
    "briefing_cooldown_hours": 8,
    "idle_chain_cooldown_hours": 24,
    "phase_reflect_cooldown_hours": 1,
}

_CONFIG_FILENAME = "auto-orchestration-config.md"
_STATE_FILENAME = "auto-orchestration-state.json"


# ── vault + path resolution (mirrors the other memory scripts) ──────────────
def _resolve_vault_path(arg_path: str | None = None) -> Path:
    """Resolve vault path: arg → MEMORY_VAULT_PATH env → error."""
    if arg_path:
        return Path(arg_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    raise ValueError(
        "vault path required: pass --vault-path or set MEMORY_VAULT_PATH"
    )


def state_path(vault: Path) -> Path:
    return Path(vault) / "_meta" / _STATE_FILENAME


def config_path(vault: Path) -> Path:
    return Path(vault) / "personal-private" / _CONFIG_FILENAME


# ── state ───────────────────────────────────────────────────────────────────
def _empty_state() -> dict:
    return {"last_fire": {}, "last_shown": {}}


def load_state(vault: Path) -> dict:
    """Read the state file, returning an empty-but-shaped dict if absent or
    unreadable (state is advisory — a corrupt file must never block a hook)."""
    p = state_path(vault)
    if not p.exists():
        return _empty_state()
    try:
        # errors="replace": a non-UTF-8 file degrades (it'll fail json parse →
        # empty shape) rather than raising out of a hook. ValueError catches the
        # UnicodeDecodeError edge belt-and-suspenders.
        data = json.loads(p.read_text(encoding="utf-8", errors="replace") or "{}")
    except (json.JSONDecodeError, OSError, ValueError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    data.setdefault("last_fire", {})
    data.setdefault("last_shown", {})
    if not isinstance(data["last_fire"], dict):
        data["last_fire"] = {}
    if not isinstance(data["last_shown"], dict):
        data["last_shown"] = {}
    return data


def save_state(vault: Path, state: dict) -> None:
    p = state_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# ── cooldowns ───────────────────────────────────────────────────────────────
def _parse_iso(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    # normalize naive → UTC so comparisons never raise
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def should_fire(state: dict, chain: str, now: datetime, cooldown_hours: float) -> bool:
    """True iff `chain` has never fired, or its last fire is older than the
    cooldown window. A non-positive cooldown means "always eligible"."""
    if cooldown_hours <= 0:
        return True
    last = state.get("last_fire", {}).get(chain)
    if not last:
        return True
    last_dt = _parse_iso(last)
    if last_dt is None:
        return True  # unparseable timestamp → treat as eligible, don't wedge
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    elapsed_hours = (now - last_dt).total_seconds() / 3600.0
    return elapsed_hours >= cooldown_hours


def record_fire(state: dict, chain: str, now: datetime) -> dict:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    state.setdefault("last_fire", {})[chain] = now.isoformat()
    return state


# ── "only when state shifted" guard ─────────────────────────────────────────
def state_shifted_since_last_shown(state: dict, signals: dict) -> bool:
    """True iff the current pending-state `signals` differ from the snapshot we
    last surfaced. Drives the SessionStart briefing's anti-fatigue gate: re-show
    only when something actually changed (a count crossed, appeared, or cleared)."""
    last = state.get("last_shown", {})
    if not isinstance(last, dict):
        return True
    # Normalize keys present on either side; a missing key reads as 0.
    keys = set(signals) | set(last)
    for k in keys:
        if signals.get(k, 0) != last.get(k, 0):
            return True
    return False


def record_shown(state: dict, signals: dict) -> dict:
    state["last_shown"] = dict(signals)
    return state


# ── config (operator-editable markdown) ─────────────────────────────────────
def _coerce(key: str, raw: str) -> object:
    """Coerce a raw string value to the type of DEFAULT_CONFIG[key]."""
    default = DEFAULT_CONFIG.get(key)
    raw = raw.strip()
    if isinstance(default, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return default
    return raw


_KV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.+?)\s*$")
_FENCE_RE = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)


def _parse_config_md(text: str) -> dict:
    """Parse `key = value` / `key: value` pairs from the config's settings block.
    Prefers a fence whose info-string is `settings` (so an operator can place an
    illustrative fence above it without breaking parsing); falls back to the first
    fence, else the whole document. Unknown keys + non-matching lines are ignored;
    comment lines (#) are skipped. Returns only recognized keys."""
    blocks = _FENCE_RE.findall(text)  # list of (info_string, body)
    body = None
    for info, b in blocks:
        if "settings" in info.lower():
            body = b
            break
    if body is None:
        body = blocks[0][1] if blocks else text
    out: dict[str, object] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        km = _KV_RE.match(line)
        if not km:
            continue
        key, raw = km.group(1), km.group(2)
        if key in DEFAULT_CONFIG:
            # strip trailing inline comments like `10   # default`
            raw = raw.split("#", 1)[0]
            out[key] = _coerce(key, raw)
    return out


def load_config(vault: Path) -> dict:
    """Return the effective config: DEFAULT_CONFIG overlaid with any values the
    operator set in the config md. Missing/absent file → all defaults."""
    cfg = dict(DEFAULT_CONFIG)
    p = config_path(vault)
    if p.exists():
        try:
            # errors="replace" keeps a partially-valid config parsing its good
            # lines instead of crashing a hook on non-UTF-8 bytes.
            cfg.update(_parse_config_md(p.read_text(encoding="utf-8", errors="replace")))
        except (OSError, ValueError):
            pass
    return cfg


def _default_config_md() -> str:
    lines = [
        "# Auto-orchestration config",
        "",
        "Operator-editable settings for the Agent M memory **push surface** (V4 #23).",
        "Edit the values in the `settings` block below; this file is only auto-seeded",
        "once — your edits are never overwritten. Delete the file to re-seed defaults.",
        "",
        "- **`enable_*`** toggles turn an emission off entirely.",
        "- **thresholds** decide when a signal is worth surfacing.",
        "- **`*_cooldown_hours`** set the minimum gap between fires of a chain.",
        "",
        "```settings",
    ]
    for k, v in DEFAULT_CONFIG.items():
        val = "true" if v is True else "false" if v is False else v
        lines.append(f"{k} = {val}")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def seed_config(vault: Path) -> bool:
    """Create the config md with defaults iff it doesn't exist. Returns True if a
    file was written, False if one already existed (idempotent; never clobbers)."""
    p = config_path(vault)
    if p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_default_config_md(), encoding="utf-8")
    return True


# ── CLI (for the bash hooks to shell out to; thin over the functions above) ──
def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="auto_orchestration.py")
    parser.add_argument("--vault-path", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed-config")
    sub.add_parser("show-config")
    sf = sub.add_parser("should-fire")
    sf.add_argument("chain")
    sf.add_argument("--cooldown-hours", type=float, default=None)
    rf = sub.add_parser("record-fire")
    rf.add_argument("chain")

    args = parser.parse_args(argv[1:])
    try:
        vault = _resolve_vault_path(args.vault_path)
    except ValueError as e:
        print(f"auto_orchestration: {e}", file=__import__("sys").stderr)
        return 1

    if args.cmd == "seed-config":
        wrote = seed_config(vault)
        print("seeded" if wrote else "kept")
        return 0
    if args.cmd == "show-config":
        print(json.dumps(load_config(vault), indent=2, sort_keys=True))
        return 0
    if args.cmd == "should-fire":
        cfg = load_config(vault)
        cooldown = args.cooldown_hours
        if cooldown is None:
            cooldown = float(cfg.get(f"{args.chain}_cooldown_hours", 0) or 0)
        state = load_state(vault)
        fire = should_fire(state, args.chain, datetime.now(timezone.utc), cooldown)
        # exit 0 = should fire, 1 = cooled down (shell-friendly)
        return 0 if fire else 1
    if args.cmd == "record-fire":
        state = load_state(vault)
        record_fire(state, args.chain, datetime.now(timezone.utc))
        save_state(vault, state)
        print("recorded")
        return 0
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv))
