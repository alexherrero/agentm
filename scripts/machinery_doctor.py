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
--git-path hooks` for worktree-safe `.git/hooks/` resolution; `health/
session_notify.py` + `health/session_email.py`'s own config readers for
whether the autonomy delivery channels are actually configured; and a
plain file-presence read of crickets' cross-review + development-lifecycle
scripts when a sibling checkout is reachable.

A registered job is not the same as a working one. A third confirmed case
(2026-08-02) made that concrete: re-running `install.sh` wiped every
`plugins.autonomy.*` key out of `~/.claude/.agentm-config.json`, and both
observability delivery jobs went on reporting `registered (live)` here while
delivering nothing, because registration was all this module asked about.
`check_job_config()` closes that: it asks each autonomy channel's own reader
whether the settings it needs are still there.

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

if str(_HERE / "health") not in sys.path:
    sys.path.insert(0, str(_HERE / "health"))

from runner import manifest as manifest_mod  # noqa: E402
from runner import state as state_mod  # noqa: E402
# The autonomy channels' own config readers — reused, never re-derived, so a
# key rename in either module can't drift from what this doctor asks about.
import session_email as session_email_mod  # noqa: E402
import session_notify as session_notify_mod  # noqa: E402

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


# ── job config completeness (installer data-loss regression, 2026-08-02) ────
# `check_runner_job()` above asks whether a job is REGISTERED. That is not the
# same question as whether it can do anything. The two autonomy delivery
# channels read their settings from `<prefix>/.agentm-config.json`; strip those
# settings and both jobs still fire, still report `registered (live)` here, and
# silently no-op forever. That is exactly what a re-run of `install.sh` did on
# 2026-08-02, when persist rebuilt the config from an allowlist and dropped
# every `plugins.autonomy.*` key. The installer bug is fixed; this row is the
# detector, so the next way those keys go missing is visible on the same day.
#
# Config-absent is WARN, not FAIL: declining to opt in is a legitimate state,
# and these channels are absent-by-default by design. The failure being caught
# is silence, not misconfiguration.
_JOB_CONFIG_CHECKS = (
    ("observability-notify-daily", "plugins.autonomy.notify_enabled"),
    ("observability-email-daily", "plugins.autonomy.email_to + .email_smtp_url"),
)


def _autonomy_config_present(job_name: str, install_prefix: Optional[Path]) -> bool:
    """Would this job's channel actually deliver? Delegates to the channel's
    own reader — the same call the job itself makes at fire time."""
    if job_name == "observability-notify-daily":
        return session_notify_mod.notify_enabled(install_prefix)
    if job_name == "observability-email-daily":
        return session_email_mod.email_config(install_prefix) is not None
    raise KeyError(job_name)


def check_job_config(
    repo: Path, job_name: str, keys_label: str, *,
    install_prefix: Optional[Path] = None,
) -> Check:
    """Registration says the job runs; this says it can do something when it does."""
    name = f"{job_name}:config"
    registered_path = repo / ".harness" / "jobs" / f"{job_name}.yaml"
    if not registered_path.is_file():
        return Check(
            name, "UNVERIFIED",
            f"job not registered on this machine — {keys_label} not applicable yet",
        )
    try:
        configured = _autonomy_config_present(job_name, install_prefix)
    except (KeyError, OSError) as e:
        return Check(name, "FAIL", f"config readable check failed: {e}")
    if configured:
        return Check(name, "OK", f"{keys_label} present in .agentm-config.json")
    return Check(
        name, "WARN",
        f"registered but {keys_label} absent from .agentm-config.json — the job "
        "fires and silently delivers nothing "
        "(set it via `python3 scripts/agentm_config.py --help`)",
    )


def job_names(repo: Path) -> list:
    jobs_dir = repo / "templates" / "jobs"
    if not jobs_dir.is_dir():
        return []
    return sorted(p.stem for p in jobs_dir.glob("*.yaml"))


# ── unattended merge-gate (V8-proving item 19) ──────────────────────────────
_UNATTENDED_DISPATCH_JOB = "n1-overnight"
_GH_PR_MERGE_RULE = "Bash(gh pr merge:*)"


def global_claude_settings_path() -> Path:
    """The *user-scope* Claude Code config — where the unattended-merge
    permission gate lives. Deliberately distinct from the repo-scope
    `<repo>/.claude/settings.json` the Stop-hook check reads: this gate is a
    property of the machine's global permission floor, not this project. Only
    read here, never written — provisioning it is the operator's dev-setup
    dotfiles' job (which owns the global `permissions` block); agentm detects."""
    return Path.home() / ".claude" / "settings.json"


def _permission_lists(settings_path: Path) -> Optional[dict]:
    """{'allow': [...], 'ask': [...], 'deny': [...]} from a Claude Code
    settings.json, or None if unreadable/invalid. Missing lists → []."""
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    perms = data.get("permissions", {})
    if not isinstance(perms, dict):
        perms = {}
    out = {}
    for key in ("allow", "ask", "deny"):
        val = perms.get(key, [])
        out[key] = val if isinstance(val, list) else []
    return out


def check_unattended_merge_gate(
    repo: Path, *, settings_path: Optional[Path] = None,
) -> Check:
    """Does the unattended merge-on-green step have a clear permission path?

    The V8-proving N1 retest (vault PROVING-LEDGER item 19, 2026-07-15) drove a
    real unattended dispatch all the way to `gh pr merge`, then stalled: the
    operator's *global* `~/.claude/settings.json` carried `Bash(gh pr merge:*)`
    in its `ask` list, and Claude Code resolves permissions `deny > ask > allow`
    — so an `ask` entry beats any `allow` at any scope. The fix is a *move*
    (ask → allow) in the global file, which only the global-config owner (the
    operator's dev-setup dotfiles) provisions; agentm's role is to make the gap
    *visible* here, never to write the global file. See
    wiki/designs/agentm-autonomy.md's amendment log (2026-07-15) for the full
    ruling and the why-not-agentm-owns-it reasoning.

    Only meaningful when this machine actually runs the unattended-dispatch job
    — a checkout with no `n1-overnight` registered in `.harness/jobs/` (the CI
    case, since `.harness/` is gitignored) never exercises the gate, so this is
    a clean OK there, not a warning.

    ⚠️ The `deny > ask > allow` precedence is a load-bearing assumption. If it
    ever changes so a narrower scoped `allow` can override a broader global
    `ask`, the remedy (and the whole where-to-provision ruling) should be
    re-audited — see the design's re-audit trigger."""
    name = "unattended-merge-gate"
    registered = repo / ".harness" / "jobs" / f"{_UNATTENDED_DISPATCH_JOB}.yaml"
    if not registered.is_file():
        return Check(
            name, "OK",
            f"no unattended-dispatch job registered ({_UNATTENDED_DISPATCH_JOB}.yaml "
            "absent from .harness/jobs/) — the merge gate isn't exercised on this machine",
        )
    path = settings_path if settings_path is not None else global_claude_settings_path()
    remedy = (
        f"run `bash {repo}/scripts/enable-unattended-merge.sh` to move it to `allow` "
        "(the operator's dev-setup link-configs.sh provisions this automatically on their machines)"
    )
    if not path.is_file():
        return Check(
            name, "WARN",
            f"{_UNATTENDED_DISPATCH_JOB} is registered but {path} is absent — can't confirm "
            f"`gh pr merge` won't block an unattended run. {remedy}",
        )
    perms = _permission_lists(path)
    if perms is None:
        return Check(
            name, "WARN",
            f"{_UNATTENDED_DISPATCH_JOB} is registered but {path} is unreadable/invalid JSON "
            f"— can't confirm the merge gate. {remedy}",
        )
    rule = _GH_PR_MERGE_RULE
    if rule in perms["deny"]:
        return Check(
            name, "WARN",
            f"`{rule}` is in the global `deny` list — an unattended merge-on-green run is "
            f"blocked at the merge step. {remedy}",
        )
    if rule in perms["ask"]:
        return Check(
            name, "WARN",
            f"`{rule}` is in the global `ask` list, which beats any `allow` (deny>ask>allow) — "
            f"an unattended merge-on-green run blocks at the merge step. {remedy}",
        )
    if rule in perms["allow"]:
        return Check(
            name, "OK",
            f"`{rule}` is allowed at global scope and not gated by ask/deny — "
            "an unattended merge-on-green run won't block at the merge step",
        )
    return Check(
        name, "WARN",
        f"`{rule}` is in neither `allow` nor `ask` at global scope — default prompt mode blocks "
        f"an unattended merge at the merge step. {remedy}",
    )


def check_storage_rules() -> Check:
    """The filing contract resolves and parses.

    This is the operator-facing half of the fail-closed arrangement. When the
    rules block will not parse, filing halts — notes wait as `unfiled` and
    nothing files anywhere — and this row is where an operator sees why without
    reading a nightly log. A vault with no rules file is not a failure: absence
    falls through to the packaged default by design, and the row says so.
    """
    name = "storage-rules"
    try:
        sys.path.insert(0, str(repo_root() / "harness" / "skills" / "memory" / "scripts"))
        import storage_rules as storage_rules_mod
    except ImportError as exc:
        return Check(name, "FAIL", f"the filing contract module will not import: {exc}")

    try:
        rules = storage_rules_mod.load()
    except Exception as exc:
        return Check(
            name, "FAIL",
            f"filing is halted — the rules block does not parse: {exc}. "
            f"Notes stay `unfiled` until it does.",
        )

    where = "packaged default" if rules.is_packaged_default else str(rules.source)
    status = "WARN" if rules.is_packaged_default else "OK"
    detail = (
        f"{len(rules.memory_types())} memory types, {len(rules.record_kinds())} record "
        f"kinds, hash {rules.content_hash()} — from {where}"
    )
    if rules.is_packaged_default:
        detail += " (no vault instance; edits to it will not take effect)"
    return Check(name, status, detail)


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


def check_memory_hook_interpreter(repo: Optional[Path] = None) -> Check:
    """Does the interpreter the memory hooks actually run work at all?

    This row exists because of a silent, years-long outage. The hooks used to
    end in a bare `python3`, a PATH lookup that resolves to Apple's system
    Python on a stock macOS box, and the vector index could never load there —
    so recall quietly returned nothing while every caller treated the failure
    as a graceful "not built yet" skip. Nothing went red, and `/doctor` had no
    row that would have said so.

    The vector index is gone (see wiki/designs/agentm-rescope-week1-
    experiment.md), so the sqlite-vec half of that question is moot. The half
    that is not moot is the one that made the outage possible: the hooks run
    whatever this resolver prints, and if that is not a working interpreter
    every memory hook fails the same quiet way. So this now asserts exactly
    that much, and stops asserting an extension nothing loads any more.

    It delegates rather than re-derives. The interpreter is whatever
    `harness/hooks/lib/resolve-python.sh` prints — the same script the hooks
    bootstrap through — so this cannot report a healthy interpreter the hooks
    never pick. A check that re-implemented the candidate list would be a
    second implementation to drift, which is the failure mode this whole area
    already has a scar from.

    Statuses:
      OK    the resolved interpreter runs
      FAIL  the resolver printed nothing, could not be run, or named an
            interpreter that does not execute
      WARN  the resolver is absent (partial checkout/install), so the hooks are
            on the bare-`python3` floor and this can't say what that resolves to
    """
    name = "memory-hook-interpreter"
    repo = repo if repo is not None else repo_root()
    resolver = repo / "harness" / "hooks" / "lib" / "resolve-python.sh"
    if not resolver.is_file():
        return Check(
            name, "WARN",
            f"{resolver} missing — the memory hooks fall back to a bare `python3`, "
            "whatever that resolves to on this box",
        )
    try:
        resolved = subprocess.run(
            ["bash", str(resolver)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return Check(name, "FAIL", f"could not run {resolver} ({e})")
    if not resolved:
        return Check(name, "FAIL", f"{resolver} printed nothing — it must always print an interpreter")

    # Ask the resolved interpreter itself, rather than inferring from its name.
    probe = (
        "import json, sys\n"
        "print(json.dumps({'version': '.'.join(str(p) for p in sys.version_info[:3])}))\n"
    )
    try:
        proc = subprocess.run([resolved, "-c", probe], capture_output=True, text=True, timeout=60)
        info = json.loads(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return Check(name, "FAIL", f"resolved to `{resolved}` but could not run it ({e})")

    where = f"`{resolved}` (Python {info.get('version', '?')})"
    override = os.environ.get("AGENTM_PYTHON") or os.environ.get("AGENT_TOOLKIT_PYTHON")
    via = " — selected by your $AGENTM_PYTHON/$AGENT_TOOLKIT_PYTHON override" if override else ""
    return Check(name, "OK", f"{where} runs{via} — the memory hooks have a working interpreter")


# ── project.json vault pointers ─────────────────────────────────────────────
# Path-valued keys on a `project.json`, each paired with the vault surface it
# must sit under. `MEMORY_VAULT_PATH` names the memory tree itself and
# `items_source` addresses per-project state inside it, so both belong under
# `harness_memory.memory_root()`. `IDEAS_SURFACE_PATH` is the operator's own
# note at the vault root, one level ABOVE the memory tree — checking it against
# the memory root would flag a correctly-configured install.
_PROJECT_JSON_PATH_KEYS = (
    ("items_source", "memory"),
    ("env.MEMORY_VAULT_PATH", "memory"),
    ("env.IDEAS_SURFACE_PATH", "vault"),
)


def _dotted(cfg: dict, key: str):
    """Read a one-level-dotted key (`env.MEMORY_VAULT_PATH`). None if absent."""
    node = cfg
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) and node.strip() else None


def _is_under(child: Path, parent: Path) -> bool:
    """True when `child` is `parent` or sits beneath it, comparing resolved
    paths so a symlinked vault root doesn't read as out-of-tree."""
    try:
        child_r, parent_r = child.resolve(), parent.resolve()
    except OSError:
        return False
    return child_r == parent_r or parent_r in child_r.parents


def check_project_json_pointers(
    config_path: Path, label: str, *,
    vault_root: Optional[Path] = None,
    mem_root: Optional[Path] = None,
) -> Check:
    """Do this `project.json`'s vault pointers still aim at the live vault?

    This row exists because of a silent 12-day staleness (2026-08-14). After the
    vault moved off the Google Drive mount and its internal layout was
    reorganized (`projects/` → `desk/projects/`), both `.harness/project.json`
    files still named the old root. That path *still existed on disk*, so
    nothing raised: every read simply returned a tree frozen at the day of the
    move. Wrong content is worse than absent content, because it reads as valid.

    No existing gate could have caught it. `check-no-hardcoded-vault-path` walks
    the filesystem and prunes `.harness/` by name (`_SKIP_DIRS`), and it only
    recognizes the retired cloud-drive mount literal anyway — a future move
    between two plain local roots would sail past it. This asks the general
    question instead: does each configured path exist, and does it sit inside
    the vault this machine actually resolves right now?

    Statuses:
      OK          every path-valued key present resolves inside the live vault
      FAIL        a key names a path that is missing, or one that exists but
                  lies outside the resolved vault — the silent-staleness shape
      WARN        no vault resolves on this machine, or the file won't parse,
                  so there is nothing to check the pointers against
    """
    name = f"project-json-pointers:{label}"
    if vault_root is None or mem_root is None:
        try:
            import harness_memory as _hm  # noqa: PLC0415 — optional, resolved per call
            vault_root = vault_root if vault_root is not None else _hm.vault_path()
            mem_root = mem_root if mem_root is not None else _hm.memory_root()
        except Exception as e:  # noqa: BLE001 — a doctor row never raises
            return Check(name, "WARN", f"could not resolve the vault ({e})")
    if vault_root is None or mem_root is None:
        return Check(
            name, "WARN",
            "no vault resolves on this machine — nothing to validate these "
            "pointers against (set one via `python3 scripts/agentm_config.py --vault-path`)",
        )
    try:
        cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return Check(name, "WARN", f"{config_path} unreadable ({e})")
    if not isinstance(cfg, dict):
        return Check(name, "WARN", f"{config_path} is not a JSON object")

    roots = {"vault": Path(vault_root), "memory": Path(mem_root)}
    problems: list = []
    checked = 0
    for key, surface in _PROJECT_JSON_PATH_KEYS:
        raw = _dotted(cfg, key)
        if raw is None:
            continue  # not every project.json carries every key
        checked += 1
        target, root = Path(os.path.expanduser(raw)), roots[surface]
        if not target.exists():
            problems.append(f"{key} → {raw} (does not exist)")
        elif not _is_under(target, root):
            problems.append(f"{key} → {raw} (exists, but outside {root} — stale pointer, reads as valid)")
    if not checked:
        return Check(name, "OK", f"{config_path} carries no vault pointers to validate")
    if problems:
        return Check(
            name, "FAIL",
            f"{config_path}: " + "; ".join(problems)
            + f" — re-point at the resolved vault ({mem_root})",
        )
    return Check(name, "OK", f"{checked} vault pointer(s) in {config_path} resolve inside {vault_root}")


def _main_worktree_root(repo: Path) -> Optional[Path]:
    """The main clone's root, for a `repo` that may be a linked worktree.
    `--git-common-dir` points at the shared `.git` in every worktree; its
    parent is the checkout that owns the gitignored `.harness/`. None when
    git can't answer."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = out.stdout.strip()
    if out.returncode != 0 or not raw:
        return None
    common = Path(raw)
    if not common.is_absolute():
        common = (repo / common).resolve()
    return common.parent


def project_json_configs(repo: Path, *, mem_root: Optional[Path] = None) -> list:
    """Every `project.json` this repo's config is reachable through: the
    repo-local `.harness/` one, plus the vault-resident one for the same
    project slug (post-V4-#26 both exist and both are read by callers, so a
    row that covered only one would miss the other's drift).

    `.harness/` is gitignored, so a linked worktree does not carry the file —
    and agentm runs worktree-native by default. Falling back to the main
    clone keeps the row present in a worktree session instead of silently
    emitting nothing, which is the exact failure shape this check exists for.
    """
    found: list = []
    local = repo / ".harness" / "project.json"
    slug = repo.name
    if not local.is_file():
        main_root = _main_worktree_root(repo)
        if main_root is not None and main_root != repo:
            candidate = main_root / ".harness" / "project.json"
            if candidate.is_file():
                local, slug = candidate, main_root.name
    if local.is_file():
        found.append((local, "repo"))
        try:
            cfg = json.loads(local.read_text(encoding="utf-8"))
            if isinstance(cfg, dict) and isinstance(cfg.get("vault_project"), str):
                slug = cfg["vault_project"]
        except (OSError, ValueError):
            pass
    # Read the layout constant unconditionally, so an injected `mem_root`
    # (tests) traverses the same relative path production does.
    try:
        import harness_memory as _hm  # noqa: PLC0415
        projects_rel = getattr(_hm, "_VAULT_PROJECTS_REL_NEW", "desk/projects")
        if mem_root is None:
            mem_root = _hm.memory_root()
    except Exception:  # noqa: BLE001 — no vault reachable; the local row still stands
        return found
    if mem_root is None:
        return found
    # Filing-v2 2b: the vault-root `Projects/` sibling is the newest generation;
    # probe it first, then the memory-root layout the constant names.
    import harness_memory as _hm_rs  # noqa: PLC0415 — the one root-space predicate
    candidates = []
    root_space = _hm_rs._root_projects_dir(Path(mem_root))
    if root_space is not None:
        candidates.append(root_space / slug / "_harness" / "project.json")
    candidates.append(Path(mem_root).joinpath(*projects_rel.split("/"), slug, "_harness", "project.json"))
    for vault_cfg in candidates:
        if vault_cfg.is_file():
            found.append((vault_cfg, "vault"))
            break
    return found


# ── composition ───────────────────────────────────────────────────────────
def run_inventory(
    repo: Optional[Path] = None, *, state_root: Optional[Path] = None,
    telemetry_root: Optional[Path] = None,
    install_prefix: Optional[Path] = None,
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
    for job_name, keys_label in _JOB_CONFIG_CHECKS:
        checks.append(check_job_config(repo, job_name, keys_label, install_prefix=install_prefix))
    checks.append(check_unattended_merge_gate(repo))
    checks.append(check_memory_hook_interpreter(repo))
    checks.append(check_storage_rules())
    for config_path, label in project_json_configs(repo):
        checks.append(check_project_json_pointers(config_path, label))
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
