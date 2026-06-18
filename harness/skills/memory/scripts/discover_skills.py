#!/usr/bin/env python3
# discover_skills.py — internet skill-discovery scan (plan #7b task 3).
#
# Periodically fetches a curated set of "skill-shaped pattern" sources from
# the internet; caches each fetch as a dated snapshot; diffs against the
# previous snapshot; emits "new content since last scan" candidate signals
# for the adapt-don't-import workflow (task 4) to evaluate.
#
# Locked design calls referenced:
#   - Source whitelist lives at <vault>/personal/skill-discovery-sources.md
#     (operator-editable in Obsidian; lines starting with # are comments;
#      blank lines ignored; URLs preserved in file order — operator can
#      prioritize which sources are scanned first).
#   - Cadence default: weekly (7 days). Configurable via --cadence-days +
#     MEMORY_SKILL_DISCOVERY_CADENCE_DAYS env var.
#   - First-run auto-seeds the source whitelist with 4 default URLs in the
#     operator-specified order: Anthropic Cookbook + awesome-claude-code
#     + awesome-mcp-servers + awesome-llm-apps.
#   - Adapt-don't-import principle (task 4) enforced architecturally — this
#     script only fetches + caches + diffs; never writes to MemoryVault's
#     skill directories. Adoption decisions live entirely downstream.
#   - stdlib-only (urllib.request); no new dependencies. Local-only embeddings
#     pattern (plan #18) confirmed the tolerance for stdlib-first design.
#   - Graceful-skip on every error: source whitelist missing → auto-seed;
#     fetch timeout / 4xx / 5xx → log + continue with other sources;
#     network outage → state file unchanged, idle hook re-tries next pass.
#
# Cache layout (under <vault>/_meta/skill-discovery-cache/):
#   <source-slug>/<YYYY-MM-DD>.md       — full snapshot of the URL response
#   <source-slug>/diff-<YYYY-MM-DD>.md  — added lines vs. previous snapshot
#   state.json                          — { last_scan, sources: { <slug>: { ... } } }
#
# Output: JSON summary on stdout (single object). Per-source stats are
# in summary["sources"]; one entry per attempted source.

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import socket
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# Default source whitelist seed — operator-specified order: cookbook,
# awesome-claude-code, awesome-mcp-servers, awesome-llm-apps. README.md
# is the natural entry point per source; operator can swap to deeper
# paths (e.g. subdirectory README) by editing the file in Obsidian.
_DEFAULT_SOURCES = [
    (
        "anthropic-cookbook",
        "https://raw.githubusercontent.com/anthropics/anthropic-cookbook/main/README.md",
        "Official Anthropic recipes + agent patterns",
    ),
    (
        "awesome-claude-code",
        "https://raw.githubusercontent.com/hesreallyhim/awesome-claude-code/main/README.md",
        "Community-curated Claude Code patterns + hooks + skills",
    ),
    (
        "awesome-mcp-servers",
        "https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md",
        "MCP server catalog (adoption candidates alongside skill patterns)",
    ),
    (
        "awesome-llm-apps",
        "https://raw.githubusercontent.com/Shubhamsaboo/awesome-llm-apps/main/README.md",
        "Broader LLM-app patterns (cross-vendor; noisier signal)",
    ),
]

_DEFAULT_CADENCE_DAYS = 7
_FETCH_TIMEOUT_SEC = 10
_USER_AGENT = "crickets-skill-discovery/0.1 (+local; stdlib-urllib)"


def _resolve_vault_path(arg_path: str | None) -> Path:
    """Resolve vault path: arg → MEMORY_VAULT_PATH env → error."""
    if arg_path:
        return Path(arg_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    raise ValueError(
        "vault path required: pass --vault-path or set MEMORY_VAULT_PATH"
    )


def _resolve_cadence_days(arg_days: int | None) -> int:
    """Resolve cadence: arg → MEMORY_SKILL_DISCOVERY_CADENCE_DAYS env → default 7."""
    if arg_days is not None:
        return max(1, int(arg_days))
    env = os.environ.get("MEMORY_SKILL_DISCOVERY_CADENCE_DAYS", "").strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return _DEFAULT_CADENCE_DAYS


def _whitelist_path(vault: Path) -> Path:
    return vault / "personal" / "skill-discovery-sources.md"


def _cache_root(vault: Path) -> Path:
    return vault / "_meta" / "skill-discovery-cache"


def _state_path(vault: Path) -> Path:
    return _cache_root(vault) / "state.json"


def _seed_whitelist(target: Path) -> None:
    """Write the default source whitelist if one doesn't exist yet.

    Format is operator-readable markdown — comment lines + URLs-per-line.
    Operator edits this file in Obsidian to add/remove/reorder sources.
    """
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # All prose lines are #-prefixed comments so parse_whitelist treats
    # only the bare URL lines as sources. The single `#` heading + blank
    # lines surround the content so Obsidian renders the file readably.
    lines = [
        "# Skill-discovery sources",
        "#",
        "# Edit this file to add, remove, or reorder sources for the periodic",
        "# skill-discovery scan. Lines starting with `#` are comments; blank",
        "# lines are ignored. One URL per non-comment line. Order matters:",
        "# sources are scanned top-to-bottom; the scan halts early via",
        "# `--max-sources N` against this order.",
        "#",
        "# Seeded with the operator's confirmed v1 set on first install. Adapt",
        "# the list as new sources prove useful (or noisy) from real scans.",
        "",
    ]
    for slug, url, comment in _DEFAULT_SOURCES:
        lines.append(f"# {slug} — {comment}")
        lines.append(url)
        lines.append("")
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def parse_whitelist(path: Path) -> list[str]:
    """Read URLs from the whitelist file, in file order; ignore comments + blanks."""
    if not path.exists():
        return []
    urls: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def _slug_from_url(url: str) -> str:
    """Derive a deterministic kebab-case slug from a URL.

    GitHub raw URLs (the common v1 case) get `<owner>-<repo>` — readable
    + unique within github. Other hosts get an 8-char hex of the URL —
    less readable but collision-resistant.
    """
    gh_match = re.search(
        r"raw\.githubusercontent\.com/([^/]+)/([^/]+)/", url
    )
    if gh_match:
        owner, repo = gh_match.group(1).lower(), gh_match.group(2).lower()
        slug = re.sub(r"[^a-z0-9]+", "-", f"{owner}-{repo}").strip("-")
        if slug:
            return slug
    gh_match2 = re.search(r"github\.com/([^/]+)/([^/]+?)(?:/|$|\.git)", url)
    if gh_match2:
        owner, repo = gh_match2.group(1).lower(), gh_match2.group(2).lower()
        slug = re.sub(r"[^a-z0-9]+", "-", f"{owner}-{repo}").strip("-")
        if slug:
            return slug
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"url-{h}"


def _load_state(vault: Path) -> dict:
    sf = _state_path(vault)
    if not sf.exists():
        return {"schema_version": 1, "last_scan": None, "sources": {}}
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema_version": 1, "last_scan": None, "sources": {}}
    if not isinstance(data, dict):
        return {"schema_version": 1, "last_scan": None, "sources": {}}
    if data.get("schema_version") != 1:
        return {"schema_version": 1, "last_scan": None, "sources": {}}
    data.setdefault("sources", {})
    return data


def _save_state(vault: Path, state: dict) -> None:
    """Atomic state write via tempfile + rename (same pattern as reflect.corpus)."""
    sf = _state_path(vault)
    sf.parent.mkdir(parents=True, exist_ok=True)
    tmp = sf.with_suffix(sf.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(tmp, sf)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_within_cadence(state: dict, cadence_days: int) -> bool:
    """True if last_scan is within the cadence window (skip-fetch case)."""
    last = state.get("last_scan")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    now = datetime.now(timezone.utc)
    age = (now - last_dt).total_seconds()
    return age < cadence_days * 86400


def _fetch_url(url: str) -> tuple[int, str, str]:
    """Fetch URL via urllib. Returns (status_code, content, error_message).

    On success: (200, body, ""). On HTTP non-2xx: (status, "", msg).
    On network/timeout/DNS: (0, "", msg).
    """
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            status = getattr(resp, "status", 200)
            try:
                body = resp.read().decode("utf-8", errors="replace")
            except Exception as e:
                return status, "", f"decode error: {e}"
            return status, body, ""
    except HTTPError as e:
        return e.code, "", f"HTTP {e.code} {e.reason}"
    except URLError as e:
        return 0, "", f"URLError: {e.reason}"
    except socket.timeout:
        return 0, "", f"timeout after {_FETCH_TIMEOUT_SEC}s"
    except Exception as e:  # pragma: no cover
        return 0, "", f"{type(e).__name__}: {e}"


def _previous_snapshot(source_dir: Path, exclude_date: str) -> Path | None:
    """Find the most recent snapshot under source_dir excluding today's file.

    Snapshots are named `YYYY-MM-DD.md`. Returns None if no prior snapshot
    exists (first-ever fetch).
    """
    if not source_dir.exists():
        return None
    candidates: list[Path] = []
    for p in source_dir.iterdir():
        if not p.is_file():
            continue
        if not p.name.endswith(".md"):
            continue
        if p.name.startswith("diff-"):
            continue
        if p.stem == exclude_date:
            continue
        if re.match(r"\d{4}-\d{2}-\d{2}$", p.stem):
            candidates.append(p)
    if not candidates:
        return None
    return sorted(candidates, key=lambda x: x.stem)[-1]


def _compute_diff(old_text: str, new_text: str) -> str:
    """Return added lines (lines in new_text not in old_text) as a single string.

    Uses difflib.unified_diff for line-granularity comparison. Empty string
    if no additions. Includes only +-prefixed lines (drops -/control lines)
    so the diff file is human-readable + downstream-parseable as a list of
    "new content" candidates.
    """
    if not old_text:
        return new_text  # First-ever fetch — everything is "new".
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        n=0,
    )
    added: list[str] = []
    for line in diff:
        # Skip the file headers (--- / +++) and the @@ hunk markers.
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("+"):
            # Drop the leading + so the diff file is grep-friendly.
            added.append(line[1:])
    return "".join(added)


def discover_one_source(
    url: str,
    *,
    vault: Path,
    today: str,
    state: dict,
    dry_run: bool = False,
) -> dict:
    """Fetch + cache + diff a single source. Returns per-source status dict."""
    slug = _slug_from_url(url)
    source_dir = _cache_root(vault) / slug
    if dry_run:
        return {
            "slug": slug, "url": url, "action": "dry-run",
            "status_code": 0, "new_chars": 0, "error": "",
        }
    status, body, err = _fetch_url(url)
    if status != 200 or err:
        # Update state with the error but don't overwrite cache.
        state["sources"][slug] = {
            **state["sources"].get(slug, {}),
            "url": url,
            "last_attempt": _utcnow_iso(),
            "last_status": status,
            "last_error": err or f"HTTP {status}",
        }
        return {
            "slug": slug, "url": url, "action": "error",
            "status_code": status, "new_chars": 0, "error": err or f"HTTP {status}",
        }
    source_dir.mkdir(parents=True, exist_ok=True)
    prev_snapshot = _previous_snapshot(source_dir, exclude_date=today)
    prev_text = ""
    if prev_snapshot is not None:
        try:
            prev_text = prev_snapshot.read_text(encoding="utf-8")
        except OSError:
            prev_text = ""
    snapshot_path = source_dir / f"{today}.md"
    snapshot_path.write_text(body, encoding="utf-8")
    diff_text = _compute_diff(prev_text, body)
    new_chars = len(diff_text)
    if diff_text:
        diff_path = source_dir / f"diff-{today}.md"
        diff_path.write_text(diff_text, encoding="utf-8")
    state["sources"][slug] = {
        "url": url,
        "last_fetch": _utcnow_iso(),
        "last_status": 200,
        "last_snapshot": str(snapshot_path.relative_to(_cache_root(vault))),
        "last_diff_chars": new_chars,
        "last_error": "",
    }
    return {
        "slug": slug, "url": url, "action": "fetched",
        "status_code": 200, "new_chars": new_chars, "error": "",
    }


def discover_skills(
    *,
    vault: Path,
    cadence_days: int = _DEFAULT_CADENCE_DAYS,
    cadence_check: bool = False,
    dry_run: bool = False,
    max_sources: int | None = None,
) -> dict:
    """Orchestrate the per-source scan + return a summary dict."""
    if not vault.exists() or not vault.is_dir():
        raise FileNotFoundError(
            f"vault path does not exist or is not a directory: {vault}"
        )
    whitelist = _whitelist_path(vault)
    seeded = False
    if not whitelist.exists():
        _seed_whitelist(whitelist)
        seeded = True
    urls = parse_whitelist(whitelist)
    state = _load_state(vault)

    summary: dict = {
        "vault": str(vault),
        "whitelist": str(whitelist),
        "whitelist_seeded": seeded,
        "total_sources": len(urls),
        "cadence_days": cadence_days,
        "dry_run": dry_run,
        "cadence_skipped": False,
        "sources": [],
        "fetched": 0,
        "errors": 0,
        "skipped_dry_run": 0,
    }

    if cadence_check and _is_within_cadence(state, cadence_days):
        summary["cadence_skipped"] = True
        return summary

    if not urls:
        return summary

    today = date.today().isoformat()
    sources_to_scan = urls if max_sources is None else urls[:max_sources]

    for url in sources_to_scan:
        result = discover_one_source(
            url, vault=vault, today=today, state=state, dry_run=dry_run,
        )
        summary["sources"].append(result)
        if result["action"] == "fetched":
            summary["fetched"] += 1
        elif result["action"] == "error":
            summary["errors"] += 1
        elif result["action"] == "dry-run":
            summary["skipped_dry_run"] += 1

    if not dry_run:
        state["last_scan"] = _utcnow_iso()
        _save_state(vault, state)
    return summary


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-discover-skills",
        description=(
            "Periodically fetch curated 'skill-shaped pattern' sources from "
            "the internet; cache + diff each fetch; emit 'new content since "
            "last scan' for the adapt-don't-import workflow (task 4) to "
            "evaluate. Source whitelist is operator-editable at "
            "<vault>/personal/skill-discovery-sources.md."
        ),
    )
    parser.add_argument(
        "--vault-path", default=None,
        help="MemoryVault root (default: $MEMORY_VAULT_PATH env var). Required.",
    )
    parser.add_argument(
        "--cadence-days", type=int, default=None,
        help=f"cadence in days (default {_DEFAULT_CADENCE_DAYS}; "
             f"override via $MEMORY_SKILL_DISCOVERY_CADENCE_DAYS env)",
    )
    parser.add_argument(
        "--cadence-check", action="store_true",
        help="skip the fetch entirely if last_scan was within the cadence "
             "window. Used by the idle-hook to avoid hammering URLs on "
             "every idle fire.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="list sources that would be scanned without fetching",
    )
    parser.add_argument(
        "--max-sources", type=int, default=None,
        help="limit to first N sources from the whitelist (testing/scout mode)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        vault = _resolve_vault_path(args.vault_path)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not vault.exists() or not vault.is_dir():
        print(
            f"ERROR: vault path does not exist or is not a directory: {vault}",
            file=sys.stderr,
        )
        return 1
    cadence_days = _resolve_cadence_days(args.cadence_days)
    summary = discover_skills(
        vault=vault,
        cadence_days=cadence_days,
        cadence_check=args.cadence_check,
        dry_run=args.dry_run,
        max_sources=args.max_sources,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
