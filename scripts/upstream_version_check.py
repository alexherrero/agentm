#!/usr/bin/env python3
"""upstream_version_check — fetch + cache latest GitHub release tags.

Per V4 #30 plan #22 task 7. SessionStart hook (in release mode only)
checks whether a newer agentm or crickets release exists upstream;
surfaces a one-line stderr notice if so. **Never auto-apply** per
locked DC-3 — operator runs `agentm-update` explicitly.

Cache TTL: 24h (default). Cached at:
    <install-prefix>/.upstream-version-check-cache.json

Cache shape:
    {
      "fetched_at": "2026-05-27T18:00:00Z",
      "alexherrero/agentm":   "v4.3.0",
      "alexherrero/crickets": "v2.1.0"
    }

Stdlib-only (ADR 0001) — uses urllib.request, not `requests`. Graceful-skip
if network unreachable + cache is stale: emit one-line warn but exit 0.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


_CACHE_FILENAME = ".upstream-version-check-cache.json"
_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h
_REPOS = ["alexherrero/agentm", "alexherrero/crickets"]
_USER_AGENT = "agentm-upstream-version-check/v4.3.0"


def cache_path(install_prefix: Path | str) -> Path:
    return Path(install_prefix) / _CACHE_FILENAME


def read_cache(install_prefix: Path | str) -> Optional[dict]:
    """Return cache dict or None if absent / malformed."""
    p = cache_path(install_prefix)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_cache(install_prefix: Path | str, data: dict) -> None:
    """Write cache atomically (tmp+rename)."""
    p = cache_path(install_prefix)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def _iso_utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_iso_utc(s: str) -> float:
    """Parse our ISO-Z timestamp to epoch seconds. Returns 0 on failure.

    Uses calendar.timegm() — the canonical stdlib pattern for "treat
    struct_time as UTC, return epoch seconds." Unlike time.mktime() which
    interprets as local time + has DST edge cases.
    """
    try:
        if s.endswith("Z"):
            s = s[:-1]
        t = time.strptime(s, "%Y-%m-%dT%H:%M:%S")
        return float(calendar.timegm(t))
    except (ValueError, TypeError):
        return 0


def is_cache_fresh(cache: dict, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> bool:
    """True if cache's `fetched_at` is within `ttl_seconds` of now."""
    fetched = cache.get("fetched_at")
    if not isinstance(fetched, str):
        return False
    epoch = _parse_iso_utc(fetched)
    if epoch == 0:
        return False
    return (time.time() - epoch) < ttl_seconds


def _fetch_latest_tag(repo: str, *, timeout: float = 5.0) -> Optional[str]:
    """Fetch the latest release tag for `<owner>/<repo>` via GitHub API.

    Uses unauthenticated API (60 req/hr per IP); for higher limits, the
    caller can extend this with $GITHUB_TOKEN handling. Returns None on
    network error / rate limit / no releases.
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    tag = data.get("tag_name")
    if isinstance(tag, str) and tag:
        return tag
    return None


def refresh_cache(
    install_prefix: Path | str,
    *,
    repos: Optional[list[str]] = None,
    fetcher=_fetch_latest_tag,
) -> dict:
    """Fetch latest tags for all configured repos; write cache; return data.

    Falls back to previously-cached values per-repo if fetch fails (so
    transient network errors don't blow away the whole cache). `fetcher`
    parameter exists for unit-test injection (mock GitHub responses).
    """
    repos = repos or _REPOS
    prior = read_cache(install_prefix) or {}
    new_data: dict = {"fetched_at": _iso_utc_now()}
    for repo in repos:
        tag = fetcher(repo)
        if tag is None:
            # Preserve previous value if any (better than dropping the field)
            if repo in prior:
                new_data[repo] = prior[repo]
        else:
            new_data[repo] = tag
    write_cache(install_prefix, new_data)
    return new_data


def _version_tuple(tag: str) -> tuple:
    """Parse 'v4.2.0' or '4.2.0' into (4, 2, 0). Unknowns sort first."""
    s = tag.lstrip("v")
    parts: list[int] = []
    for piece in s.split("."):
        # Strip pre-release suffix (e.g. "0-rc1" → "0")
        piece = piece.split("-")[0]
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def find_newer_versions(
    cache: dict,
    *,
    installed: dict[str, str],
) -> list[tuple[str, str, str]]:
    """Compare cached latest tags against installed versions.

    `installed`: {repo_full_name: installed_tag}, e.g.
        {"alexherrero/agentm": "v4.2.0", "alexherrero/crickets": "v2.0.0"}

    Returns list of (repo, installed_tag, latest_tag) for repos where
    latest > installed.
    """
    out: list[tuple[str, str, str]] = []
    for repo, installed_tag in installed.items():
        latest = cache.get(repo)
        if not isinstance(latest, str):
            continue
        if not installed_tag:
            continue
        if _version_tuple(latest) > _version_tuple(installed_tag):
            out.append((repo, installed_tag, latest))
    return out


def check_and_notify(
    install_prefix: Path | str,
    *,
    installed: Optional[dict[str, str]] = None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    force_refresh: bool = False,
    fetcher=_fetch_latest_tag,
) -> dict:
    """High-level: refresh cache if stale, compare to installed, surface notices.

    Returns `{cache, notices: [(repo, installed, latest), ...], cache_stale: bool}`.

    Emits one-line stderr per repo with a newer version available. Never
    auto-applies per DC-3.
    """
    cache = read_cache(install_prefix)
    cache_stale = (cache is None) or (not is_cache_fresh(cache, ttl_seconds))
    if cache_stale or force_refresh:
        cache = refresh_cache(install_prefix, fetcher=fetcher)
        cache_stale = False  # just refreshed
    notices: list[tuple[str, str, str]] = []
    if installed:
        notices = find_newer_versions(cache, installed=installed)
        for repo, installed_tag, latest_tag in notices:
            slug = repo.split("/")[-1]
            print(
                f"[install-state-sync] {latest_tag} available for {slug} "
                f"(installed: {installed_tag}). Run `agentm-update` to apply.",
                file=sys.stderr,
            )
    return {"cache": cache, "notices": notices, "cache_stale": cache_stale}


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch + cache latest GitHub release tags; surface notices for newer versions.",
    )
    parser.add_argument(
        "--install-prefix",
        default=None,
        help="install prefix (default: $AGENTM_INSTALL_PREFIX or ~/.claude)",
    )
    parser.add_argument(
        "--installed",
        default=None,
        help="JSON map of installed versions, e.g. '{\"alexherrero/agentm\": \"v4.2.0\"}'",
    )
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=_DEFAULT_TTL_SECONDS,
        help="cache TTL (default: 86400 = 24h)",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="bypass cache + force re-fetch",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.install_prefix:
        prefix = Path(os.path.expanduser(args.install_prefix))
    elif os.environ.get("AGENTM_INSTALL_PREFIX"):
        prefix = Path(os.path.expanduser(os.environ["AGENTM_INSTALL_PREFIX"]))
    else:
        prefix = Path.home() / ".claude"

    installed = None
    if args.installed:
        try:
            installed = json.loads(args.installed)
        except json.JSONDecodeError as exc:
            print(f"[upstream_version_check] --installed must be JSON: {exc}", file=sys.stderr)
            return 2

    result = check_and_notify(
        prefix,
        installed=installed,
        ttl_seconds=args.ttl_seconds,
        force_refresh=args.force_refresh,
    )
    # Emit JSON summary to stdout (notices go to stderr).
    serializable = {
        "cache": result["cache"],
        "notices": [
            {"repo": r, "installed": i, "latest": l}
            for r, i, l in result["notices"]
        ],
        "cache_stale": result["cache_stale"],
    }
    sys.stdout.write(json.dumps(serializable, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
