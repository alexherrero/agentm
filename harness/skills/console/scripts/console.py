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

Read-only report: never mutates repo, vault, or board state. Every section
degrades to a one-line "n/a: <reason>" instead of raising whenever a dev-only
surface (health/spend/board-drift) is absent -- e.g. this isn't agentm's own
dev checkout, or a crickets sibling isn't reachable.

Usage:
    python3 console.py                          # terminal report
    python3 console.py --html [--output PATH]    # extend the static console page
"""
from __future__ import annotations

import argparse
import html as html_lib
import io
import os
import subprocess
import sys
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
def section_health(repo_root: "Path | None", *, runner=subprocess.run) -> str:
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
    return out.strip() or "Health: (empty report)"


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

    NOTE: `harness/skills/memory/scripts/orchestration_briefing.py`'s own
    `count_inbox()` looks at `<vault>/_inbox` (no `personal/` segment) --
    confirmed by direct inspection to be a pre-existing mismatch against the
    live vault layout, not touched here (out of scope for this plan; see
    PLAN-cons-7-console-v1.md's Notes). This function reads the real path
    directly so the console doesn't silently propagate that miscount."""
    d = vault / "personal" / "_inbox"
    if not d.is_dir():
        return 0
    try:
        return sum(1 for p in d.glob("*.md") if p.is_file() and p.name.lower() not in _INBOX_SKIP)
    except OSError:
        return 0


def count_incubator(vault: Path) -> int:
    """Real vault layout: `<vault>/_idea-incubator/<slug>/` (root-level).

    NOTE: `orchestration_briefing.py`'s own `count_incubator_pending()` looks
    at `<vault>/personal/_idea-incubator` -- the opposite-direction mismatch
    of `count_inbox`'s, same pre-existing bug, not touched here."""
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


# ── report assembly ───────────────────────────────────────────────────────────
def gather_report(repo_root: "Path | None" = None, vault: "Path | None" = None, *, runner=subprocess.run) -> dict:
    return {
        "health": section_health(repo_root, runner=runner),
        "plans": section_plans(repo_root, runner=runner),
        "board_drift": section_board_drift(repo_root, runner=runner),
        "spend": section_spend(repo_root, runner=runner),
        "memory": section_memory(vault),
    }


def render_terminal(report: dict) -> str:
    lines = ["AgentM Console", "=" * len("AgentM Console"), ""]
    for title, key in (
        ("Health", "health"), ("Plans", "plans"),
        ("Board drift", "board_drift"), ("Spend", "spend"),
        ("Memory activity", "memory"),
    ):
        lines.append(f"-- {title} --")
        lines.append(report[key])
        lines.append("")
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

<section><h2>Health</h2><pre>{esc(report['health'])}</pre></section>
<section><h2>Plans</h2><pre>{esc(report['plans'])}</pre></section>
<section><h2>Board drift</h2><pre>{esc(report['board_drift'])}</pre></section>
<section><h2>Spend</h2>{_spend_html_fragment(repo_root, runner=runner)}</section>
<section><h2>Memory activity</h2><pre>{esc(report['memory'])}</pre></section>

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
    print(render_terminal(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
