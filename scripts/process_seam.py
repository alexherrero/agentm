#!/usr/bin/env python3
"""process_seam — the memory↔process client seam (V5-4).

A small, **read-only**, **graceful-no-op** view that a *process* (the crickets
development-lifecycle phases today; the V5-9 MCP server + plugins tomorrow) calls
instead of reaching into the memory engine's internals. It exports three
functions over the DC-7-frozen public memory API:

    offer_save_here(context, candidate)             -> list[dict]
    state_path(context, which)                      -> Path
    project_path(context, which)                    -> Path

R0.9 (agentmEngine#2): a third function, recall_here, was retired — it
delegated to harness_memory.phase_recall(), which has returned "" for every
call since the V5-3 vault-backend removal. No live crickets consumer called
it (crickets' documenter sub-agent uses harness_memory.py's own
documenter-context CLI verb instead, a separate live surface this retirement
does not touch).

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
surface (``resolve_project`` / ``resolve_active_plan`` / ``active_plan_paths`` /
``project_state_root`` / ``is_available``). It never touches engine internals and never widens the
engine's surface — a consumer that needs something the frozen API lacks is a
separate engine change, not a seam widening.

The shared ``context`` dict (both functions):

    {"cwd": <project root>,   # optional; defaults to the process cwd
     "project": <project slug>,  # optional; project_path only, a project with no checkout
     "phase": <dev-loop phase>,  # optional; offer_save_here only, passed through
     "plan": <named-plan slug>}  # optional; state_path only (named-plan awareness)

Run directly for the shell shim:

    python3 scripts/process_seam.py state-path plan
    python3 scripts/process_seam.py project-path desk [--cwd ROOT | --project SLUG]
    python3 scripts/process_seam.py offer-save-here --kind decision --slug foo --body-file -

``project-path`` names a project's ``tasks/``, ``designs/`` or ``desk/`` and
creates nothing: exit 0 with the path whether or not it exists yet, 1 when the
project has no vault home (the reason on stderr), 2 on a usage error or an
unsafe slug (agentm-vault, resolve-the-project-homes). The rest of this
paragraph is ``state-path``'s.

The shim's exit codes: 0 resolved, 2 a caller bug or a loud refusal the engine
raised, and **4 name the task** — a bare ``state-path`` on a project that keeps
its plans in numbered tasks, which has no singleton to answer with. Exit 4 puts
nothing on stdout and one line on stderr; it is the code the crickets
development-lifecycle release handles (agentm-vault plan 10, task 7(b)). It is
not the [LC-3] degrade: [LC-3] is about a project with no vault, which still has
a repo-local ``.harness/`` to name, and still exits 0.
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

# Which `which` tokens state_path() accepts: the resolved (plan, progress) pair
# from `resolve_active_plan`, and the tracker it carries beside them.
_STATE_WHICH = ("plan", "progress", "tracker")

# The homes `project_path` names: a project's tasks, its designs, and the desk
# that holds its machine files (agentm-vault § Projects and tasks).
_PROJECT_HOMES = ("tasks", "designs", "desk")


class NoProjectHome(LookupError):
    """The project has no vault home to name: no project is bound to the
    checkout, no synced storage backend is active, or the project or the
    device has opted into local state mode. The shim answers exit 1."""


def _project_root(context: Optional[dict]) -> Path:
    """The context's project root (``cwd`` key), defaulting to the process cwd."""
    ctx = context or {}
    return Path(ctx.get("cwd", Path.cwd()))


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
    project_locator = resolution.get("project_locator")
    enriched["target"] = project_locator.key if project_locator is not None else None
    return [enriched]


def state_path(context: Optional[dict], which: str) -> Path:
    """Resolve the harness state path for ``which`` in the current context.

    Wraps ``resolve_project`` + ``active_plan_paths`` (so V5-10 named-plan
    awareness comes for free). On a synced backend the answer is a task's file,
    ``tasks/<name>/…`` in the project's vault directory; with no vault it
    **degrades to repo-local ``<project_root>/.harness/<file>``** ([LC-3]) —
    never ``None``.

    Args:
        context: the shared context dict; ``cwd`` selects the project root,
            ``plan`` (optional) names a plan or task (``"foo"`` finds
            ``tasks/NNN-foo/``, or ``PLAN-foo.md`` with no vault) via
            ``resolve_active_plan``'s explicit-arg path.
        which: ``"plan"``, ``"progress"`` or ``"tracker"`` — which file of the
            active plan. The tracker sits beside the plan: ``tracker.md`` in a
            task directory, ``tracker-foo.md`` beside a repo-local pair.

    Returns:
        The resolved ``Path`` (a vault ``tasks/<name>/`` file, or repo-local
        ``.harness/``).

    Raises:
        ValueError: if ``which`` is not one of those three — a caller bug,
            distinct from the absent-memory degrade (which never raises).
        harness_memory.ActivePlanError / ValueError: propagated, **not**
            swallowed, when a present ``.harness/active-plan`` marker is dangling
            or names an unsafe slug. A corrupt/unsafe marker is a loud-fail
            safety property (V5-10 Risk #7), not the absent-memory degrade —
            silently degrading there could mis-bind the worker to another plan.
        harness_memory.TaskNameRequired: propagated when ``context`` names no
            plan and the project keeps its plans in numbered tasks — it has no
            singleton, so there is no path to return. The shim answers exit 4;
            an in-process caller catches it and asks which task.
    """
    if which not in _STATE_WHICH:
        raise ValueError(
            f"state_path: which must be one of {_STATE_WHICH!r}, got {which!r}"
        )
    ctx = context or {}
    resolution = _hm.resolve_project(ctx)
    paths = _hm.active_plan_paths(resolution, plan_arg=ctx.get("plan"))
    index = _STATE_WHICH.index(which)
    if paths is None:
        # No project root in the resolution → repo-local degrade ([LC-3]).
        active = _hm.resolve_active_plan(resolution, plan_arg=ctx.get("plan"))
        return _project_root(ctx) / ".harness" / (active[0], active[1], active.tracker)[index]
    return paths[index]


def project_path(context: Optional[dict], which: str) -> Path:
    """Name one of a project's homes: its ``tasks/``, ``designs/`` or ``desk/``.

    The project is the one bound to ``context["cwd"]`` through its
    ``.harness/project.json``, or the one ``context["project"]`` names by slug
    for a project with no repo checkout. The answer is composed from the
    project directory the resolver already names (``project_state_root``), so
    it is the same on the vault-root and memory-root layouts, and it is
    returned whether or not the directory exists yet. Nothing is created.

    Raises:
        ValueError: ``which`` is not a home, or the slug is not a single path
            component — a caller bug or a refusal, never a degrade.
        NoProjectHome: no project is bound, or the project has no vault home
            (no synced backend, or a local opt-out). There is no [LC-3] degrade
            here: a repo-local ``.harness/`` holds no tasks, designs or desk.
    """
    if which not in _PROJECT_HOMES:
        raise ValueError(
            f"project_path: which must be one of {_PROJECT_HOMES!r}, got {which!r}"
        )
    ctx = context or {}
    resolution = _hm.resolve_project(ctx)
    slug = resolution.get("slug")
    if slug is None:
        raise NoProjectHome(
            f"no project is bound to {_project_root(ctx)}: its .harness/project.json "
            f"names no vault_project. Pass --project SLUG to name one."
        )
    root = _hm.project_state_root(resolution)
    if root is None:
        raise NoProjectHome(
            f"{slug} has no vault home: no synced storage backend is active, or "
            f"the project or this device is in local state mode."
        )
    return root / which


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
            "Shells out to the same offer_save_here / state_path "
            "functions the in-process Python API exposes."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_state = sub.add_parser("state-path", help="resolve the active plan, progress or tracker path")
    p_state.add_argument("which", choices=list(_STATE_WHICH))
    p_state.add_argument("--cwd", default=None, help="project root (default: cwd)")
    p_state.add_argument("--plan", default=None, help="named-plan slug (e.g. 'foo' → PLAN-foo.md)")

    p_home = sub.add_parser(
        "project-path",
        help="name a project's tasks, designs or desk directory (creates nothing)",
    )
    p_home.add_argument("which", choices=list(_PROJECT_HOMES))
    home_where = p_home.add_mutually_exclusive_group()
    home_where.add_argument("--cwd", default=None, help="project root (default: cwd)")
    home_where.add_argument(
        "--project", default=None, metavar="SLUG",
        help="a project named by slug, for one with no repo checkout",
    )

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

    if args.cmd == "state-path":
        context = {"cwd": args.cwd} if args.cwd else {}
        if args.plan:
            context["plan"] = args.plan
        try:
            resolved = state_path(context, args.which)
        except _hm.TaskNameRequired as exc:
            # Exit 4, nothing on stdout: a bare call on a project that keeps its
            # plans in numbered tasks has no singleton to answer with. The
            # crickets development-lifecycle release handles exactly this code
            # and asks which task (agentm-vault plan 10, task 7(b)).
            print(f"[process_seam] {exc}", file=sys.stderr)
            return 4
        print(resolved)
        return 0

    if args.cmd == "project-path":
        # Exit 0 with the path (whether or not it exists), 1 when the project has
        # no vault home (the reason on stderr), 2 on a usage error or an unsafe
        # slug — the contract crickets' project_homes.py was written against.
        if args.project is not None:
            context = {"project": args.project}
        else:
            context = {"cwd": args.cwd} if args.cwd else {}
        try:
            resolved = project_path(context, args.which)
        except ValueError as exc:
            print(f"[process_seam] {exc}", file=sys.stderr)
            return 2
        except NoProjectHome as exc:
            print(f"[process_seam] {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 — a configured backend that will not load
            # is no home to name, said loudly; never a quiet device-local answer.
            print(f"[process_seam] no vault home: the storage backend did not resolve: {exc}",
                  file=sys.stderr)
            return 1
        print(resolved)
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
