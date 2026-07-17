#!/usr/bin/env python3
"""console.py — the /console skill's composition script (CONS-7, Consolidation
arc Wave 2, CONSOLIDATION-VERDICT.md Ruling 7 — "the management dashboard as a
product").

Builds nothing new underneath: every section below reads or shells out to a
surface that already exists and is independently invocable on its own --

    Health          -> scripts/status.py (reads health_score.py's history.jsonl)
    Plans           -> scripts/queue_status_lite.py
    Board drift     -> crickets' check_project_sync.py, the read-only detector
                       (never report_drift.py, which posts a GitHub comment)
    Spend           -> scripts/health/observability_console.py's rollup,
                       refreshed on demand by scripts/runner/aggregator.py
    Memory activity -> harness/skills/memory/scripts/{recall.py heat-policy,
                       watchlist_review.py} + direct vault directory counts
    Machinery       -> scripts/machinery_doctor.py (Consolidation follow-ups
                       batch, machinery-integrity lane, piece 3)
    Vault doctor    -> crickets' src/obsidian-vault/scripts/doctor_vault.py
    Vault lint      -> harness/skills/memory/scripts/vault_lint.py's --audit
                       report under <vault>/_meta/ (piece 1's scheduled job)
    Dreaming        -> <vault>/_meta/dream-auto-expired-latest.json, the
                       stable pointer dream.py's auto-apply wrapper writes
                       every cycle (piece 5)

Read-only report: never mutates repo, vault, or board state. Every section
degrades to a one-line "n/a: <reason>" instead of raising whenever a dev-only
surface (health/spend/board-drift) is absent -- e.g. this isn't agentm's own
dev checkout, or a crickets sibling isn't reachable. Freshness-bearing
sections (health, vault doctor, vault lint, dreaming) follow an
honest-dark convention -- a check that has genuinely never run says so
plainly ("dark" / "never run") rather than being omitted, matching
scripts/health/dark-checks.jsonl's own verified-live / explicit-dark /
never-silently-absent shape.

Usage:
    python3 console.py                          # terminal report
    python3 console.py --html [--output PATH]    # extend the static console page
"""
from __future__ import annotations

import argparse
import html as html_lib
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent  # harness/skills/console/scripts/ (or the installed equivalent)


# ── repo / vault / sibling resolution ────────────────────────────────────────
def find_repo_root(start: "Path | None" = None) -> "Path | None":
    """Walk up from `start` (default cwd) looking for the agentm dev-checkout
    marker (`scripts/check-all.sh`). Returns None outside an agentm dev
    checkout -- the health/plans/spend/board-drift sections degrade
    gracefully in that case; memory activity works independently (it only
    needs a resolvable vault, which ships to every install)."""
    p = Path(start or Path.cwd()).resolve()
    for candidate in (p, *p.parents):
        if (candidate / "scripts" / "check-all.sh").is_file():
            return candidate
    return None


def find_crickets_root() -> "Path | None":
    """`$CRICKETS_SCRIPTS_DIR`'s repo root if set (mirrors
    scripts/runner/aggregator.py's own override convention), else the
    conventional sibling checkout `~/Antigravity/crickets`."""
    env = os.environ.get("CRICKETS_SCRIPTS_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
        # env may point at the tokens scripts dir itself (aggregator's own
        # convention) or at a repo root -- accept either by walking up to
        # find a `src/` sibling.
        for candidate in (p, *p.parents):
            if (candidate / "src" / "github-projects").is_dir():
                return candidate
        return None
    default = Path.home() / "Antigravity" / "crickets"
    return default if default.is_dir() else None


def _memory_scripts_dir() -> "Path | None":
    """The `memory` compound skill's scripts/, resolved as a sibling of this
    skill's own install location -- `<skills>/console/scripts/../../memory/
    scripts` in both source-tree and installed-tree layouts, since `console`
    and `memory` are installed side by side under the same `skills/` parent
    on every host (Claude Code `.claude/skills/`, Antigravity `.agents/
    skills/`). No dependency on an agentm dev checkout."""
    candidate = _HERE.parent.parent / "memory" / "scripts"
    return candidate if candidate.is_dir() else None


def resolve_vault_path() -> "Path | None":
    """Reuse harness_memory.vault_path() when this is an agentm dev checkout
    (the canonical resolver); fall back to $MEMORY_VAULT_PATH directly
    otherwise -- same resolution order every other memory script honors."""
    repo_root = find_repo_root()
    if repo_root is not None:
        scripts_dir = repo_root / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        try:
            import harness_memory as hm  # type: ignore

            p = hm.vault_path()
            if p is not None:
                return p
        except Exception:
            pass
    env = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    return None


# ── freshness formatting (shared by health / vault-doctor / vault-lint /
#    dreaming sections -- the "last-fired timestamp" half of honest-dark) ────
def _format_age(ts: float, *, now: "float | None" = None) -> str:
    """`<UTC timestamp> (<relative age> ago)` -- e.g. `2026-07-10 06:03Z
    (18.4h ago)`. `now` is injectable for deterministic tests."""
    now = now if now is not None else time.time()
    when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    delta = max(0.0, now - ts)
    if delta < 3600:
        age = f"{int(delta // 60)}m ago"
    elif delta < 86400:
        age = f"{delta / 3600:.1f}h ago"
    else:
        age = f"{delta / 86400:.1f}d ago"
    return f"{when} ({age})"


# ── subprocess helper (injectable for hermetic tests) ────────────────────────
def _run(cmd: list, *, cwd: "Path | None" = None, timeout: int = 30, runner=subprocess.run):
    """Best-effort subprocess run. Returns (returncode, stdout, stderr); on any
    OSError/timeout, returncode is None (never raises) so a section degrades
    to "n/a" instead of blocking the whole report."""
    try:
        proc = runner(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return None, "", str(e)


# ── sections: health / plans / board drift / spend ───────────────────────────
def _health_history_path(repo_root: Path, *, resolve_fn=None) -> Path:
    """`health_score.resolve_history_path()`, imported from the repo's own
    `scripts/health/` -- the vault-resolved location (device-local fallback
    for vault-less installs) since V8 proving Lane S, 2026-07-13. Falls back
    to the pre-localization repo path if the module can't be imported (e.g.
    a stale/partial checkout), so this stays best-effort like every other
    console.py section. `resolve_fn` is injectable (tests pass one to avoid
    the real health_score module -- possibly already cached in sys.modules
    by another test file -- winning over a fixture's fake repo_root)."""
    if resolve_fn is not None:
        return resolve_fn()
    health_dir = repo_root / "scripts" / "health"
    if str(health_dir) not in sys.path:
        sys.path.insert(0, str(health_dir))
    try:
        import health_score as hs  # type: ignore

        return hs.resolve_history_path()
    except Exception:
        return repo_root / "scripts" / "health" / "history.jsonl"


def _read_health_history_ts(repo_root: Path, *, resolve_fn=None) -> "float | None":
    """The health-scorecard's last-recorded `ts` (epoch seconds), read
    directly from the resolved history ledger's last row -- a plain file
    read rather than calling health_score.read_latest_history_row(), so a
    malformed or missing ledger degrades to None instead of a raised
    exception."""
    path = _health_history_path(repo_root, resolve_fn=resolve_fn)
    if not path.is_file():
        return None
    last_line = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last_line = line
    except OSError:
        return None
    if last_line is None:
        return None
    try:
        row = json.loads(last_line)
    except json.JSONDecodeError:
        return None
    ts = row.get("ts") if isinstance(row, dict) else None
    return float(ts) if isinstance(ts, (int, float)) else None


def section_health(repo_root: "Path | None", *, runner=subprocess.run, now: "float | None" = None, resolve_fn=None) -> str:
    if repo_root is None:
        return "Health: n/a (not an agentm dev checkout)"
    rc, out, err = _run([sys.executable, "scripts/status.py"], cwd=repo_root, runner=runner)
    if rc is None:
        return f"Health: n/a ({err})"
    if rc != 0:
        return (
            "Health: no scorecard history yet -- run `bash scripts/health/run-fast-tier.sh "
            "| python3 scripts/health/health_score.py --history` at least once"
        )
    text = out.strip() or "Health: (empty report)"
    ts = _read_health_history_ts(repo_root, resolve_fn=resolve_fn)
    if ts is None:
        # Honest-dark: status.py above printed a real row (rc == 0), so this
        # is a genuinely unexpected shape (a ts-less legacy row, or the
        # ledger vanishing between the two reads) rather than "never run" --
        # said plainly rather than silently omitted.
        return text + "\nLast nightly run: unknown (no ts on the latest history.jsonl row)"
    return text + f"\nLast nightly run: {_format_age(ts, now=now)}"


def section_plans(repo_root: "Path | None", *, runner=subprocess.run) -> str:
    if repo_root is None:
        return "Plans: n/a (not an agentm dev checkout)"
    rc, out, err = _run([sys.executable, "scripts/queue_status_lite.py"], cwd=repo_root, runner=runner)
    if rc is None:
        return f"Plans: n/a ({err})"
    return out.strip() or "Plans: no active plans."


def section_board_drift(repo_root: "Path | None", *, runner=subprocess.run) -> str:
    crickets_root = find_crickets_root()
    if crickets_root is None:
        return "Board drift: n/a (no crickets sibling checkout found)"
    checker = crickets_root / "src" / "github-projects" / "scripts" / "check_project_sync.py"
    if not checker.is_file():
        return "Board drift: n/a (github-projects plugin not found in the crickets checkout)"
    cwd = repo_root if repo_root is not None else Path.cwd()
    rc, out, err = _run([sys.executable, str(checker)], cwd=cwd, runner=runner)
    if rc is None:
        return f"Board drift: n/a ({err})"
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return lines[0] if lines else "Board drift: (no output)"


def section_spend(repo_root: "Path | None", *, runner=subprocess.run) -> str:
    data = _spend_data(repo_root, runner=runner)
    if data is None:
        return _spend_unavailable_reason(repo_root, runner=runner)
    return (
        f"Spend: ${data['total_spend_usd']:.4f} total across {data['plan_count']} plan(s) "
        f"(${data['cost_per_plan_usd']:.4f}/plan)"
    )


def _spend_modules(repo_root: Path):
    scripts_dir = repo_root / "scripts"
    for p in (scripts_dir, scripts_dir / "health"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from runner import aggregator as agg  # type: ignore
    import observability_console as oc  # type: ignore

    return agg, oc


def _spend_data(repo_root: "Path | None", *, runner=subprocess.run) -> "dict | None":
    if repo_root is None:
        return None
    # Best-effort refresh so the report reflects the latest telemetry; a
    # refresh failure (most commonly: no crickets sibling to reuse
    # analyzer._compute_windows()) degrades to whatever rollup already
    # exists on disk, if any -- never raises.
    _run([sys.executable, "-m", "runner.aggregator"], cwd=repo_root / "scripts", runner=runner)
    try:
        agg, oc = _spend_modules(repo_root)
    except ImportError:
        return None
    db_path = agg.default_db_path()
    if not db_path.is_file():
        return None
    try:
        return oc.compute_console_data(db_path)
    except ValueError:
        return None


def section_runner_jobs(repo_root: "Path | None", *, state_root: "Path | None" = None,
                         now: "float | None" = None) -> str:
    """Per-registered-job honesty surface (2026-07-17 finding: a launchd-
    triggered runner cycle can go dark for days -- wrong CWD-relative job
    discovery on this machine was one cause -- with zero visible error, and
    separately, `cycle.py`'s lookback re-anchor writes a "status": "done"
    marker that is byte-identical to a real completion in every field except
    `last_real_run`/`missed`). Reports each non-dry-run registered job's last
    GENUINE execution, flagged when the due-clock has been silently
    re-anchored past it without one."""
    if repo_root is None:
        return "Runner jobs: n/a (not an agentm dev checkout)"
    scripts_dir = repo_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from runner import manifest as rmanifest
        from runner import state as rstate
    except ImportError as e:
        return f"Runner jobs: n/a ({e})"
    try:
        jobs = rmanifest.load_manifests(repo_root / ".harness" / "jobs")
    except rmanifest.ManifestError as e:
        return f"Runner jobs: n/a ({e})"
    real_jobs = [j for j in jobs if not j.dry_run]
    if not real_jobs:
        return "Runner jobs: none registered (or all still dry-run) under .harness/jobs/"
    now = now if now is not None else time.time()
    lines = []
    for job in sorted(real_jobs, key=lambda j: j.name):
        marker = rstate.read_marker(job.name, state_root=state_root)
        real_ts = rstate.last_real_run_epoch(marker)
        if real_ts is None:
            lines.append(f"  {job.name}: never run for real")
            continue
        line = f"  {job.name}: last real run {_format_age(real_ts, now=now)}"
        if rstate.was_last_advance_a_miss(marker):
            line += " -- OVERDUE (re-anchored past its lookback without running)"
        lines.append(line)
    return "Runner jobs:\n" + "\n".join(lines)


def _spend_unavailable_reason(repo_root: "Path | None", *, runner=subprocess.run) -> str:
    if repo_root is None:
        return "Spend: n/a (not an agentm dev checkout)"
    try:
        agg, oc = _spend_modules(repo_root)
    except ImportError as e:
        return f"Spend: n/a ({e})"
    db_path = agg.default_db_path()
    if not db_path.is_file():
        return "Spend: n/a (no rollup yet -- run `python3 -m runner.aggregator` from scripts/)"
    try:
        oc.compute_console_data(db_path)
    except ValueError as e:
        return f"Spend: n/a ({e})"
    return "Spend: n/a"


# ── section: memory activity ─────────────────────────────────────────────────
_INBOX_SKIP = {"_index.md", "readme.md", "_readme.md"}
_CURATED_SKIP_DIRS = {"_inbox", "_skill-watchlist", "_watchlist", "_archive"}


def count_inbox(vault: Path) -> int:
    """Real vault layout: `<vault>/personal/_inbox/*.md`.

    NOTE: `harness/skills/memory/scripts/orchestration_briefing.py` carries
    its own independent `count_inbox()` -- historically it read `<vault>/
    _inbox` (no `personal/` segment), a mismatch against the live vault
    layout that this function deliberately avoided by reading the real path
    directly. That mismatch has since been fixed there too, so the two
    implementations are now duplicates that happen to agree; this one is
    left as-is rather than importing the other, to keep console.py's own
    dependency footprint self-contained."""
    d = vault / "personal" / "_inbox"
    if not d.is_dir():
        return 0
    try:
        return sum(1 for p in d.glob("*.md") if p.is_file() and p.name.lower() not in _INBOX_SKIP)
    except OSError:
        return 0


def count_incubator(vault: Path) -> int:
    """Real vault layout: `<vault>/_idea-incubator/<slug>/` (root-level).

    NOTE: `orchestration_briefing.py`'s own `count_incubator_pending()` used
    to look at `<vault>/personal/_idea-incubator` -- the opposite-direction
    mismatch of `count_inbox`'s, now fixed there as well. Same historical
    duplicate-that-now-agrees situation; see `count_inbox`'s NOTE above."""
    d = vault / "_idea-incubator"
    if not d.is_dir():
        return 0
    try:
        return sum(1 for p in d.iterdir() if p.is_dir() and not p.name.startswith("_"))
    except OSError:
        return 0


def watchlist_summary(vault: "Path | None") -> str:
    if vault is None:
        return "Watchlist: n/a (no vault resolved)"
    mem_dir = _memory_scripts_dir()
    if mem_dir is None:
        return "Watchlist: n/a (memory skill not installed alongside console)"
    if str(mem_dir) not in sys.path:
        sys.path.insert(0, str(mem_dir))
    try:
        import watchlist_review as wr  # type: ignore
    except ImportError as e:
        return f"Watchlist: n/a ({e})"
    try:
        entries = wr.list_watchlist_entries(vault)
    except OSError as e:
        return f"Watchlist: n/a ({e})"
    pending = [
        e for e in entries
        if e["frontmatter"].get("status", "").strip().lower() in ("", "pending-review")
    ]
    high_pending = sum(
        1 for e in pending
        if e["frontmatter"].get("evaluator_classification", "").strip().upper() == "HIGH"
    )
    return f"Watchlist: {len(entries)} entries ({len(pending)} pending, {high_pending} HIGH)"


def newest_curated_entries(vault: Path, n: int = 5) -> list:
    personal = vault / "personal"
    if not personal.is_dir():
        return []
    candidates = []
    try:
        for p in personal.rglob("*.md"):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(personal)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] in _CURATED_SKIP_DIRS:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, rel.as_posix()))
    except OSError:
        return []
    candidates.sort(reverse=True)
    return [name for _, name in candidates[:n]]


def heat_policy_report(vault: "Path | None") -> str:
    if vault is None:
        return "Heat-policy: n/a (no vault resolved)"
    mem_dir = _memory_scripts_dir()
    if mem_dir is None:
        return "Heat-policy: n/a (memory skill not installed alongside console)"
    if str(mem_dir) not in sys.path:
        sys.path.insert(0, str(mem_dir))
    try:
        import heat_policy as hp  # type: ignore
    except ImportError as e:
        return f"Heat-policy: n/a ({e})"
    buf = io.StringIO()
    try:
        result = hp.run_policy(vault, dry_run=True, stderr=buf)
    except Exception as e:  # never let a heat-policy surprise sink the whole report
        return f"Heat-policy: n/a ({e})"
    if result.get("too_early"):
        detail = "too early to judge (not enough sessions recorded yet)"
    else:
        detail = f"{len(result['demoted'])} candidate(s) to demote, {len(result['promoted'])} to promote"
    extras = []
    if result.get("pinned_skipped"):
        extras.append(f"{len(result['pinned_skipped'])} pinned (skipped)")
    if result.get("floor_skipped"):
        extras.append(f"{result['floor_skipped']} skipped (always-load floor)")
    if extras:
        detail += "; " + "; ".join(extras)
    return f"Heat-policy (dry-run): {detail}"


def section_memory(vault: "Path | None") -> str:
    if vault is None:
        return (
            "Memory activity: n/a (no vault resolved -- set MEMORY_VAULT_PATH or "
            "configure plugins.obsidian-vault.vault_path)"
        )
    lines = []
    inbox_n = count_inbox(vault)
    lines.append(f"Inbox: {inbox_n} unreviewed entr{'y' if inbox_n == 1 else 'ies'}")
    lines.append(watchlist_summary(vault))
    incubator_n = count_incubator(vault)
    lines.append(f"Incubator: {incubator_n} idea{'' if incubator_n == 1 else 's'} in research")
    newest = newest_curated_entries(vault)
    lines.append("Newest curated entries: " + (", ".join(newest) if newest else "none found"))
    lines.append(heat_policy_report(vault))
    return "\n".join(lines)


# ── section: machinery integrity (Consolidation follow-ups batch, piece 3) ──
def section_machinery(repo_root: "Path | None", *, runner=subprocess.run) -> str:
    """Composes over `scripts/machinery_doctor.py` (piece 2) rather than
    re-deriving its checks -- the console's job is to surface the summary
    + every non-OK row, not to re-implement the inventory."""
    if repo_root is None:
        return "Machinery: n/a (not an agentm dev checkout)"
    rc, out, err = _run(
        [sys.executable, "scripts/machinery_doctor.py", "--format", "json"], cwd=repo_root, runner=runner
    )
    if rc is None:
        return f"Machinery: n/a ({err})"
    try:
        payload = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return "Machinery: n/a (unparseable machinery_doctor.py output)"
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    checks = payload.get("checks", []) if isinstance(payload, dict) else []
    line = (
        f"Machinery: {summary.get('OK', 0)} OK, {summary.get('WARN', 0)} WARN, "
        f"{summary.get('FAIL', 0)} FAIL, {summary.get('UNVERIFIED', 0)} UNVERIFIED"
    )
    concerning = [c for c in checks if isinstance(c, dict) and c.get("status") in ("FAIL", "UNVERIFIED")]
    if concerning:
        detail_lines = [f"  [{c.get('status')}] {c.get('name')}: {c.get('detail')}" for c in concerning]
        line += "\n" + "\n".join(detail_lines)
    return line


# ── section: vault doctor (Consolidation follow-ups batch, piece 3b) ────────
def section_vault_doctor(vault: "Path | None", *, runner=subprocess.run, now: "float | None" = None) -> str:
    """A live check, run fresh every console invocation (unlike vault-lint's
    dated report below) -- `doctor_vault.py` has no persisted run history of
    its own, so "when it ran" is honestly "just now, by this console call,"
    not a stale cached timestamp."""
    crickets_root = find_crickets_root()
    if crickets_root is None:
        return "Vault doctor: n/a (no crickets sibling checkout -- obsidian-vault plugin not reachable)"
    script = crickets_root / "src" / "obsidian-vault" / "scripts" / "doctor_vault.py"
    if not script.is_file():
        return "Vault doctor: n/a (obsidian-vault plugin not found in the crickets checkout)"
    args = [sys.executable, str(script)]
    if vault is not None:
        args += ["--vault-path", str(vault)]
    rc, out, err = _run(args, runner=runner)
    if rc is None:
        return f"Vault doctor: n/a ({err})"
    lines = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("[doctor_vault]")]
    body = "\n".join(f"  {ln.strip()}" for ln in lines) if lines else "  (no output)"
    now = now if now is not None else time.time()
    checked = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    return f"Vault doctor (live check, {checked}):\n{body}"


# ── section: vault lint (Consolidation follow-ups batch, piece 1 + 3b) ──────
_VAULT_LINT_SUMMARY_RE = re.compile(r"\*\*Summary:\*\*\s*(.+)")


def _extract_vault_lint_summary(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "(unreadable report)"
    m = _VAULT_LINT_SUMMARY_RE.search(text)
    return m.group(1).strip() if m else "(summary line not found in report)"


def section_vault_lint(vault: "Path | None", *, now: "float | None" = None) -> str:
    if vault is None:
        return "Vault lint: n/a (no vault resolved)"
    meta_dir = vault / "_meta"
    reports = sorted(meta_dir.glob("vault-lint-*.md")) if meta_dir.is_dir() else []
    if not reports:
        return (
            "Vault lint: dark -- no vault-lint-*.md report under _meta/ yet "
            "(the weekly job may not be registered on this machine or has never fired; "
            "see templates/jobs/vault-lint.yaml)"
        )
    latest = reports[-1]
    summary = _extract_vault_lint_summary(latest)
    try:
        mtime = latest.stat().st_mtime
    except OSError:
        mtime = None
    when = _format_age(mtime, now=now) if mtime is not None else "unknown"
    return f"Vault lint: {summary} (report: {latest.name}, rendered {when})"


# ── section: latest brief (L1, ledger ruling 7 -- "seen" delivery home) ────
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def section_brief(vault: "Path | None", *, now: "float | None" = None) -> str:
    """Reads `<vault>/_briefs/*.md` (`inbox_digest.py` / `window_park.py`'s
    shared write target, L1/F2 fix) and reports the most recent one by
    filename -- slugs are `YYYYMMDD-...`, so lexicographic sort is
    chronological. This is the one section `render_terminal`/
    `render_html_report` put first: the Morning Brief needs to actually be
    seen, not one more entry a triage pass could bury."""
    if vault is None:
        return "Latest brief: n/a (no vault resolved)"
    briefs_dir = vault / "_briefs"
    briefs = sorted(briefs_dir.glob("*.md")) if briefs_dir.is_dir() else []
    if not briefs:
        return "Latest brief: dark -- no _briefs/*.md yet (no digest or park job has fired on this machine)"
    latest = briefs[-1]
    try:
        text = latest.read_text(encoding="utf-8")
    except OSError:
        text = ""
    m = _H1_RE.search(text)
    title = m.group(1).strip() if m else latest.stem
    try:
        mtime = latest.stat().st_mtime
    except OSError:
        mtime = None
    when = _format_age(mtime, now=now) if mtime is not None else "unknown"
    return f"Latest brief: {title} (file: {latest.name}, {when})"


# ── section: dreaming auto-expire (Consolidation follow-ups batch, piece 5) ─
def section_dream_expire(vault: "Path | None", *, now: "float | None" = None) -> str:
    """Reads the stable, always-overwritten pointer `dream.py`'s auto-apply
    wrapper writes every cycle (`_meta/dream-auto-expired-latest.json`).
    Honest-dark on every edge: no vault, no pointer file (job never run),
    or an unreadable/malformed pointer all say so plainly rather than
    omitting the line or raising."""
    if vault is None:
        return "Dreaming auto-expire: n/a (no vault resolved)"
    pointer = vault / "_meta" / "dream-auto-expired-latest.json"
    if not pointer.is_file():
        return (
            "Dreaming auto-expire: dark -- no _meta/dream-auto-expired-latest.json yet "
            "(the dreaming job may not be registered on this machine or has never fired; "
            "see templates/jobs/dream.yaml)"
        )
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return f"Dreaming auto-expire: n/a (unreadable pointer at {pointer}: {e})"
    if not isinstance(data, dict):
        return f"Dreaming auto-expire: n/a (pointer at {pointer} is not a JSON object)"
    run_id = data.get("run_id", "?")
    count = data.get("count", 0)
    applied_at = data.get("applied_at")
    when = _format_age(applied_at, now=now) if isinstance(applied_at, (int, float)) else "unknown"
    revert = data.get("revert", {}) if isinstance(data.get("revert"), dict) else {}
    how = revert.get("how", "see revert_log.py (no CLI) with this pointer's run_id")
    if not count:
        return f"Dreaming auto-expire: last cycle (run {run_id}, {when}) auto-expired 0 item(s) -- nothing to revert"
    return (
        f"Dreaming auto-expire: last cycle (run {run_id}, {when}) auto-expired {count} item(s) "
        f"-- revert: {how}"
    )


# ── section: rich (HTML) view pointer (Consolidation follow-ups batch,
#    piece 4) -- printed at the end of every terminal run, not a mode the
#    operator has to remember exists ─────────────────────────────────────
def rich_view_line(output_path: "Path | None" = None) -> str:
    path = output_path if output_path is not None else default_html_output_path()
    if not path.is_file():
        return f"Rich view: not yet rendered -- run `python3 console.py --html` to generate {path}"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return f"Rich view: {path} (last-rendered time unavailable)"
    return f"Rich view: {path} (last rendered {_format_age(mtime)})"


def scorecard_html_line(repo_root: "Path | None") -> str:
    """Points at the health scorecard's own fixed-path HTML render (V8
    proving Lane S, 2026-07-13) -- a sibling of console.html, produced by
    the local runner's daily health-pass job (`health_score.py --html`),
    never by console.py itself."""
    if repo_root is None:
        default_path = Path.home() / ".cache" / "agentm" / "telemetry" / "scorecard.html"
    else:
        health_dir = repo_root / "scripts" / "health"
        if str(health_dir) not in sys.path:
            sys.path.insert(0, str(health_dir))
        try:
            import health_score as hs  # type: ignore

            default_path = hs.default_html_output_path()
        except Exception:
            default_path = Path.home() / ".cache" / "agentm" / "telemetry" / "scorecard.html"
    if not default_path.is_file():
        return f"Scorecard (rich HTML): not yet rendered -- the local runner's daily health-pass job writes {default_path}"
    try:
        mtime = default_path.stat().st_mtime
    except OSError:
        return f"Scorecard (rich HTML): {default_path} (last-rendered time unavailable)"
    return f"Scorecard (rich HTML): {default_path} (last rendered {_format_age(mtime)})"


# ── report assembly ───────────────────────────────────────────────────────────
def gather_report(repo_root: "Path | None" = None, vault: "Path | None" = None, *, runner=subprocess.run) -> dict:
    return {
        "brief": section_brief(vault),
        "health": section_health(repo_root, runner=runner),
        "plans": section_plans(repo_root, runner=runner),
        "board_drift": section_board_drift(repo_root, runner=runner),
        "spend": section_spend(repo_root, runner=runner),
        "runner_jobs": section_runner_jobs(repo_root),
        "memory": section_memory(vault),
        "machinery": section_machinery(repo_root, runner=runner),
        "vault_doctor": section_vault_doctor(vault, runner=runner),
        "vault_lint": section_vault_lint(vault),
        "dream_expire": section_dream_expire(vault),
    }


def render_terminal(report: dict, *, html_path: "Path | None" = None, repo_root: "Path | None" = None) -> str:
    lines = ["AgentM Console", "=" * len("AgentM Console"), ""]
    for title, key in (
        ("Latest brief", "brief"),
        ("Health", "health"), ("Plans", "plans"),
        ("Board drift", "board_drift"), ("Spend", "spend"),
        ("Runner jobs", "runner_jobs"),
        ("Memory activity", "memory"), ("Machinery", "machinery"),
        ("Vault doctor", "vault_doctor"), ("Vault lint", "vault_lint"),
        ("Dreaming", "dream_expire"),
    ):
        if key not in report:
            continue
        lines.append(f"-- {title} --")
        lines.append(report[key])
        lines.append("")
    # Piece 4: the rich HTML view link/path is always one command away --
    # printed at the end of every terminal run, not a mode the operator has
    # to remember exists.
    lines.append(rich_view_line(html_path))
    lines.append(scorecard_html_line(repo_root))
    return "\n".join(lines).rstrip() + "\n"


def _extract_body(full_html: str) -> str:
    """Pull the `<body>...</body>` contents out of a full HTML document --
    used to embed observability_console.py's exact rendered spend tables
    (byte-for-byte, not re-implemented) inside the composed console page."""
    start = full_html.find("<body>")
    end = full_html.find("</body>")
    if start == -1 or end == -1:
        return full_html
    inner = full_html[start + len("<body>"):end]
    # The nested page's own <h1> would duplicate our own "Spend" <h2> --
    # drop just that one line; everything else renders exactly as-is.
    inner = inner.replace("<h1>AgentM Observability Console</h1>", "", 1)
    return inner.strip()


def _spend_html_fragment(repo_root: "Path | None", *, runner=subprocess.run) -> str:
    data = _spend_data(repo_root, runner=runner)
    if data is None:
        reason = _spend_unavailable_reason(repo_root, runner=runner)
        return f"<p><em>{html_lib.escape(reason)}</em></p>"
    _, oc = _spend_modules(repo_root)  # already imported successfully by _spend_data
    return _extract_body(oc.render_html(data))


def render_html_report(report: dict, repo_root: "Path | None", *, runner=subprocess.run) -> str:
    esc = html_lib.escape
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>AgentM Console</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 2em auto; color: #222; }}
  h1 {{ font-size: 1.4em; }}
  h2 {{ font-size: 1.1em; margin-top: 2em; }}
  section {{ margin-bottom: 1.5em; }}
  pre {{ background: #f5f5f5; padding: 0.6em; border-radius: 4px; white-space: pre-wrap; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 0.5em; }}
  th, td {{ text-align: left; padding: 0.3em 0.6em; border-bottom: 1px solid #ddd; }}
  th {{ background: #f5f5f5; }}
</style>
</head>
<body>
<h1>AgentM Console</h1>

<section><h2>Latest brief</h2><pre>{esc(report.get('brief', ''))}</pre></section>
<section><h2>Health</h2><pre>{esc(report['health'])}</pre></section>
<section><h2>Plans</h2><pre>{esc(report['plans'])}</pre></section>
<section><h2>Board drift</h2><pre>{esc(report['board_drift'])}</pre></section>
<section><h2>Spend</h2>{_spend_html_fragment(repo_root, runner=runner)}</section>
<section><h2>Runner jobs</h2><pre>{esc(report.get('runner_jobs', ''))}</pre></section>
<section><h2>Memory activity</h2><pre>{esc(report['memory'])}</pre></section>
<section><h2>Machinery</h2><pre>{esc(report.get('machinery', ''))}</pre></section>
<section><h2>Vault doctor</h2><pre>{esc(report.get('vault_doctor', ''))}</pre></section>
<section><h2>Vault lint</h2><pre>{esc(report.get('vault_lint', ''))}</pre></section>
<section><h2>Dreaming</h2><pre>{esc(report.get('dream_expire', ''))}</pre></section>
<section><h2>Scorecard</h2><pre>{esc(scorecard_html_line(repo_root))}</pre></section>

</body>
</html>
"""


def default_html_output_path() -> Path:
    return Path.home() / ".cache" / "agentm" / "telemetry" / "console.html"


# ── CLI ───────────────────────────────────────────────────────────────────────
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="console", description="Compose agentm's observability surfaces into one report.",
    )
    p.add_argument("--html", action="store_true", help="render an HTML report instead of the terminal report")
    p.add_argument("--output", default=None, help="HTML output path (default: ~/.cache/agentm/telemetry/console.html)")
    p.add_argument(
        "--repo-root", default=None,
        help="override the agentm dev-checkout root (default: auto-detected from cwd)",
    )
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = build_arg_parser().parse_args(argv if argv is not None else sys.argv[1:])
    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root()
    vault = resolve_vault_path()
    report = gather_report(repo_root=repo_root, vault=vault)
    if args.html:
        out_path = Path(args.output) if args.output else default_html_output_path()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_html_report(report, repo_root), encoding="utf-8")
        print(f"console: wrote {out_path}")
        return 0
    print(render_terminal(report, repo_root=repo_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
