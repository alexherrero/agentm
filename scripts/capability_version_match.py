#!/usr/bin/env python3
"""capability_version_match — single semver-range check for the capability resolver.

Implements Task 2 of V5-8: a one-range, one-provider comparison that answers
"does the installed version satisfy the declared range?" Stdlib-only; never
imports plugin code; returns False on any malformed input (LC-4 graceful degrade).

Public API:

    satisfies(installed_version, range_str) -> bool
        True iff installed_version is within the range expressed by range_str.
        Returns False when either argument is None/empty, when the range cannot
        be parsed, or when the installed version cannot be parsed.

Range format (subset of PEP 440 specifiers):
    ">= 1.2"   — installed >= 1.2.0
    "> 1.0"    — installed > 1.0.0
    "<= 2.0"   — installed <= 2.0.0
    "< 3"      — installed < 3.0.0
    "== 1.2.3" — installed == 1.2.3
    "~= 1.2"   — compatible release: installed >= 1.2 AND < 2  (drops last component)

Design constraints:
- LC-3  Single range check, never a solver (enhances ∩ requires = ∅).
- LC-4  Malformed / absent → False, never raise.
"""
from __future__ import annotations

import re
from typing import Sequence

# ── version parsing ───────────────────────────────────────────────────────────

_VER_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?\s*$")
_OP_RE = re.compile(r"^\s*(~=|==|!=|<=|>=|<|>)\s*(.+)$")


def _parse_version(v: str) -> tuple[int, ...] | None:
    """Parse a version string into a comparable tuple of ints (unpadded).

    Returns None on parse failure. Callers pad to match depths for comparison.
    """
    v = v.strip()
    m = _VER_RE.match(v)
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def _pad(t: tuple[int, ...], length: int) -> tuple[int, ...]:
    while len(t) < length:
        t += (0,)  # type: ignore[assignment]
    return t


def _parse_range(range_str: str) -> tuple[str, tuple[int, ...]] | None:
    """Parse an operator + version from a range string.

    Returns (operator, version_tuple_unpadded) or None on parse failure.
    Rejects compound specifiers (e.g. ">= 1.0, < 2.0") because the version
    part fails _VER_RE's end-anchor after the comma.
    """
    m = _OP_RE.match(range_str.strip())
    if not m:
        return None
    op, ver_str = m.group(1), m.group(2)
    ver = _parse_version(ver_str)
    if ver is None:
        return None
    return op, ver


# ── public API ────────────────────────────────────────────────────────────────

def satisfies(installed_version: str | None, range_str: str) -> bool:
    """Return True iff `installed_version` satisfies `range_str`.

    Both arguments must be non-empty strings. Returns False on any parse
    failure or None input — never raises (LC-4).
    """
    try:
        if not installed_version or not range_str:
            return False

        installed = _parse_version(installed_version)
        if installed is None:
            return False

        parsed = _parse_range(range_str)
        if parsed is None:
            return False

        op, req = parsed

        if op == "~=":
            # Compatible release: >= req AND < (req-with-last-dropped incremented).
            # ~= 1.2   → >= 1.2 AND < 2     (drop .2, increment 1 → 2)
            # ~= 1.2.3 → >= 1.2.3 AND < 1.3 (drop .3, increment .2 → .3)
            # Single-component (~= 1) is invalid per PEP 440.
            if len(req) < 2:
                return False
            depth = len(req)
            inst_p = _pad(installed, depth)
            req_p = _pad(req, depth)
            if inst_p < req_p:
                return False
            # Upper bound: drop last component, increment the preceding one.
            upper = req[:-1][:-1] + (req[:-1][-1] + 1,)
            upper_p = _pad(upper, depth)
            return inst_p < upper_p

        # Pad both to the same length for comparison.
        depth = max(len(installed), len(req))
        installed = _pad(installed, depth)
        req = _pad(req, depth)

        if op == ">=":
            return installed >= req
        if op == ">":
            return installed > req
        if op == "<=":
            return installed <= req
        if op == "<":
            return installed < req
        if op == "==":
            return installed == req
        if op == "!=":
            return installed != req

        return False  # unknown operator (should not happen given _OP_RE)
    except Exception:
        return False
