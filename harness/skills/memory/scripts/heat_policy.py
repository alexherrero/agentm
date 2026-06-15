#!/usr/bin/env python3
"""heat_policy.py — heat-based always-load entry curation for MemoryVault.

Part G of ROADMAP #46 (token-efficiency). Implements:
  - Per-entry heat sidecar (.heat.json) read/write
  - Conservative promote/demote policy for always-load entry set
  - Pin (never-demote) support via heat_pin frontmatter field

Heat signal: on-demand recall hits recorded by recall.py's prompt_submit()
via record_hit(). Always-load emission frequency is uniform across all entries
and carries no salience signal — only on-demand hits are tracked.

Public API (called by recall.py):
  record_hit(vault, slug)              — record one on-demand recall hit
  run_policy(vault, *, dry_run, stderr) — evaluate + optionally apply tier changes
  pin_entry(vault, slug, *, stderr)    — pin entry (restore to always-load, mark heat_pin)

Policy invariants (locked design calls):
  - Never demote below MIN_ALWAYS_LOAD entries (safety floor)
  - Never demote a heat_pin: true entry
  - Sustained cold (COLD_SESSIONS_MIN sessions with 0 hits) to demote
  - Sustained hot (HOT_HITS_MIN hits across HOT_SESSIONS_MIN distinct sessions) to promote
  - Single-session spike does NOT promote (spike guard: hit_sessions >= HOT_SESSIONS_MIN)
  - Tier changes are never silent — logged on stderr
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Heat sidecar filename at vault root (hidden by leading dot, not an Obsidian note).
HEAT_SIDECAR_NAME = ".heat.json"

# Always-load directory (relative to vault root), matching recall.py convention.
_ALWAYS_LOAD_REL = Path("personal-private") / "_always-load"

# Policy thresholds (conservative by design — incorrect demotions are silent quality bugs).
COLD_SESSIONS_MIN = 10   # min prompt-submit sessions recorded before cold-demotion is eligible
HOT_HITS_MIN = 3         # min total hits for a promote candidate
HOT_SESSIONS_MIN = 2     # min distinct sessions with a hit (spike guard)
MIN_ALWAYS_LOAD = 5      # safety floor: never demote below this count of always-load entries


def _load_heat(vault: Path) -> dict:
    """Load the heat sidecar. Returns default structure if missing or corrupt."""
    path = vault / HEAT_SIDECAR_NAME
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("version") == 1:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "total_sessions": 0, "entries": {}}


def _save_heat(vault: Path, data: dict) -> None:
    """Atomically write the heat sidecar via vault_lock primitives."""
    try:
        from vault_lock import atomic_write  # type: ignore
    except ImportError:
        # Fallback: plain write (no concurrent-write safety, acceptable for
        # best-effort heat tracking if vault_lock is unavailable).
        path = vault / HEAT_SIDECAR_NAME
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return
    path = vault / HEAT_SIDECAR_NAME
    content = json.dumps(data, indent=2, sort_keys=True)
    atomic_write(path, content)


def record_hit(vault: Path, slug: str, *, today: str | None = None) -> None:
    """Record one on-demand recall hit for `slug`.

    Increments the entry's hit count and hit_session count (if this is the
    first hit of the day). Also increments total_sessions if this is the
    first hit of the day across ALL entries (one session counter tick per day).

    Best-effort: all exceptions are swallowed — heat tracking must never
    block or fail the recall pipeline.

    `today` is injectable for tests (ISO date string YYYY-MM-DD).
    """
    try:
        if today is None:
            import datetime
            today = datetime.date.today().isoformat()
        try:
            from vault_lock import vault_mutex  # type: ignore
            ctx = vault_mutex(vault)
        except ImportError:
            from contextlib import nullcontext
            ctx = nullcontext()

        with ctx:
            data = _load_heat(vault)
            entries = data.setdefault("entries", {})
            entry = entries.setdefault(slug, {"hits": 0, "hit_sessions": 0, "last_hit": None})

            was_new_day = entry.get("last_hit") != today
            entry["hits"] = entry.get("hits", 0) + 1
            if was_new_day:
                entry["hit_sessions"] = entry.get("hit_sessions", 0) + 1
            entry["last_hit"] = today

            # Increment total_sessions once per day (first new-day hit globally).
            last_session_day = data.get("last_session_day")
            if last_session_day != today:
                data["total_sessions"] = data.get("total_sessions", 0) + 1
                data["last_session_day"] = today

            _save_heat(vault, data)
    except Exception:  # noqa: BLE001 — heat tracking is best-effort
        pass


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Minimal frontmatter parser (mirrors recall.py's inline parser)."""
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end == -1:
        return {}, content
    fm_text = content[4:end]
    body = content[end + 5:]
    fm: dict[str, str] = {}
    for line in fm_text.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        fm[key] = value
    return fm, body


def _patch_frontmatter(content: str, updates: dict[str, str | bool]) -> str:
    """Apply `updates` to frontmatter fields.

    Adds new fields after existing ones if not present. Updates values in-place
    if field already exists. Preserves body unchanged.
    """
    if not content.startswith("---\n"):
        # No frontmatter — prepend a new one.
        fm_lines = ["---"]
        for k, v in updates.items():
            fm_lines.append(f"{k}: {_fm_value(v)}")
        fm_lines.append("---")
        return "\n".join(fm_lines) + "\n" + content

    end = content.find("\n---\n", 4)
    if end == -1:
        return content  # Malformed — leave unchanged.

    fm_text = content[4:end]
    body = content[end + 5:]

    lines = fm_text.split("\n")
    remaining_updates = dict(updates)
    new_lines = []
    for line in lines:
        if ":" in line:
            key = line.partition(":")[0].strip()
            if key in remaining_updates:
                new_lines.append(f"{key}: {_fm_value(remaining_updates.pop(key))}")
                continue
        new_lines.append(line)
    # Append any new fields not found in existing frontmatter.
    for k, v in remaining_updates.items():
        new_lines.append(f"{k}: {_fm_value(v)}")

    return "---\n" + "\n".join(new_lines) + "\n---\n" + body


def _fm_value(v: str | bool) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _find_entry_in_vault(vault: Path, slug: str) -> Path | None:
    """Walk vault to find an entry with the given slug (stem).

    Excludes `_archive/` and hidden directories. Returns the first match
    or None if not found.
    """
    for dirpath, dirnames, filenames in os.walk(vault):
        # Prune excluded dirs.
        dirnames[:] = [
            d for d in dirnames
            if d != "_archive" and not d.startswith(".")
        ]
        for fname in filenames:
            if fname == f"{slug}.md":
                return Path(dirpath) / fname
    return None


def run_policy(
    vault: Path,
    *,
    dry_run: bool = True,
    stderr=sys.stderr,
) -> dict:
    """Evaluate the heat policy and optionally apply tier changes.

    Args:
        vault:    MemoryVault root path.
        dry_run:  If True (default), report actions without applying them.
                  Pass dry_run=False to move files.
        stderr:   Output stream for markers (demotion/promotion notices).

    Returns a dict with:
        {
          "demoted": [<slug>, ...],
          "promoted": [<slug>, ...],
          "pinned_skipped": [<slug>, ...],
          "floor_skipped": <int>,  # candidates dropped due to MIN_ALWAYS_LOAD floor
          "too_early": <bool>,     # True if total_sessions < COLD_SESSIONS_MIN
        }
    """
    result: dict = {
        "demoted": [],
        "promoted": [],
        "pinned_skipped": [],
        "floor_skipped": 0,
        "too_early": False,
    }

    always_load_dir = vault / _ALWAYS_LOAD_REL
    if not vault.exists():
        return result

    heat = _load_heat(vault)
    total_sessions = heat.get("total_sessions", 0)
    entries_heat = heat.get("entries", {})

    # --- Demotion pass: walk always-load entries ---
    always_load_entries = sorted(always_load_dir.glob("*.md")) if always_load_dir.exists() else []
    current_count = len(always_load_entries)

    demote_candidates: list[tuple[Path, dict, str]] = []  # (path, fm, slug)
    for md_path in always_load_entries:
        slug = md_path.stem
        try:
            content = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, _ = _parse_frontmatter(content)

        if fm.get("status") == "superseded":
            continue

        # Never demote pinned entries.
        if fm.get("heat_pin") == "true":
            result["pinned_skipped"].append(slug)
            continue

        # Cold check: zero hits across sufficient sessions.
        entry_heat = entries_heat.get(slug, {})
        hits = entry_heat.get("hits", 0)
        if hits == 0 and total_sessions >= COLD_SESSIONS_MIN:
            demote_candidates.append((md_path, fm, slug))

    # Too early to judge — not enough sessions recorded.
    if total_sessions < COLD_SESSIONS_MIN and not any(
        entries_heat.get(s, {}).get("hits", 0) == 0 for _, _, s in demote_candidates
    ):
        # Only set too_early when there are no cold candidates at all.
        pass
    if total_sessions < COLD_SESSIONS_MIN and not demote_candidates:
        result["too_early"] = True

    # Apply safety floor: keep at least MIN_ALWAYS_LOAD entries.
    allowed_demotions = max(0, current_count - MIN_ALWAYS_LOAD)
    if len(demote_candidates) > allowed_demotions:
        result["floor_skipped"] = len(demote_candidates) - allowed_demotions
        demote_candidates = demote_candidates[:allowed_demotions]

    for md_path, fm, slug in demote_candidates:
        group = fm.get("group", "personal-private")
        dest_dir = vault / group
        dest_path = dest_dir / md_path.name

        print(
            f"[heat-policy] DEMOTE {slug}: 0 hits over {total_sessions} sessions"
            + (" (dry-run)" if dry_run else ""),
            file=stderr,
        )
        if not dry_run:
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                # Patch frontmatter: always_load → false.
                content = md_path.read_text(encoding="utf-8")
                patched = _patch_frontmatter(content, {"always_load": False})
                try:
                    from vault_lock import atomic_write  # type: ignore
                    atomic_write(dest_path, patched)
                except ImportError:
                    dest_path.write_text(patched, encoding="utf-8")
                md_path.unlink()
                result["demoted"].append(slug)
            except Exception as e:
                print(f"[heat-policy] DEMOTE {slug} failed: {e}", file=stderr)
        else:
            result["demoted"].append(slug)

    # --- Promotion pass: walk on-demand entries with sustained hot signal ---
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [
            d for d in dirnames
            if d not in {"_archive", "_always-load"} and not d.startswith(".")
        ]
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            md_path = Path(dirpath) / fname
            slug = md_path.stem

            # Skip if already in always-load.
            if (always_load_dir / fname).exists():
                continue

            entry_heat = entries_heat.get(slug, {})
            hits = entry_heat.get("hits", 0)
            hit_sessions = entry_heat.get("hit_sessions", 0)

            # Promotion criteria: sustained hot (spike guard: must span 2+ sessions).
            if hits >= HOT_HITS_MIN and hit_sessions >= HOT_SESSIONS_MIN:
                try:
                    content = md_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                fm, _ = _parse_frontmatter(content)
                if fm.get("status") == "superseded":
                    continue

                dest_path = always_load_dir / fname
                print(
                    f"[heat-policy] PROMOTE {slug}: {hits} hits over {hit_sessions} sessions"
                    + (" (dry-run)" if dry_run else ""),
                    file=stderr,
                )
                if not dry_run:
                    try:
                        always_load_dir.mkdir(parents=True, exist_ok=True)
                        patched = _patch_frontmatter(content, {"always_load": True})
                        try:
                            from vault_lock import atomic_write  # type: ignore
                            atomic_write(dest_path, patched)
                        except ImportError:
                            dest_path.write_text(patched, encoding="utf-8")
                        md_path.unlink()
                        result["promoted"].append(slug)
                    except Exception as e:
                        print(f"[heat-policy] PROMOTE {slug} failed: {e}", file=stderr)
                else:
                    result["promoted"].append(slug)

    return result


def pin_entry(vault: Path, slug: str, *, stderr=sys.stderr) -> bool:
    """Pin `slug` to always-load (heat_pin: true) and restore it if demoted.

    If the entry is currently in the always-load dir: add heat_pin to frontmatter.
    If the entry is elsewhere in the vault: move it to always-load + add heat_pin.
    If not found anywhere: log error, return False.

    Returns True on success, False on failure.
    """
    always_load_dir = vault / _ALWAYS_LOAD_REL
    always_load_path = always_load_dir / f"{slug}.md"

    if always_load_path.exists():
        # Already in always-load — just add/update the pin.
        try:
            content = always_load_path.read_text(encoding="utf-8")
            patched = _patch_frontmatter(content, {"heat_pin": True})
            try:
                from vault_lock import atomic_write  # type: ignore
                atomic_write(always_load_path, patched)
            except ImportError:
                always_load_path.write_text(patched, encoding="utf-8")
            print(f"[heat-policy] PIN {slug}: marked heat_pin=true (already in always-load)", file=stderr)
            return True
        except Exception as e:
            print(f"[heat-policy] PIN {slug} failed: {e}", file=stderr)
            return False

    # Not in always-load — search vault.
    src = _find_entry_in_vault(vault, slug)
    if src is None:
        print(f"[heat-policy] PIN {slug}: entry not found in vault", file=stderr)
        return False

    try:
        content = src.read_text(encoding="utf-8")
        patched = _patch_frontmatter(content, {"always_load": True, "heat_pin": True})
        always_load_dir.mkdir(parents=True, exist_ok=True)
        try:
            from vault_lock import atomic_write  # type: ignore
            atomic_write(always_load_path, patched)
        except ImportError:
            always_load_path.write_text(patched, encoding="utf-8")
        src.unlink()
        print(f"[heat-policy] PIN {slug}: restored to always-load + marked heat_pin=true", file=stderr)
        return True
    except Exception as e:
        print(f"[heat-policy] PIN {slug} failed: {e}", file=stderr)
        return False
