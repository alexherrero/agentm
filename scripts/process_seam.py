#!/usr/bin/env python3
"""process_seam — the memory↔process client seam (V5-4).

A small, **read-only**, **graceful-no-op** view that a *process* (the crickets
developer-workflows phases today; the V5-9 MCP server + plugins tomorrow) calls
instead of reaching into the memory engine's internals. It exports three
functions over the DC-7-frozen public memory API:

    recall_here(context, *, query=None, limit=None) -> str
    offer_save_here(context, candidate)             -> list[dict]
    state_path(context, which)                      -> Path

Design contract (parent design `v5-4-process-seam`, Locked design calls):

- **[LC-1] The module API is the contract.** A thin ``python -m`` entrypoint
  (``main`` below) covers non-Python shell callers, but the designed surface is
  these importable functions.
- **[LC-2] ``offer_save_here`` is advisory-only.** It computes + returns save
  *candidates*; it never persists. The write stays on the existing ``/memory
  save`` path (``harness_memory.offer_save`` / the ``offer-save`` CLI verb). This
  module deliberately does **not** import or call any write path — that is what
  makes "the seam is read-only" literally true and gives the read-only test
  something concrete to assert.
- **[LC-3] ``state_path`` degrades to repo-local ``<project_root>/.harness/``**
  when no vault/memory is configured — never ``None``.
- **[LC-4] Memory never imports the process.** This module imports the engine
  (``harness_memory``); nothing in the engine imports it back. The one-way edge
  is enforced by ``check-process-seam-import-direction.sh``.

Frozen-API anchoring: every call routes through ``harness_memory``'s *public*
surface (``resolve_project`` / ``phase_recall`` / ``resolve_active_plan`` /
``harness_state_dir`` / ``is_available``). It never touches engine internals and
never widens the engine's surface — a consumer that needs something the frozen
API lacks is a separate engine change, not a seam widening.

The shared ``context`` dict (all three functions):

    {"cwd": <project root>,   # optional; defaults to the process cwd
     "phase": <dev-loop phase>,  # optional; recall_here only, default "work"
     "plan": <named-plan slug>}  # optional; state_path only (named-plan awareness)

Run directly for the shell shim:

    python3 scripts/process_seam.py recall-here --phase work
    python3 scripts/process_seam.py state-path plan
    python3 scripts/process_seam.py offer-save-here --kind decision --slug foo --body-file -
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as _hm  # noqa: E402

# The dev-loop phase a context-oriented recall defaults to when the caller does
# not name one. "work" is the loop's primary phase and the seam's main caller;
# an unknown/absent phase degrades here rather than raising (graceful-no-op).
_DEFAULT_PHASE = "work"

# Which `which` tokens state_path() accepts, mapped to the resolved (plan,
# progress) pair index from `resolve_active_plan`.
_STATE_WHICH = ("plan", "progress")


def _project_root(context: Optional[dict]) -> Path:
    """The context's project root (``cwd`` key), defaulting to the process cwd."""
    ctx = context or {}
    return Path(ctx.get("cwd", Path.cwd()))


def recall_here(
    context: Optional[dict],
    *,
    query: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Recall memory context relevant to the current working context.

    Resolves the context (cwd → project slug), then calls the engine's public
    ``recall`` (``phase_recall``) scoped to that project. Returns the budgeted
    markdown recall summary.

    Args:
        context: the shared context dict; ``cwd`` selects the project root,
            ``phase`` (one of the dev-loop phases) selects the recall scope
            (default ``"work"``).
        query: **reserved / forward-compat.** The frozen public recall is a
            phase+project read with no free-text/semantic query; that capability
            arrives with the V6 vector index. Accepted today so callers can be
            written against the final signature, but it is a no-op until the
            engine gains semantic recall — passing it neither errors nor filters.
        limit: recall budget in tokens (maps to ``phase_recall``'s ``budget``);
            ``None`` uses the phase default.

    Read-only — never triggers reflect/evolve, never writes.

    No-op degrade: no engine or no vault configured → ``""`` (empty string),
    never an exception.
    """
    ctx = context or {}
    phase = ctx.get("phase") or _DEFAULT_PHASE
    if phase not in _hm._VALID_PHASES:
        # Never raise on an unknown phase — degrade to the default scope.
        phase = _DEFAULT_PHASE

    # `query` is intentionally unused (reserved; see docstring).
    _ = query

    resolution = _hm.resolve_project(ctx)
    slug = resolution.get("slug")

    # `phase_recall` is itself graceful (returns "" when the vault is absent);
    # the explicit guard keeps the degrade contract local + testable.
    if not _hm.is_available():
        return ""
    return _hm.phase_recall(phase, slug, budget=limit)


def offer_save_here(context: Optional[dict], candidate: Any) -> list[dict]:
    """Surface what *could* be saved from this context — without saving it.

    Advisory-only ([LC-2]): returns the candidate enriched with the resolved
    save target (project slug + vault dir) so the caller can invoke the existing
    ``/memory save`` path. **This function never persists** — it imports and
    calls no write path. Save-worthiness is the caller's (or the engine's
    reflection's) call, deliberately not judged here.

    Args:
        context: the shared context dict (``cwd`` selects the project root;
            ``phase`` is passed through onto the candidate if present).
        candidate: the caller's proposed save — a dict shaped like the
            ``offer-save`` verb's inputs (e.g. ``{"kind", "slug", "body",
            "confidence"?, "confidence_reason"?}``). Passed through, not
            validated: the seam stays minimal and lets the caller decide.

    Returns:
        ``[enriched_candidate]`` when memory is available and a project resolves;
        ``[]`` (nothing to offer) when memory/vault is absent, no project
        resolves, or ``candidate`` is empty.
    """
    if not candidate:
        return []
    resolution = _hm.resolve_project(context or {})
    slug = resolution.get("slug")
    if not _hm.is_available() or slug is None:
        return []

    enriched = dict(candidate) if isinstance(candidate, dict) else {"body": candidate}
    enriched.setdefault("project", slug)
    phase = (context or {}).get("phase")
    if phase is not None:
        enriched.setdefault("phase", phase)
    vault_target = resolution.get("vault_path")
    enriched["target"] = str(vault_target) if vault_target else None
    return [enriched]


def state_path(context: Optional[dict], which: str) -> Path:
    """Resolve the harness state path for ``which`` in the current context.

    Wraps ``resolve_project`` + ``resolve_active_plan`` (so V5-10 named-plan
    awareness comes for free) + ``harness_state_dir``. Vault-backed when memory
    is present; **degrades to repo-local ``<project_root>/.harness/<file>``**
    ([LC-3]) when not — never ``None``.

    Args:
        context: the shared context dict; ``cwd`` selects the project root,
            ``plan`` (optional) names a plan (``"foo"`` → ``PLAN-foo.md`` /
            ``progress-foo.md``) via ``resolve_active_plan``'s explicit-arg path.
        which: ``"plan"`` or ``"progress"`` — which file of the active pair.

    Returns:
        The resolved ``Path`` (vault ``_harness/`` or repo-local ``.harness/``).

    Raises:
        ValueError: if ``which`` is not ``"plan"``/``"progress"`` — a caller bug,
            distinct from the absent-memory degrade (which never raises).
        harness_memory.ActivePlanError / ValueError: propagated, **not**
            swallowed, when a present ``.harness/active-plan`` marker is dangling
            or names an unsafe slug. A corrupt/unsafe marker is a loud-fail
            safety property (V5-10 Risk #7), not the absent-memory degrade —
            silently degrading there could mis-bind the worker to another plan.
    """
    if which not in _STATE_WHICH:
        raise ValueError(
            f"state_path: which must be one of {_STATE_WHICH!r}, got {which!r}"
        )
    ctx = context or {}
    resolution = _hm.resolve_project(ctx)
    plan_name, progress_name = _hm.resolve_active_plan(
        resolution, plan_arg=ctx.get("plan")
    )
    filename = plan_name if which == "plan" else progress_name

    directory = _hm.harness_state_dir(resolution)
    if directory is None:
        # Vault-mode but no vault configured → repo-local degrade ([LC-3]).
        directory = _project_root(ctx) / ".harness"
    return directory / filename


# -----------------------------------------------------------------------------
# Thin shell entrypoint ([LC-1]) — the contract is the module API above; this
# shim lets non-Python hosts shell out to the same functions. Always exits 0 on
# the graceful-no-op paths so a process never wedges on a memory-absent seam.
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="process_seam",
        description=(
            "Read-only, graceful-no-op memory↔process client seam (V5-4). "
            "Shells out to the same recall_here / offer_save_here / state_path "
            "functions the in-process Python API exposes."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_recall = sub.add_parser("recall-here", help="emit recall context for the cwd's project")
    p_recall.add_argument("--cwd", default=None, help="project root (default: cwd)")
    p_recall.add_argument("--phase", default=None, help=f"dev-loop phase (default: {_DEFAULT_PHASE})")
    p_recall.add_argument("--query", default=None, help="reserved / forward-compat; no-op today")
    p_recall.add_argument("--limit", type=int, default=None, help="recall budget in tokens")

    p_state = sub.add_parser("state-path", help="resolve the active PLAN/progress path")
    p_state.add_argument("which", choices=list(_STATE_WHICH))
    p_state.add_argument("--cwd", default=None, help="project root (default: cwd)")
    p_state.add_argument("--plan", default=None, help="named-plan slug (e.g. 'foo' → PLAN-foo.md)")

    p_offer = sub.add_parser("offer-save-here", help="emit advisory save candidate(s) as JSON")
    p_offer.add_argument("--cwd", default=None, help="project root (default: cwd)")
    p_offer.add_argument("--phase", default=None, help="dev-loop phase (passed through)")
    p_offer.add_argument("--kind", required=True, help="entry kind (e.g. decision)")
    p_offer.add_argument("--slug", required=True, help="entry slug")
    p_offer.add_argument(
        "--body-file", default="-",
        help="path to the candidate body, or '-' for stdin (default)",
    )
    p_offer.add_argument("--confidence", type=float, default=None)
    p_offer.add_argument("--confidence-reason", default=None)
    return parser


def _read_body(body_file: str) -> str:
    if body_file == "-":
        return sys.stdin.read()
    return Path(body_file).read_text(encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.cmd == "recall-here":
        context = {"cwd": args.cwd} if args.cwd else {}
        if args.phase:
            context["phase"] = args.phase
        out = recall_here(context, query=args.query, limit=args.limit)
        if out:
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
        return 0

    if args.cmd == "state-path":
        context = {"cwd": args.cwd} if args.cwd else {}
        if args.plan:
            context["plan"] = args.plan
        print(state_path(context, args.which))
        return 0

    if args.cmd == "offer-save-here":
        context = {"cwd": args.cwd} if args.cwd else {}
        if args.phase:
            context["phase"] = args.phase
        candidate = {
            "kind": args.kind,
            "slug": args.slug,
            "body": _read_body(args.body_file),
        }
        if args.confidence is not None:
            candidate["confidence"] = args.confidence
        if args.confidence_reason is not None:
            candidate["confidence_reason"] = args.confidence_reason
        print(json.dumps(offer_save_here(context, candidate), indent=2))
        return 0

    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(main())
