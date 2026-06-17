#!/usr/bin/env python3
"""agentm memory MCP server — doctor health checks.

Four checks, each returning (passed: bool | None, message: str).
None means the check was skipped with a reason.

  liveness      — GET /health → {"status":"ok"}; daemon must be warm
  token_env     — AGENTM_MCP_TOKEN is set and non-empty
  origin_guard  — POST /mcp with spoofed Origin returns 403 (DNS-rebinding defense)
  index_root_safe — vault_mutex lock root is NOT inside the synced vault path

Usage:
  python3 scripts/memory_mcp_doctor.py           # run liveness + token_env
  python3 scripts/memory_mcp_doctor.py --all     # run all four checks
  python3 scripts/memory_mcp_doctor.py --live    # alias for --all
  python3 scripts/memory_mcp_doctor.py --check liveness
  python3 scripts/memory_mcp_doctor.py --check origin_guard

Exits 0 if all run checks pass (or are skipped), 1 if any fail.
Stdout: one line per check, human-readable.
Stderr: debug messages only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

_DEFAULT_URL = "http://127.0.0.1:7821"
_TIMEOUT: float = 3.0

# Paths that indicate a file is inside a synced/cloud backing.
_SYNC_MARKERS = (
    "/CloudStorage/",  # macOS iCloud, Google Drive, OneDrive via FUSE
    "/Dropbox/",
    "/Google Drive/",
    "/OneDrive/",
    "/Box/",
)


# ── individual checks ─────────────────────────────────────────────────────────

def check_liveness(url: str = _DEFAULT_URL, timeout: float = _TIMEOUT) -> tuple:
    """Check that the daemon responds to GET /health with status=ok."""
    health_url = url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(health_url, timeout=timeout) as resp:
            body = json.loads(resp.read())
            if body.get("status") == "ok":
                return (True, f"daemon is up at {url}")
            return (
                False,
                f"daemon at {url} returned unexpected health body: {body!r}. "
                "Restart: launchctl bootout gui/$UID com.agentm.memory-mcp-server "
                "&& launchctl bootstrap gui/$UID com.agentm.memory-mcp-server",
            )
    except Exception as exc:
        return (
            False,
            f"daemon not reachable at {url} ({exc.__class__.__name__}). "
            "Start: launchctl bootstrap gui/$UID com.agentm.memory-mcp-server",
        )


def check_token_env(env: Optional[dict] = None) -> tuple:
    """Check that AGENTM_MCP_TOKEN is set and non-empty."""
    if env is None:
        env = os.environ
    token = env.get("AGENTM_MCP_TOKEN", "").strip()
    if token:
        return (True, "AGENTM_MCP_TOKEN is set")
    return (
        False,
        "AGENTM_MCP_TOKEN is not set. "
        "Set it in your shell profile (~/.zshrc or ~/.bashrc) and run: "
        "launchctl setenv AGENTM_MCP_TOKEN <your-token>",
    )


def check_origin_guard(url: str = _DEFAULT_URL, timeout: float = _TIMEOUT) -> tuple:
    """Check that the daemon returns 403 on a spoofed cross-origin request."""
    # First verify the daemon is up — skip if it's down.
    live, _ = check_liveness(url, timeout)
    if not live:
        return (None, "skipped — daemon not reachable")

    mcp_url = url.rstrip("/") + "/mcp"
    req = urllib.request.Request(
        mcp_url,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Origin": "http://evil.example.com",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception as exc:
        return (None, f"skipped — HTTP error probing origin guard: {exc}")

    if status == 403:
        return (True, "Origin-validation active — spoofed origin correctly blocked (403)")
    return (
        False,
        f"Origin-validation NOT active — spoofed origin returned {status} (expected 403). "
        "Ensure AGENTM_MCP_TOKEN is set and restart the daemon so "
        "_OriginValidator is wired.",
    )


def _get_vault_lock_root() -> Path:
    """Return the vault_mutex default lock root (injectable for tests)."""
    import sys as _sys
    scripts_dir = Path(__file__).parent
    if str(scripts_dir) not in _sys.path:
        _sys.path.insert(0, str(scripts_dir))
    from vault_lock import _default_lock_root
    return _default_lock_root()


def check_index_root_safe() -> tuple:
    """Check that the vault_mutex lock root is outside the synced vault path."""
    try:
        lock_root = _get_vault_lock_root()
    except ImportError:
        return (None, "skipped — vault_lock not importable (agentm scripts not on PYTHONPATH)")

    lock_root_str = lock_root.resolve().as_posix()

    for marker in _SYNC_MARKERS:
        if marker in lock_root_str:
            return (
                False,
                f"Lock root {lock_root_str!r} is inside a synced/cloud path "
                f"(contains {marker!r}). Move it to a local path such as "
                "~/.cache/agentm/locks by setting XDG_CACHE_HOME to a local "
                "directory — see vault_lock.vault_mutex() docs.",
            )
    return (True, f"lock root {lock_root_str!r} is outside any known sync path")


# ── runner ────────────────────────────────────────────────────────────────────

_ALL_CHECKS = ("liveness", "token_env", "origin_guard", "index_root_safe")
_DEFAULT_CHECKS = ("liveness", "token_env")


def run_checks(
    checks: tuple = _DEFAULT_CHECKS,
    url: str = _DEFAULT_URL,
) -> list:
    """Run the named checks. Returns list of (name, passed, message)."""
    results = []
    for name in checks:
        if name == "liveness":
            passed, msg = check_liveness(url)
        elif name == "token_env":
            passed, msg = check_token_env()
        elif name == "origin_guard":
            passed, msg = check_origin_guard(url)
        elif name == "index_root_safe":
            passed, msg = check_index_root_safe()
        else:
            passed, msg = (None, f"unknown check {name!r}")
        results.append((name, passed, msg))
    return results


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="memory_mcp_doctor",
        description="agentm memory MCP server — doctor health checks",
    )
    parser.add_argument(
        "--all", "--live",
        dest="all_checks",
        action="store_true",
        help="Run all four checks (including origin_guard which requires a live daemon).",
    )
    parser.add_argument(
        "--check",
        choices=_ALL_CHECKS,
        help="Run a single named check.",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("AGENTM_MCP_URL", _DEFAULT_URL),
        help=f"Daemon base URL (default: {_DEFAULT_URL}; override: AGENTM_MCP_URL env).",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    if args.check:
        checks = (args.check,)
    elif args.all_checks:
        checks = _ALL_CHECKS
    else:
        checks = _DEFAULT_CHECKS

    results = run_checks(checks=checks, url=args.url.rstrip("/"))

    any_failed = False
    for name, passed, msg in results:
        if passed is True:
            label = "[OK]  "
        elif passed is False:
            label = "[FAIL]"
            any_failed = True
        else:
            label = "[SKIP]"
        print(f"  memory-server {label} {name}: {msg}")

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
