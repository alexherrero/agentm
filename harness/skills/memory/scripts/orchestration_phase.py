#!/usr/bin/env python3
"""orchestration_phase.py — phase-integration auto-dispatch (V4 #23 task 5).

Wires the memory skills into the harness phase boundaries, EXTENDING (not
duplicating) the V4 #8 auto-context dispatcher (`harness_memory.py`, which owns
recall / offer-save / plan-done-promotion). Two operator-gated, cooldown-gated,
config-toggleable dispatches the phase specs call at their tail:

  post-work     After `/work` commits a task, reflect the just-finished session
                (`reflect.py <transcript> --summary --route`). DEDUP-GUARDED
                against the `memory-reflect-stop` hook: it cooperates via the
                per-session `.reflected` marker so the same transcript is never
                reflected twice (a second --route errors on a HIGH-save slug
                collision). On Claude Code the Stop hook is the fallback; on a
                host WITHOUT a Stop hook (Antigravity) this is the only
                task-completion reflect — that's the cross-host win. (V4 #23
                DC-1 / Option A, operator call 2026-06-01.)

  post-release  After `/release`, refresh the skill surfaces to align with what
                shipped: `index_skills.py` (local skill index) + `discover_skills.py
                --cadence-check` (external sources, self-throttled so a release
                never blocks on a full network fetch).

Both mirror the task-2 gate pattern used by the briefing (task 3) + idle chain
(task 4): `enable_phase_integration` toggle → `phase_reflect_cooldown_hours`
cooldown via `auto_orchestration.should_fire` → run → `record_fire` + `save_state`.
post-work uses chain `phase_reflect`; post-release uses chain `phase_release`
(separate last-fire timestamps, same cooldown value) so a reflect doesn't block a
release refresh. `--dry-run` returns the resolved plan without executing or
touching state. Never raises (phase-spec-invoked; must not wedge a phase).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# sibling imports (same scripts dir)
import auto_orchestration as ao
import crystallize

_REFLECT_CHAIN = "phase_reflect"
_RELEASE_CHAIN = "phase_release"

_STEP_TIMEOUT_SEC = {
    "reflect": 120,
    "index-skills": 60,
    "discover-skills": 90,
}


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent


def _script(name: str) -> str:
    return str(_scripts_dir() / name)


def _default_runner(name: str, argv: list[str]) -> dict:
    """Run one dispatch step as a subprocess; never raises."""
    try:
        proc = subprocess.run(
            [sys.executable, *argv],
            capture_output=True,
            text=True,
            timeout=_STEP_TIMEOUT_SEC.get(name, 90),
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": "timeout", "timed_out": True}
    except Exception as e:
        return {"returncode": 1, "stdout": "", "stderr": str(e), "timed_out": False}


def _cooldown_hours(config: dict) -> float:
    return float(config.get("phase_reflect_cooldown_hours", 0) or 0)


# ── post-work reflect ───────────────────────────────────────────────────────
def _resolve_session_marker(harness_dir: Path) -> tuple[Path | None, str | None]:
    """Resolve the current session's `.start` marker — concurrency-safely.

    Returns (marker, reason). A marker is only returned when EXACTLY ONE
    `session-id-*.start` marker exists (an unambiguous single active session,
    the normal case — one active session per repo). Otherwise we refuse to
    guess and defer to the session-exact `memory-reflect-stop` hook:
      - 0 markers           → (None, "no-session")
      - ≥2 markers          → (None, "ambiguous-session")

    Why no mtime tie-break: a newest-mtime guess can pick the WRONG transcript
    when two sessions are active in one repo (concurrent agents) or an active
    session sits beside a not-yet-swept crashed-session orphan — reflecting the
    wrong session AND burning the shared `phase_reflect` cooldown for it. The
    Stop hook keys on the exact session_id from its event payload, so deferring
    to it is always correct; a caller that knows the session id can pass it
    explicitly (`session_id=`) to target precisely."""
    if not harness_dir.is_dir():
        return None, "no-session"
    try:
        markers = [p for p in harness_dir.glob("session-id-*.start") if p.is_file()]
    except OSError:
        return None, "no-session"
    if not markers:
        return None, "no-session"
    if len(markers) > 1:
        return None, "ambiguous-session"
    return markers[0], None


def _marker_transcript(marker: Path) -> str | None:
    try:
        for line in marker.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("transcript:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def post_work_reflect(
    vault: Path,
    project_root: Path,
    config: dict | None = None,
    now: datetime | None = None,
    *,
    transcript: str | None = None,
    session_id: str | None = None,
    dry_run: bool = False,
    runner=None,
) -> dict:
    """Reflect the just-finished `/work` session (dedup-guarded vs the Stop hook).
    Returns a result dict; never raises. Status: disabled | dry-run | cooldown |
    no-session | ambiguous-session | already-reflected | reflected |
    reflect-failed | error.

    Target resolution (most → least precise): explicit `transcript=` → explicit
    `session_id=` (`session-id-<sid>.start`) → the single unambiguous `.start`
    marker in `<project_root>/.harness/` (else defer to the Stop hook)."""
    vault = Path(vault)
    project_root = Path(project_root)
    if now is None:
        now = datetime.now(timezone.utc)
    if runner is None:
        runner = _default_runner
    result: dict = {"dispatch": "post-work", "status": None, "dry_run": dry_run}
    try:
        if config is None:
            config = ao.load_config(vault)
        if not config.get("enable_phase_integration", True):
            result["status"] = "disabled"
            return result

        harness_dir = project_root / ".harness"
        # Resolve the target transcript + the session marker (for dedup/rename).
        marker = None
        if transcript:
            tpath = transcript
        elif session_id:
            # Session-exact: target this session's marker precisely.
            m = harness_dir / f"session-id-{session_id}.start"
            if (harness_dir / f"session-id-{session_id}.reflected").exists():
                result["status"] = "already-reflected"
                return result
            if not m.is_file():
                result["status"] = "no-session"
                return result
            marker = m
            tpath = _marker_transcript(m)
        else:
            marker, reason = _resolve_session_marker(harness_dir)
            if marker is None:
                result["status"] = reason or "no-session"
                return result
            tpath = _marker_transcript(marker)

        if not tpath:
            result["status"] = "no-session"
            return result
        result["transcript"] = tpath

        # Dedup: a `.reflected` sibling means the Stop hook (or a prior dispatch)
        # already reflected this session — skip to avoid a double --route.
        reflected_marker = marker.with_suffix(".reflected") if marker else None
        if reflected_marker is not None and reflected_marker.exists():
            result["status"] = "already-reflected"
            return result

        state = ao.load_state(vault)
        cooldown_ok = ao.should_fire(state, _REFLECT_CHAIN, now, _cooldown_hours(config))
        argv = [_script("reflect.py"), tpath, "--summary", "--route", "--vault-path", str(vault)]

        if dry_run:
            result["status"] = "dry-run"
            result["cooldown_ok"] = cooldown_ok
            result["argv"] = argv
            return result

        if not cooldown_ok:
            # Skip (don't rename the marker) so the Stop hook still reflects it.
            result["status"] = "cooldown"
            return result

        r = runner("reflect", argv)
        result["returncode"] = r.get("returncode")
        if r.get("returncode") == 0:
            # Mark the session reflected so the Stop hook's guard skips it.
            if marker is not None and reflected_marker is not None:
                try:
                    marker.rename(reflected_marker)
                except OSError:
                    pass
            ao.record_fire(state, _REFLECT_CHAIN, now)
            ao.save_state(vault, state)
            result["status"] = "reflected"
        else:
            # Leave the `.start` marker so the Stop hook retries — don't consume
            # the cooldown on a failed reflect.
            result["status"] = "reflect-failed"
            result["stderr_tail"] = (r.get("stderr") or "")[-200:]
        return result
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        return result


# ── post-release refresh ────────────────────────────────────────────────────
def post_release_refresh(
    vault: Path,
    config: dict | None = None,
    now: datetime | None = None,
    *,
    dry_run: bool = False,
    runner=None,
) -> dict:
    """Refresh skill surfaces after `/release`: index_skills + discover_skills
    (cadence-checked). Returns a result dict; never raises. Status: disabled |
    dry-run | cooldown | ran | error."""
    vault = Path(vault)
    if now is None:
        now = datetime.now(timezone.utc)
    if runner is None:
        runner = _default_runner
    result: dict = {"dispatch": "post-release", "status": None, "dry_run": dry_run, "steps": []}
    try:
        if config is None:
            config = ao.load_config(vault)
        if not config.get("enable_phase_integration", True):
            result["status"] = "disabled"
            return result

        v = str(vault)
        steps = [
            ("index-skills", [_script("index_skills.py"), "--vault-path", v]),
            # cadence-check keeps a release from blocking on a full network fetch
            ("discover-skills", [_script("discover_skills.py"), "--vault-path", v, "--cadence-check"]),
        ]

        state = ao.load_state(vault)
        cooldown_ok = ao.should_fire(state, _RELEASE_CHAIN, now, _cooldown_hours(config))

        if dry_run:
            result["status"] = "dry-run"
            result["cooldown_ok"] = cooldown_ok
            result["steps"] = [{"name": n, "argv": argv} for n, argv in steps]
            return result

        if not cooldown_ok:
            result["status"] = "cooldown"
            return result

        for name, argv in steps:
            r = runner(name, argv)
            result["steps"].append({
                "name": name,
                "argv": argv,
                "returncode": r.get("returncode"),
                "timed_out": bool(r.get("timed_out", False)),
            })
        ao.record_fire(state, _RELEASE_CHAIN, now)
        ao.save_state(vault, state)
        result["status"] = "ran"
        return result
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        return result


# ── crystallization staging (agentm-experience-and-dreaming.md § Crystallization's
# phase-close trigger, locked calls 1-2-3-5-9) ───────────────────────────────
#
# A SIBLING step, not a step inside post_work_reflect() (call 2): post-work
# fires after every task commit, and reflect renames the session's `.start`
# marker to `.reflected` on its first success, so every commit after the
# first takes post_work_reflect()'s `already-reflected` early return. Staging
# placed inside that function would never fire on what is, after the first
# commit, the common path. _main() below calls this as a separate step for
# both post-work and post-release, merging the outcome under a new
# "crystallization" result key so the existing top-level "status" values
# verify-phases.sh already asserts on stay untouched.

_MARKER_RE = re.compile(r"^session-id-(.+)\.(start|reflected)$")


def _session_id_from_marker(marker: Path) -> str | None:
    m = _MARKER_RE.match(marker.name)
    return m.group(1) if m else None


def _resolve_transcript_for_staging(harness_dir: Path) -> tuple[str | None, str | None, str | None]:
    """Resolve the one session to stage a candidate for. Returns
    `(session_id, transcript, reason)`; `reason` is `None` on success, else
    `"no-session"` or `"ambiguous-session"`.

    Tolerant of both marker states (call 3): reflect renames `.start` to
    `.reflected` on success, so by the second task commit there is no
    `.start` file left to read — `_resolve_session_marker` (above) globs only
    `.start` and is correct for reflect's own purpose, but would starve
    staging of a transcript on the common post-first-commit path.

    Filters to markers whose transcript still resolves BEFORE counting (call
    9's live-transcript filter) — this is what makes the exactly-one rule
    usable at all rather than a pile of dead pointers manufacturing false
    ambiguity. A session that happens to carry both a `.start` and a
    `.reflected` marker (should not normally coexist, but is not assumed
    impossible) counts once, keyed by session id, not twice.
    """
    if not harness_dir.is_dir():
        return None, None, "no-session"
    try:
        markers = [p for p in harness_dir.glob("session-id-*.start") if p.is_file()]
        markers += [p for p in harness_dir.glob("session-id-*.reflected") if p.is_file()]
    except OSError:
        return None, None, "no-session"

    live: dict[str, str] = {}  # session_id -> transcript, dead pointers filtered first
    for marker in markers:
        sid = _session_id_from_marker(marker)
        if sid is None:
            continue
        transcript = _marker_transcript(marker)
        if not transcript or not Path(transcript).is_file():
            continue
        live[sid] = transcript

    if not live:
        return None, None, "no-session"
    if len(live) > 1:
        return None, None, "ambiguous-session"
    (sid, transcript), = live.items()
    return sid, transcript, None


def stage_crystallization_candidate(
    vault: Path,
    project_root: Path,
    phase: str,
    config: dict | None = None,
    now: datetime | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Stage a crystallization candidate for the session this phase-dispatch
    fired from. Returns a result dict; never raises (invoked from a
    non-blocking phase dispatch — see `phase_dispatch()`'s own contract).
    Status: `disabled | no-session | ambiguous-session | dry-run | staged |
    refreshed | capped | error`.

    Deliberately no cooldown (call 5): idempotence on `(phase, session_id)`
    already prevents duplicate candidates, and gating this write on a shared
    clock would silently drop whole sessions rather than just skip a
    redundant write.
    """
    vault = Path(vault)
    project_root = Path(project_root)
    result: dict = {"dispatch": "crystallization", "phase": phase, "dry_run": dry_run}
    try:
        if config is None:
            config = ao.load_config(vault)
        if not config.get("enable_crystallization_staging", True):
            result["status"] = "disabled"
            return result

        sid, transcript, reason = _resolve_transcript_for_staging(project_root / ".harness")
        if reason is not None:
            result["status"] = reason
            return result
        result["session_id"] = sid
        result["transcript"] = transcript

        if dry_run:
            result["status"] = "dry-run"
            return result

        # crystallize.stage_candidate takes an ISO string (its own on-disk
        # field shape); this function's `now` is a datetime for consistency
        # with post_work_reflect/post_release_refresh's testable-clock shape.
        now_iso = now.isoformat() if now is not None else None
        staged = crystallize.stage_candidate(vault, phase, sid, transcript, now=now_iso)
        result.update(staged)
        return result
    except Exception as e:  # phase-dispatch-invoked — must not wedge the phase.
        result["status"] = "error"
        result["error"] = str(e)
        return result


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="orchestration_phase.py")
    parser.add_argument("--vault-path", default=None)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    pw = sub.add_parser("post-work")
    pw.add_argument("--transcript", default=None,
                    help="explicit transcript path (most precise target)")
    pw.add_argument("--session-id", default=None,
                    help="target this session's marker exactly (session-exact; "
                         "default: the single unambiguous .start marker, else "
                         "defer to the Stop hook)")
    sub.add_parser("post-release")
    args = parser.parse_args(argv[1:])

    try:
        vault = ao._resolve_vault_path(args.vault_path)
    except ValueError:
        return 0  # no vault → silent, non-blocking

    if args.cmd == "post-work":
        result = post_work_reflect(
            vault, Path(args.project_root),
            transcript=args.transcript, session_id=args.session_id, dry_run=args.dry_run,
        )
    elif args.cmd == "post-release":
        result = post_release_refresh(vault, dry_run=args.dry_run)
    else:  # pragma: no cover
        return 2

    # Sibling step (call 2) — not nested in either dispatch above, and merged
    # under a new key so their existing top-level "status" values are untouched.
    result["crystallization"] = stage_crystallization_candidate(
        vault, Path(args.project_root), args.cmd, dry_run=args.dry_run,
    )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
