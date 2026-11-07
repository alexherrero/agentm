#!/usr/bin/env python3
"""orchestration_phase.py — phase-integration auto-dispatch (V4 #23 task 5).

Wires the memory skills into the harness phase boundaries, EXTENDING (not
duplicating) the V4 #8 auto-context dispatcher (`harness_memory.py`, which owns
recall / offer-save / plan-done-promotion). Two operator-gated, cooldown-gated,
config-toggleable dispatches the phase specs call at their tail:

  post-work     After `/work` commits a task, stage the just-finished session's
                new slice for tonight's lesson compile
                (`lesson_compile.py collect --transcript <path>`), through the
                same per-session bookmark the night reads, so the slice is
                staged once whichever gets there first. Nothing is filed here
                (task 199; until then this ran `reflect.py --route` and
                cooperated with the Stop hook through `.reflected` markers).

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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# sibling imports (same scripts dir)
import auto_orchestration as ao

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
def _session_transcript(project_root: Path, session_id: "str | None") -> "str | None":
    """The `/work` session's transcript, found in the host's folder for the
    project root: the named session's file, else the folder's newest.

    The newest is a guess when two sessions share a repo, and since task 199 a
    harmless one: staging stages only what is new past a session's bookmark,
    and every session is collected again at night, so the worst a wrong guess
    does is stage another session's slice early."""
    import session_binding  # noqa: E402  (same skill dir)
    import lesson_compile  # noqa: E402
    try:
        real = project_root.resolve()
    except OSError:
        real = project_root
    folder = lesson_compile.claude_projects_root() / session_binding.host_folder_name(str(real))
    if session_id:
        p = folder / f"{session_id}.jsonl"
        return str(p) if p.is_file() else None
    try:
        files = [f for f in folder.glob("*.jsonl") if f.is_file()]
    except OSError:
        return None
    return str(max(files, key=lambda f: f.stat().st_mtime)) if files else None


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
    """Stage the just-finished `/work` session's new slice for tonight's lesson
    compile, through the same per-session bookmark the night uses (task 199).
    Nothing is filed here. Returns a result dict; never raises. Status:
    disabled | dry-run | cooldown | no-session | staged | stage-failed | error.

    Target resolution (most → least precise): explicit `transcript=` → explicit
    `session_id=` in the host's transcript folder for `project_root` → that
    folder's newest transcript."""
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

        tpath = transcript or _session_transcript(project_root, session_id)
        if not tpath:
            result["status"] = "no-session"
            return result
        result["transcript"] = tpath

        state = ao.load_state(vault)
        cooldown_ok = ao.should_fire(state, _REFLECT_CHAIN, now, _cooldown_hours(config))
        argv = [_script("lesson_compile.py"), "collect", "--transcript", tpath, "--vault-path", str(vault)]

        if dry_run:
            result["status"] = "dry-run"
            result["cooldown_ok"] = cooldown_ok
            result["argv"] = argv
            return result

        if not cooldown_ok:
            result["status"] = "cooldown"
            return result

        r = runner("reflect", argv)
        result["returncode"] = r.get("returncode")
        if r.get("returncode") == 0:
            ao.record_fire(state, _REFLECT_CHAIN, now)
            ao.save_state(vault, state)
            result["status"] = "staged"
        else:
            # The night collects the session anyway; nothing is lost.
            result["status"] = "stage-failed"
            result["stderr_tail"] = (r.get("stderr") or "")[-200:]
        return result
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        return result



# ── local `main`, kept level with the remote ────────────────────────────────
DEFAULT_BRANCH = "main"


def _repo_root() -> "Path | None":
    """The clone this file is installed from, or None.

    The skills directory is symlinked into the primary clone on this machine,
    so resolving through __file__ lands on the clone whose checked-out commit
    *is* the live configuration — which is the clone whose `main` matters.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return None


def _git(repo: Path, *args: str) -> "tuple[int, str]":
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return r.returncode, (r.stdout + r.stderr).strip()


def refresh_local_main(repo: Path, *, branch: str = DEFAULT_BRANCH,
                       dry_run: bool = False) -> dict:
    """Fast-forward the primary clone's local `main` to `origin/main`.

    The primary clone is deliberately kept detached at `origin/main` — release
    commits are made there detached and pushed `HEAD:main` — so local `main` is
    never held and is always free for a worktree to take. The cost is that it
    also goes stale, and on 2026-09-06 a desktop session's first minute ran
    `git checkout main` in it, landing on a ref 38 commits behind. Because the
    machine's hooks and skills are symlinks into that clone, the live
    configuration silently regressed two days, and a survey then read a stale
    design and reported a false finding from it.

    Nothing here stops the checkout. It makes the checkout harmless: a `main`
    that equals `origin/main` is the same tree the detached HEAD was on.

    Deliberately conservative. Only a fast-forward — if local `main` holds
    commits `origin/main` does not, that is somebody's unpushed work and this
    leaves it alone and says so. And never while `main` is checked out
    anywhere: `git branch -f` refuses that, and a worktree holding the branch
    is the stranded-worktree state, not something to force through.
    """
    repo = Path(repo)
    out: dict = {"step": "refresh-local-main", "branch": branch, "status": None}

    rc, remote = _git(repo, "rev-parse", "--verify", f"refs/remotes/origin/{branch}")
    if rc != 0:
        out["status"] = "no-remote-branch"
        return out
    out["target"] = remote

    rc, local = _git(repo, "rev-parse", "--verify", f"refs/heads/{branch}")
    if rc != 0:
        out["status"] = "no-local-branch"
        return out
    out["from"] = local

    if local == remote:
        out["status"] = "already-current"
        return out

    # Refuse to discard local commits. `--is-ancestor` is the right question
    # here and only here: this asks whether the local ref is *reachable from*
    # the remote one, which is what "a fast-forward is safe" means. It is not
    # the discredited test for whether a squashed branch has landed.
    rc, _ = _git(repo, "merge-base", "--is-ancestor", local, remote)
    if rc != 0:
        out["status"] = "diverged"
        return out

    # Whether a worktree has the branch checked out. `git branch -f` refuses in
    # that case anyway; asking first turns a stderr into a reported status.
    rc, worktrees = _git(repo, "worktree", "list", "--porcelain")
    if rc == 0 and f"branch refs/heads/{branch}" in worktrees:
        out["status"] = "checked-out"
        return out

    if dry_run:
        out["status"] = "dry-run"
        return out

    rc, detail = _git(repo, "branch", "-f", branch, f"origin/{branch}")
    if rc != 0:
        out["status"] = "failed"
        out["detail"] = detail
        return out
    out["status"] = "fast-forwarded"
    return out


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

        # Outside the cooldown gate on purpose. The cooldown exists to keep a
        # burst of releases from re-running the network-touching skill refresh;
        # levelling a local ref is free and idempotent, and "after every
        # release" is the whole guarantee — a release skipped by the cooldown
        # is exactly when the clone would go stale unnoticed.
        repo = _repo_root()
        if repo is not None:
            result["local_main"] = refresh_local_main(repo, dry_run=dry_run)

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


# Crystallization staging retired (agentm-vault plan 04). It staged a marker
# per session at post-work and post-release for a digest nobody composed — 52
# of them by 2026-09-06 — and the design has since ruled that crystallize is
# never a session's act: it is a weekly dreaming phase reading closed work for
# recurrence (plan 11), and a synthesis on request (`crystallize.py write`).


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
                    help="this session's transcript in the host's folder for the "
                         "project root (default: that folder's newest)")
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

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
