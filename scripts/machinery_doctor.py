#!/usr/bin/env python3
"""machinery_doctor.py — is agentm's own operational machinery actually
installed and firing on THIS machine, not just present in the repo?

(Consolidation follow-ups batch, machinery-integrity lane, piece 2.)

The motivating question was the operator's own: "how do we know all these
structures are working consistently when we run doctor or check the
dashboard?" Two confirmed failure cases proved the gap was real — the
session-cost-capture hook sat merged-but-never-installed in agentm for
weeks (fixed by the CONS-9 chunk that added `.claude/hooks/
session-cost-capture.sh` + its `Stop` wiring in `.claude/settings.json`),
and crickets' cross-review Gemini fallback degraded silently until a
parallel lane made it self-report (`CROSS-REVIEW-DEGRADED`, crickets PR
#189). Both are "the source exists, but is it actually wired on THIS
machine" gaps — exactly what `/doctor`'s existing structural checks don't
ask, because they check the harness's OWN install surface (sub-agents,
skills, user-scope hooks merged by `install.sh`), not this repo's own
dev-loop machinery (its Stop hook, its scheduled runner jobs, its
cross-repo bridges).

This module composes over checks that mostly already exist rather than
building a new subsystem: `scripts/runner/manifest.py` + `scripts/runner/
state.py` for job registration + last-fired; a self-contained read of the
device-local telemetry event log (mirrors crickets' `event_log.py` schema,
no cross-repo import needed for a two-field read); `git rev-parse
--git-path hooks` for worktree-safe `.git/hooks/` resolution; and a
plain file-presence read of crickets' cross-review + development-lifecycle
scripts when a sibling checkout is reachable.

Four status values, matching the "honest-dark" convention the console
lane's health-scorecard already uses, adapted to a per-machine liveness
axis rather than a design-vs-built one:

  OK           verified installed + wired on this machine right now.
  WARN         source/template exists but isn't installed on this
               machine, or is installed but has never fired — visible,
               non-fatal (a fresh clone or an opt-in-only job legitimately
               starts here).
  FAIL         installed/registered but broken (references a missing
               file, fails to parse) — a real regression, the exact shape
               of the two confirmed incidents above.
  UNVERIFIED   this repo alone can't determine liveness (usually: no
               crickets sibling checkout reachable) — named plainly with
               an owner, never silently omitted.

Read-only. Never installs, never mutates `.git/hooks/`, `.claude/
settings.json`, or `.harness/jobs/`. Stdlib-only (PyYAML already a
repo-wide dependency via `runner.manifest`).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from runner import manifest as manifest_mod  # noqa: E402
from runner import state as state_mod  # noqa: E402

_VALID_STATUSES = ("OK", "WARN", "FAIL", "UNVERIFIED")


@dataclass
class Check:
    name: str
    status: str
    detail: str
    last_fired: Optional[float] = None
    owner: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"invalid status {self.status!r} (expected one of {_VALID_STATUSES})")

    def to_dict(self) -> dict:
        return {
            "name": self.name, "status": self.status, "detail": self.detail,
            "last_fired": self.last_fired, "owner": self.owner,
        }


def repo_root() -> Path:
    """This script always lives at `<repo>/scripts/machinery_doctor.py` —
    unlike `console.py` (installed as a skill elsewhere), no upward search
    is needed; each worktree carries its own copy at the same relative
    path."""
    return _HERE.parent


# ── device-local telemetry event log (self-contained read; no crickets
#    import needed for a two-field scan) ─────────────────────────────────
def _telemetry_dir() -> Path:
    """Mirrors crickets' `event_log.telemetry_dir()`: `$AGENTM_TELEMETRY_DIR`
    override, else `~/.agentm/telemetry/`."""
    env = os.environ.get("AGENTM_TELEMETRY_DIR", "").strip()
    return Path(env) if env else Path.home() / ".agentm" / "telemetry"


def last_event_epoch(event_name: str, *, telemetry_root: Optional[Path] = None) -> Optional[float]:
    """Most recent `ts` (epoch seconds) among events named `event_name`
    across every monthly `events-*.jsonl` file. None if the log is
    absent/empty/unparseable — never raises."""
    root = telemetry_root if telemetry_root is not None else _telemetry_dir()
    if not root.is_dir():
        return None
    latest: Optional[float] = None
    try:
        log_files = sorted(root.glob("events-*.jsonl"))
    except OSError:
        return None
    for p in log_files:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("event") != event_name:
                continue
            ts_raw = rec.get("ts")
            if not isinstance(ts_raw, str):
                continue
            try:
                dt = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            epoch = dt.timestamp()
            if latest is None or epoch > latest:
                latest = epoch
    return latest


# ── worktree-safe .git/hooks/ resolution ─────────────────────────────────
def git_hooks_dir(repo: Path) -> Optional[Path]:
    """`git rev-parse --git-path hooks` — resolves to the shared common
    git dir's hooks/ whether `repo` is the main checkout or one of its
    worktrees (a worktree's `.git` is a file, not a directory, so a bare
    `repo / '.git' / 'hooks'` join silently misses)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = proc.stdout.strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = repo / p
    try:
        return p.resolve()
    except OSError:
        return None


# ── crickets sibling resolution (mirrors console.py's own convention) ────
def find_crickets_root() -> Optional[Path]:
    env = os.environ.get("CRICKETS_SCRIPTS_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
        for candidate in (p, *p.parents):
            if (candidate / "src" / "github-projects").is_dir():
                return candidate
        return None
    default = Path.home() / "Antigravity" / "crickets"
    return default if default.is_dir() else None


# ── individual checks ────────────────────────────────────────────────────
def check_stop_hook_wired(
    repo: Path, *, hook_filename: str = "session-cost-capture.sh",
    telemetry_root: Optional[Path] = None,
) -> Check:
    """The concrete regression this lane's motivating incident was:
    `.claude/hooks/session-cost-capture.sh` shipped in a commit, but
    nothing re-verified per-machine that `.claude/settings.json`'s `Stop`
    block still references it. Required (not merely optional), since both
    the script and its wiring are tracked, committed files in this repo —
    unlike the manually-installed git hooks below."""
    name = f"stop-hook:{hook_filename}"
    settings_path = repo / ".claude" / "settings.json"
    if not settings_path.is_file():
        return Check(name, "FAIL", f"{settings_path} not found — Stop hook can't be wired")
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return Check(name, "FAIL", f"{settings_path} unreadable/invalid JSON ({e})")
    stop_entries = data.get("hooks", {}).get("Stop", []) if isinstance(data, dict) else []
    wired = False
    if isinstance(stop_entries, list):
        for entry in stop_entries:
            inner_hooks = entry.get("hooks", []) if isinstance(entry, dict) else []
            for inner in inner_hooks if isinstance(inner_hooks, list) else []:
                cmd = inner.get("command", "") if isinstance(inner, dict) else ""
                if isinstance(cmd, str) and hook_filename in cmd:
                    wired = True
    if not wired:
        return Check(name, "FAIL", f"settings.json has no Stop hook referencing {hook_filename} — re-run the wiring step")
    script_path = repo / ".claude" / "hooks" / hook_filename
    if not script_path.is_file():
        return Check(name, "FAIL", f"wired in settings.json but script missing on disk at {script_path}")
    last_fired = last_event_epoch("session-cost", telemetry_root=telemetry_root)
    return Check(name, "OK", f"wired in settings.json + script present at {script_path}", last_fired=last_fired)


def check_git_hook_installed(repo: Path, hook_name: str, *, note: str = "") -> Check:
    """Optional, manually-installed dev-safety git hooks (commit-msg-gate,
    coauthor-guard's prepare-commit-msg) — crickets' own group.yaml is
    explicit that "no automated installer wires it in yet," so an absent
    hook here is a real, visible per-machine state, not a regression:
    WARN, never FAIL."""
    name = f"git-hook:{hook_name}"
    hooks_dir = git_hooks_dir(repo)
    if hooks_dir is None:
        return Check(name, "UNVERIFIED", f"could not resolve .git hooks dir for {repo} (not a git repo?)")
    hook_path = hooks_dir / hook_name
    if not hook_path.is_file():
        suffix = f" — {note}" if note else ""
        return Check(name, "WARN", f"not installed at {hook_path} (manual install{suffix})")
    if not os.access(hook_path, os.X_OK):
        return Check(name, "WARN", f"installed at {hook_path} but not executable")
    return Check(name, "OK", f"installed at {hook_path}")


def check_runner_job(repo: Path, job_name: str, *, state_root: Optional[Path] = None) -> Check:
    """Registration + last-fired state for one `templates/jobs/<name>.yaml`
    template, reusing `runner.manifest` (parsing) and `runner.state`
    (per-job last-run marker) rather than re-deriving either."""
    template_path = repo / "templates" / "jobs" / f"{job_name}.yaml"
    if not template_path.is_file():
        return Check(job_name, "UNVERIFIED", f"no shipped template at {template_path}")
    registered_path = repo / ".harness" / "jobs" / f"{job_name}.yaml"
    marker = state_mod.read_marker(job_name, state_root=state_root)
    last_run = state_mod.last_run_epoch(marker)
    if not registered_path.is_file():
        return Check(
            job_name, "WARN",
            "template shipped but not registered on this machine "
            f"(copy to {registered_path} to enable)",
            last_fired=last_run,
        )
    try:
        jobs = manifest_mod.load_manifests(registered_path.parent)
    except manifest_mod.ManifestError as e:
        return Check(job_name, "FAIL", f"registered manifest fails to parse: {e}")
    job = next((j for j in jobs if j.name == job_name), None)
    if job is None:
        return Check(job_name, "FAIL", f"{registered_path} present but not found by the loader")
    mode = "dry-run" if job.dry_run else "live"
    if last_run is None:
        return Check(job_name, "WARN", f"registered ({mode}) but has never fired on this machine", last_fired=None)
    return Check(job_name, "OK", f"registered ({mode}), last fired", last_fired=last_run)


def job_names(repo: Path) -> list:
    jobs_dir = repo / "templates" / "jobs"
    if not jobs_dir.is_dir():
        return []
    return sorted(p.stem for p in jobs_dir.glob("*.yaml"))


def check_crickets_sibling() -> Check:
    root = find_crickets_root()
    if root is None:
        return Check(
            "crickets-sibling", "WARN",
            "no crickets sibling checkout found (~/Antigravity/crickets or $CRICKETS_SCRIPTS_DIR) "
            "— cross-repo bridges (spend rollup, board drift, cross-review) degrade to n/a",
        )
    return Check("crickets-sibling", "OK", f"resolved at {root}")


def check_cross_review_visible_degradation(crickets_root: Optional[Path]) -> Check:
    """Confirms the fix from crickets PR #189 is present: an agy (Gemini)
    fallback in `cross-review.sh` prints a `CROSS-REVIEW-DEGRADED` stdout
    marker instead of degrading silently — the transport moved from the
    standalone `gemini` CLI to `agy` in V8 proving Lane G (2026-07-13), the
    marker text and this check's source-presence probe are unchanged. This
    repo can't independently exercise the fallback (that needs a crickets
    checkout + the adversarial-reviewer-cross agent), so this only confirms
    the marker text ships — a source-presence check, not a live-behavior
    probe."""
    name = "cross-review-degradation-marker"
    owner = "crickets code-review plugin"
    if crickets_root is None:
        return Check(name, "UNVERIFIED", "no crickets sibling — can't confirm the marker is present", owner=owner)
    script = crickets_root / "src" / "code-review" / "scripts" / "cross-review.sh"
    if not script.is_file():
        return Check(name, "UNVERIFIED", f"{script} not found in crickets checkout", owner=owner)
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return Check(name, "UNVERIFIED", f"could not read {script} ({e})", owner=owner)
    if "CROSS-REVIEW-DEGRADED" in text:
        return Check(
            name, "OK",
            "cross-review.sh emits a visible CROSS-REVIEW-DEGRADED marker on an agy (Gemini) fallback "
            "(crickets PR #189, retargeted to agy in V8 proving Lane G) — a degraded run self-reports, "
            "it does not silently pass",
        )
    return Check(name, "FAIL", "cross-review.sh has no visible degradation marker — an agy fallback would be silent")


def check_crickets_coordination_suite(crickets_root: Optional[Path]) -> Check:
    """The coordination checks the operator named (readiness/touches via
    `check-plan-grounding.py` + `doctor_worktrees.py`, `preflight_reconcile.py`,
    the `escalation_tripwire.py`, the `agentm_bridge.py` cascade) all live in
    crickets' `development-lifecycle` capability. Each already has its own
    dedicated crickets-side unit test (`scripts/test_<name>.py`) — this
    check confirms the scripts are present in the sibling checkout; it does
    not re-run crickets' own test suite from here (that would duplicate
    crickets CI, not compose over it)."""
    name = "crickets-coordination-suite"
    owner = "crickets development-lifecycle"
    if crickets_root is None:
        return Check(name, "UNVERIFIED", "no crickets sibling — can't confirm", owner=owner)
    scripts_dir = crickets_root / "src" / "development-lifecycle" / "scripts"
    wanted = [
        "preflight_reconcile.py", "check-plan-grounding.py", "escalation_tripwire.py",
        "agentm_bridge.py", "doctor_worktrees.py",
    ]
    missing = [n for n in wanted if not (scripts_dir / n).is_file()]
    if missing:
        return Check(name, "FAIL", f"missing from crickets checkout: {', '.join(missing)}", owner=owner)
    return Check(
        name, "OK",
        f"present in {scripts_dir} — each covered by its own crickets-side "
        "scripts/test_<name>.py (not re-run from agentm)",
    )


# ── composition ───────────────────────────────────────────────────────────
def run_inventory(
    repo: Optional[Path] = None, *, state_root: Optional[Path] = None,
    telemetry_root: Optional[Path] = None,
) -> list:
    repo = repo if repo is not None else repo_root()
    checks = [
        check_stop_hook_wired(repo, telemetry_root=telemetry_root),
        check_git_hook_installed(
            repo, "commit-msg",
            note="see crickets src/developer-safety/hooks/commit-msg-gate/hook.md",
        ),
        check_git_hook_installed(
            repo, "prepare-commit-msg",
            note="see crickets src/developer-safety/hooks/coauthor-guard/hook.md",
        ),
    ]
    for job_name in job_names(repo):
        checks.append(check_runner_job(repo, job_name, state_root=state_root))
    crickets_check = check_crickets_sibling()
    checks.append(crickets_check)
    crickets_root = find_crickets_root()
    checks.append(check_cross_review_visible_degradation(crickets_root))
    checks.append(check_crickets_coordination_suite(crickets_root))
    return checks


def summarize(checks: list) -> dict:
    counts = {s: 0 for s in _VALID_STATUSES}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    return counts


def render_text(checks: list) -> str:
    counts = summarize(checks)
    width = max((len(c.name) for c in checks), default=0)
    lines = ["machinery doctor:"]
    for c in checks:
        fired = ""
        if c.last_fired is not None:
            fired = " (last fired " + datetime.fromtimestamp(c.last_fired, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ") + ")"
        owner = f" [owner: {c.owner}]" if c.owner else ""
        lines.append(f"  [{c.status:<10}] {c.name:<{width}}  {c.detail}{fired}{owner}")
    lines.append("")
    lines.append(
        f"summary: {counts['OK']} OK, {counts['WARN']} WARN, "
        f"{counts['FAIL']} FAIL, {counts['UNVERIFIED']} UNVERIFIED"
    )
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="machinery_doctor", description="Per-machine liveness check over agentm's operational machinery.",
    )
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--repo", default=None, help="repo root override (default: this script's own repo)")
    return p


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    repo = Path(args.repo).resolve() if args.repo else repo_root()
    checks = run_inventory(repo)
    if args.format == "json":
        print(json.dumps({"checks": [c.to_dict() for c in checks], "summary": summarize(checks)}, indent=2))
    else:
        print(render_text(checks), end="")
    # Advisory, like vault_lint.py and doctor's own contract — never fails
    # a build on its own; FAIL rows are visible in the output, not in the
    # exit code, since this composes into /doctor's own reporting rather
    # than gating anything by itself.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
