#!/usr/bin/env bash
# resolve-python.sh — print the Python interpreter the memory hooks should run.
#
# THE CANONICAL RESOLVER. One implementation, sourced by nothing and executed
# by everything: the four memory hooks call it through their own
# `_resolve_agentm_python()` bootstrap, and `scripts/machinery_doctor.py`'s
# `memory-hook-interpreter` check delegates to this same script rather than
# re-deriving the probe. A doctor that re-implemented the candidate list could
# report a healthy interpreter the hooks never actually pick; delegating means
# the check cannot drift from the behavior it is checking.
#
# WHY THIS EXISTS. The hooks used to end in a bare `exec python3 …`, which
# PATH-resolves to /usr/bin/python3 on a stock macOS box — Apple's system
# Python, a deliberately minimal build. That was found in 2026-08-02 as the
# cause of a silent recall outage: the vector index could not load its native
# sqlite extension there, and every caller read the failure as the graceful
# "index not built yet" skip, so nothing ever went red.
#
# That index has since been removed (see wiki/designs/agentm-rescope-week1-
# experiment.md), so the specific dependency is gone. The resolver stays, and
# so does its probe, because the probe was never really about one extension:
# an interpreter whose sqlite3 was built with loadable-extension support is a
# real Python install, and Apple's stub is the one that isn't. Every memory
# hook runs on whatever this prints, so picking the real install still matters.
# The probe is kept as-is rather than loosened — changing what it selects would
# be a behavior change with nothing behind it.
#
# RESOLUTION ORDER.
#   1. $AGENTM_PYTHON          — explicit operator override, honored as given
#   2. $AGENT_TOOLKIT_PYTHON   — back-compat alias for the same knob
#   3. first candidate whose sqlite3 build supports extension loading — the
#      proxy for "a real Python install" (see WHY THIS EXISTS above)
#   4. bare `python3` — today's behavior, the floor
#
# An explicit override wins outright whenever it is executable. It is not
# re-probed and not silently replaced: an operator who names an interpreter
# gets that interpreter, and if it happens to be incapable the doctor check
# says so and names the override as the cause. Silently overriding an override
# would make that diagnosis impossible.
#
# WHY THE CANDIDATE LIST LOOKS LIKE THIS. Homebrew installs only a *versioned*
# `python3.13` — there is no bare `python3` symlink in its bin dir — so a list
# of bare-name paths (the shape this resolver replaces) missed it entirely and
# fell through to the incapable floor. Versioned names are probed via PATH
# first, which finds a Homebrew/pyenv/system interpreter wherever the operator
# actually put it; the absolute prefixes afterwards are a backstop for a
# non-login shell whose PATH is thinner than the operator's. Nothing here is
# hardcoded to one machine's layout.
#
# COST. One process spawn per probed candidate; a candidate that isn't on PATH
# costs nothing (no spawn). Measured 2026-08-02 on an M-series Mac: ~15 ms per
# spawn, and the first capable candidate returns immediately — ~40 ms end to end
# on a box whose bare `python3` is incapable (bash startup, one failed probe,
# one successful one), ~25 ms where it is capable. That matters because this
# runs inside the UserPromptSubmit hook's 300 ms budget. The probe deliberately
# imports nothing beyond `sqlite3`: adding an `import sqlite_vec` to it measured
# ~28 ms more per capable candidate, which buys a tie-break this resolver
# doesn't need (see below) at a tenth of the interactive budget.
# Re-measure before trusting these figures; the memory-system design's
# 2026-08-02 amendment exists because a cost comment was trusted for a year.
# Re-audit trigger: if a warm-embedder path lands and per-prompt recall starts
# spending its budget on real retrieval, re-measure this and consider caching
# the resolution — at that point ~40 ms stops being free.
#
# Always prints exactly one line and always exits 0 — a resolver that failed
# would turn a graceful degradation into a broken hook.

set -uo pipefail

# Probe one interpreter: does its sqlite3 build support extension loading?
#
# This asks about the *build*, deliberately, and not about whether `sqlite_vec`
# is importable. `enable_load_extension` is fixed at compile time and cannot be
# added to an interpreter in place; a missing `sqlite_vec` is one `pip install`
# away. So a build-capable interpreter is the right pick even when the package
# is absent — it is precisely the one where installing the package will help —
# and the doctor check reports a missing package separately, against the
# interpreter this resolver actually chose. Selecting on the package instead
# would let a fixable gap veto the only viable interpreter on the box.
_probe() {
    "$1" -c 'import sqlite3, sys; sys.exit(0 if hasattr(sqlite3.Connection, "enable_load_extension") else 1)' \
        >/dev/null 2>&1
}

# An explicit override is authoritative when it is runnable at all.
for _override in "${AGENTM_PYTHON:-}" "${AGENT_TOOLKIT_PYTHON:-}"; do
    if [[ -n "$_override" ]] && command -v "$_override" >/dev/null 2>&1; then
        printf '%s\n' "$_override"
        exit 0
    fi
done

_candidates=()
# PATH-relative first — respects wherever the operator actually installed
# Python. Bare `python3` leads so an already-capable box keeps today's exact
# behavior at the cost of a single probe.
_candidates+=(python3)
for _minor in 14 13 12 11 10 9; do
    _candidates+=("python3.$_minor")
done
# Absolute backstops for a non-login shell with a thin PATH. Both bare and
# versioned names, since Homebrew ships only the latter.
for _prefix in /opt/homebrew/bin /usr/local/bin /opt/local/bin "${HOME:-}/.pyenv/shims" /usr/bin; do
    [[ -n "$_prefix" ]] || continue
    _candidates+=("$_prefix/python3")
    for _minor in 14 13 12 11 10 9; do
        _candidates+=("$_prefix/python3.$_minor")
    done
done

# First capable candidate wins.
for _c in "${_candidates[@]}"; do
    command -v "$_c" >/dev/null 2>&1 || continue
    if _probe "$_c"; then
        printf '%s\n' "$_c"
        exit 0
    fi
done

# Floor: today's behavior — never worse than what the hooks did before this
# resolver existed.
printf '%s\n' "python3"
exit 0
