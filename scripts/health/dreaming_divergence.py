#!/usr/bin/env python3
"""dreaming_divergence.py — the overlap window's divergence review.

Filing v2 part 6 (task 6). While the dreaming binary runs report-only beside
the Python layer, this script runs both over a COPY of the memory root and
diffs their decisions: the lifecycle policy (demoted / revived / archive
candidates), the copy families (canonical and members), the promotion
targets and their sources, and the calendar reviews (which files would be
written, and their bytes). It writes a dated note under
`<memory root>/diagnostics/dreaming/divergence-<YYYY-MM-DD>.md` naming every
disagreement verbatim — counts and paths, no adjectives, no disposition. A
clean run says so in the same shape.

Nothing here mutates the memory root: the Python producers run against a
copy under a scratch state dir; the binary runs `-force` without `-apply`
against the same copy with its own scratch state dir.

    python3 scripts/health/dreaming_divergence.py            # print the review
    python3 scripts/health/dreaming_divergence.py --write    # also write the note
    python3 scripts/health/dreaming_divergence.py --vault <memory root> --agentmdream <binary>

Exit 0 when the layers agree, 1 when they diverge, 2 when a layer could not
run (no binary, no vault).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
for _d in (_SCRIPTS, _REPO / "scripts"):  # the producers, then harness_memory's resolver
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

DIAGNOSTICS = Path("diagnostics") / "dreaming"


def _resolve_vault(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    env = os.environ.get("MEMORY_VAULT_PATH")
    if env:
        return Path(env)
    try:
        import harness_memory  # type: ignore
        return Path(harness_memory.memory_root())
    except Exception:
        return None


def _resolve_binary(explicit: str | None) -> str | None:
    for cand in (explicit, os.environ.get("AGENTMDREAM"), shutil.which("agentmdream"),
                 str(Path.home() / ".local" / "bin" / "agentmdream")):
        if cand and Path(cand).exists():
            return cand
    return None


def _copy_root(src: Path, dst: Path) -> None:
    """The memory root, copied — notes, the sidecar, the register beside it."""
    shutil.copytree(src, dst, symlinks=False, ignore=shutil.ignore_patterns(".git", "diagnostics", ".obsidian"))
    # The register sits beside a nested memory root; carry it so both layers
    # see the same calendar.
    parent_cal = src.parent / "Calendar"
    if (src.parent / ".obsidian").is_dir() and parent_cal.is_dir() and not (src / "Calendar").exists():
        (dst.parent / ".obsidian").mkdir(exist_ok=True)
        shutil.copytree(parent_cal, dst.parent / "Calendar", symlinks=False)


def python_side(vault: Path, today: dt.date) -> dict:
    import calendar_rollups
    import consolidate
    import dream
    import lifecycle_transitions as lt

    now = today.isoformat() + "T09:00:00+00:00"
    out: dict = {}
    pol = lt.policy_pass(vault, now=now, apply=False)
    out["lifecycle"] = {
        "demoted": sorted(r for r, _ in pol.demoted),
        "revived": sorted(r for r, _ in pol.revived),
        "archive_candidates": sorted(r for r, _ in pol.archive_candidates),
    }
    entries = dream._iter_entries(vault)
    loaded = dream._load(entries)
    out["copies"] = sorted(
        ({"canonical": p.paths[0], "copies": sorted(p.paths[1:])}
         for p in dream._stage_suffix_backlog_drain(vault, entries, loaded)
         if p.paths[0].startswith("memory/")),
        key=lambda f: f["canonical"])
    episodic = sorted(str(p.relative_to(vault)).replace("\\", "/") for p in (vault / "memory" / "episodic").rglob("*.md")) \
        if (vault / "memory" / "episodic").is_dir() else []
    recurring = consolidate.find_recurring_targets(vault, episodic)
    out["promote"] = {t: sorted(s) for t, s in sorted(recurring.items())}
    written: dict = {}
    root = calendar_rollups.cf.calendar_root(vault)
    if root is not None:
        y, w, _ = today.isocalendar()
        cursor = dt.date.fromisocalendar(y, w, 1)
        for i in range(1, 9):
            monday = cursor - dt.timedelta(weeks=i)
            wy, ww, _ = monday.isocalendar()
            if calendar_rollups.week_days(wy, ww)[-1] >= today:
                continue
            key = f"{wy:04d}-W{ww:02d}"
            text = calendar_rollups.render_week(vault, wy, ww)
            target = root / f"{wy:04d}" / f"{key}-review.md"
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                written[target.name] = text
        prev = today.replace(day=1) - dt.timedelta(days=1)
        for (my, mm) in ((prev.year, prev.month), (today.year, today.month)):
            key = f"{my:04d}-{mm:02d}"
            text = calendar_rollups.render_month(vault, my, mm)
            target = root / f"{my:04d}" / f"{key}-review.md"
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                written[target.name] = text
    out["calendar"] = {"written": sorted(written), "texts": written}
    return out


def go_side(binary: str, vault: Path, state: Path) -> dict:
    env = dict(os.environ, AGENTM_STATE_DIR=str(state), AGENTM_RECALL_HISTORY=str(state / "no-recalls.jsonl"),
               XDG_CACHE_HOME=str(state / "cache"))
    # Its own scratch index path: the gate is bypassed by -force, and the live
    # index is never opened by a review.
    argv = [binary, "run", "-force", "-json", "-index", str(state / "index.db"),
            "-vault", str(vault.parent if (vault.parent / ".obsidian").is_dir() else vault)]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=600, env=env)
    if proc.returncode not in (0,):
        raise RuntimeError(f"agentmdream run failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[:400]}")
    rep = json.loads(proc.stdout or "{}")
    out: dict = {}
    plan = rep.get("plan") or {}
    out["lifecycle"] = {
        "demoted": sorted(m["rel"] for m in plan.get("demoted") or []),
        "revived": sorted(m["rel"] for m in plan.get("revived") or []),
        "archive_candidates": sorted(m["rel"] for m in plan.get("archive_candidates") or []),
    }
    out["copies"] = sorted(
        ({"canonical": f["canonical"], "copies": sorted(f.get("copies") or [])} for f in (rep.get("copies") or {}).get("families") or []),
        key=lambda f: f["canonical"])
    out["promote"] = {p["target"]: sorted(p["sources"]) for p in (rep.get("promote") or {}).get("promotions") or []}
    cal = rep.get("calendar") or {}
    out["calendar"] = {"written": sorted(cal.get("written") or []), "texts": {}}
    return out, rep


def diff(py: dict, go: dict) -> list:
    """Every disagreement, verbatim: (surface, python-only, go-only)."""
    out = []

    def sets(name, a, b):
        a, b = set(a), set(b)
        if a != b:
            out.append((name, sorted(a - b), sorted(b - a)))

    for k in ("demoted", "revived", "archive_candidates"):
        sets(f"lifecycle.{k}", py["lifecycle"][k], go["lifecycle"][k])
    sets("copies.families", [f["canonical"] + " <- " + ",".join(f["copies"]) for f in py["copies"]],
         [f["canonical"] + " <- " + ",".join(f["copies"]) for f in go["copies"]])
    sets("promote.targets", [t + " <- " + ",".join(s) for t, s in py["promote"].items()],
         [t + " <- " + ",".join(s) for t, s in go["promote"].items()])
    sets("calendar.written", py["calendar"]["written"], go["calendar"]["written"])
    return out


def render(today: dt.date, vault: Path, binary: str, py: dict, go: dict, divergences: list, go_report: dict) -> str:
    lines = ["---", "kind: report", "report: dreaming-divergence", "status: active", "altitude: artifact",
             f"created: {today.isoformat()}", f"updated: {today.isoformat()}", "tags: [dreaming, divergence, overlap]",
             "group: diagnostics", f"slug: divergence-{today.isoformat()}", "generated_by: dreaming_divergence.py", "---", "",
             f"# Dreaming divergence — {today.isoformat()}", "",
             f"The Python layer and `{Path(binary).name}` (report-only) over a copy of `{vault}`. "
             "Every decision the two disagree on, verbatim. Disposition is the operator's, in writing, before the flip.", "",
             "## Agreement", "",
             f"- lifecycle: python demoted {len(py['lifecycle']['demoted'])}, revived {len(py['lifecycle']['revived'])}, "
             f"archive candidates {len(py['lifecycle']['archive_candidates'])}; go demoted {len(go['lifecycle']['demoted'])}, "
             f"revived {len(go['lifecycle']['revived'])}, archive candidates {len(go['lifecycle']['archive_candidates'])}",
             f"- copies: python {len(py['copies'])} famil{'y' if len(py['copies']) == 1 else 'ies'}, go {len(go['copies'])}",
             f"- promote: python {len(py['promote'])} target(s), go {len(go['promote'])}",
             f"- calendar: python would write {len(py['calendar']['written'])}, go {len(go['calendar']['written'])}",
             f"- go-only jobs this pass (no Python twin): mocs {len((go_report.get('mocs') or {}).get('pages') or [])} page(s), "
             f"dates {len((go_report.get('dates') or {}).get('glossed') or [])} gloss(es), "
             f"refile {len((go_report.get('refile') or {}).get('moves') or [])} move(s)", ""]
    if not divergences:
        lines += ["## Divergences", "", "None. The two layers made the same decisions.", ""]
    else:
        lines += ["## Divergences", ""]
        for name, py_only, go_only in divergences:
            lines.append(f"### {name}")
            lines.append("")
            for x in py_only:
                lines.append(f"- python only: `{x}`")
            for x in go_only:
                lines.append(f"- go only: `{x}`")
            lines.append("")
    lines.append(f"{len(divergences)} surface(s) diverged.")
    return "\n".join(lines).rstrip("\n") + "\n"


def review(vault: Path, binary: str, today: dt.date) -> tuple:
    work = Path(tempfile.mkdtemp(prefix="dreaming-divergence-"))
    try:
        copy = work / "vault" / vault.name
        copy.parent.mkdir(parents=True)
        _copy_root(vault, copy)
        os.environ["AGENTM_STATE_DIR"] = str(work / "py-state")
        py = python_side(copy, today)
        go, rep = go_side(binary, copy, work / "go-state")
        return py, go, rep, diff(py, go)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the overlap window's divergence review: Python layer vs the dreaming binary, report-only")
    ap.add_argument("--vault", help="the memory root (default: $MEMORY_VAULT_PATH, then the kernel config)")
    ap.add_argument("--agentmdream", help="the binary (default: $AGENTMDREAM, PATH, ~/.local/bin)")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--write", action="store_true", help="write the note under <memory root>/diagnostics/dreaming/")
    a = ap.parse_args(argv)
    vault = _resolve_vault(a.vault)
    binary = _resolve_binary(a.agentmdream)
    if vault is None or not vault.is_dir():
        print("dreaming_divergence: no memory root", file=sys.stderr)
        return 2
    if binary is None:
        print("dreaming_divergence: no agentmdream binary", file=sys.stderr)
        return 2
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    py, go, rep, divergences = review(vault, binary, today)
    text = render(today, vault, binary, py, go, divergences, rep)
    print(text, end="")
    if a.write:
        target = vault / DIAGNOSTICS / f"divergence-{today.isoformat()}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"written {target}")
    return 1 if divergences else 0


if __name__ == "__main__":
    sys.exit(main())
