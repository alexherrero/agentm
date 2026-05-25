#!/usr/bin/env python3
"""harness_memory — auto-context dispatcher for harness phases.

Wires MemoryVault read + write into each harness phase command
(`/setup`, `/plan`, `/work`, `/review`, `/release`, `/bugfix`). Phase specs
invoke this CLI unconditionally; the dispatcher graceful-skips when
MemoryVault is not installed (`MEMORY_VAULT_PATH` env unset or directory
missing), so the harness runs the same on systems with or without the
sibling `crickets` install.

Four sub-commands:

    recall              — phase-specific context-load for the agent's prompt
    offer-save          — preview + ask + dispatch to /memory save (self-modulating
                          by --confidence: ≥ threshold auto-saves silently)
    plan-done-promotion — dumps progress.md tail past a byte cursor; advances cursor
    available           — exit 0 if vault accessible, 1 otherwise

Stdlib-only. Cross-platform via pathlib + subprocess. No third-party deps.

Env vars consulted:

    MEMORY_VAULT_PATH                       — root of MemoryVault (required for non-skip)
    HARNESS_AUTO_SAVE_MODE                  — ask | silent | off (default: ask)
    HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD  — float 0–1 (default: 0.8)
    HARNESS_RECALL_BUDGET_<PHASE>           — int tokens per-phase (defaults below)
    HARNESS_MEMORY_TOOLKIT_PATH             — override toolkit memory scripts dir
                                              (used by tests; auto-detect otherwise)

Default recall budgets (tokens, approximate by chars/4):

    setup   = 4000
    plan    = 6000
    work    = 6000
    review  = 4000
    release = 6000
    bugfix  = 6000

Per-phase recall query templates live in `_RECALL_QUERIES`. Per-phase
permanence rules live in `_PERMANENT_ONLY_DIRS`.

See agentm ROADMAP #8 + ADR 0009 (lands in plan #8 task 9).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Allow direct import of vault_project (same scripts/ dir).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vault_project as vp  # noqa: E402


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

_VALID_PHASES = ("setup", "plan", "work", "review", "release", "bugfix")

_DEFAULT_BUDGETS = {
    "setup": 4000,
    "plan": 6000,
    "work": 6000,
    "review": 4000,
    "release": 6000,
    "bugfix": 6000,
}

# Phase-specific subdirectory mappings under `personal-projects/<slug>/`.
# Used by recall to scope the per-project read.
_PHASE_PROJECT_DIRS = {
    "setup": ("_index.md",),  # bootstrap signal only
    "plan": ("_index.md", "decisions", "open-questions"),
    "work": ("decisions", "known-issues"),
    "review": (),  # review reads global conventions, not per-project
    "release": ("decisions",),
    "bugfix": ("known-issues",),
}

# Per-phase recall query templates. Used for the toolkit-side `recall.py query`
# subprocess call (when the toolkit is available).
_RECALL_QUERIES = {
    "setup": "operator conventions coding style tools setup",
    "plan": "decisions open questions roadmap design",
    "work": "decisions known-issues gotchas conventions",
    "review": "conventions code review patterns",
    "release": "decisions changelog what-shipped",
    "bugfix": "known-issues gotchas recurring root-cause",
}

# Always-load conventions live at personal-private/_always-load/.
_ALWAYS_LOAD_REL = ("personal-private", "_always-load")

# Cursor file relative to project root.
_CURSOR_REL = (".harness", ".promoted-progress-cursor")
_PROGRESS_REL = (".harness", "progress.md")


# -----------------------------------------------------------------------------
# Vault + toolkit discovery
# -----------------------------------------------------------------------------

def vault_path() -> Optional[Path]:
    """Return the MemoryVault root if accessible, else None.

    Resolution order: MEMORY_VAULT_PATH env > None.
    The directory must exist for the path to be returned.
    """
    raw = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_dir():
        return None
    return p


def is_available() -> bool:
    """True iff MemoryVault is accessible."""
    return vault_path() is not None


def toolkit_scripts_dir() -> Optional[Path]:
    """Locate the toolkit memory scripts dir, or None if not installed.

    Resolution order:
      1. HARNESS_MEMORY_TOOLKIT_PATH env (override, used by tests)
      2. <harness_repo>/../crickets/skills/memory/scripts/  (sibling clone)
      3. ~/Antigravity/crickets/skills/memory/scripts/      (canonical install)
    """
    override = os.environ.get("HARNESS_MEMORY_TOOLKIT_PATH", "").strip()
    if override:
        p = Path(override).expanduser()
        return p if p.is_dir() else None

    # Sibling clone — _HERE is <harness>/scripts/
    harness_root = _HERE.parent
    sibling = harness_root.parent / "crickets" / "skills" / "memory" / "scripts"
    if sibling.is_dir():
        return sibling

    canonical = Path.home() / "Antigravity" / "crickets" / "skills" / "memory" / "scripts"
    if canonical.is_dir():
        return canonical

    return None


# -----------------------------------------------------------------------------
# recall
# -----------------------------------------------------------------------------

def phase_budget(phase: str, arg_budget: Optional[int] = None) -> int:
    """Resolve recall budget for `phase`.

    Order: --budget arg > HARNESS_RECALL_BUDGET_<PHASE> env > default.
    """
    if arg_budget is not None and arg_budget > 0:
        return arg_budget
    env_key = f"HARNESS_RECALL_BUDGET_{phase.upper()}"
    raw = os.environ.get(env_key, "").strip()
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_BUDGETS.get(phase, 4000)


def _approx_token_count(text: str) -> int:
    """Approximate token count (chars / 4). Deterministic, cheap, no model needed."""
    return max(1, len(text) // 4)


def _truncate_to_budget(parts: list[str], budget: int) -> list[str]:
    """Pop entries off the end until total approx-tokens ≤ budget."""
    total = sum(_approx_token_count(p) for p in parts)
    while parts and total > budget:
        dropped = parts.pop()
        total -= _approx_token_count(dropped)
    return parts


def _format_entry(label: str, body: str) -> str:
    return f"### {label}\n\n{body.rstrip()}\n"


def _load_always_load(vault: Path, permanent_only: bool) -> list[str]:
    """Return the list of always-load markdown bodies (one per file).

    Permanent-only is a no-op for always-load (they're always permanent by
    design — that's the whole point of the directory).
    """
    al = vault / _ALWAYS_LOAD_REL[0] / _ALWAYS_LOAD_REL[1]
    if not al.is_dir():
        return []
    out: list[str] = []
    for path in sorted(al.glob("*.md")):
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append(_format_entry(f"always-load: {path.stem}", body))
    return out


def _load_project_entries(
    vault: Path,
    project: str,
    phase: str,
    permanent_only: bool,
) -> list[str]:
    """Return per-project entries scoped to the phase."""
    base = vault / "personal-projects" / project
    if not base.is_dir():
        return []

    out: list[str] = []
    rels = _PHASE_PROJECT_DIRS.get(phase, ())
    for rel in rels:
        target = base / rel
        if target.is_file() and target.suffix == ".md":
            try:
                body = target.read_text(encoding="utf-8")
            except OSError:
                continue
            out.append(_format_entry(f"{project}/{rel}", body))
        elif target.is_dir():
            for path in sorted(target.glob("*.md")):
                # Permanent-only: skip files marked ephemeral via convention.
                # Convention: filenames under `daily/` or starting with `_inbox-`
                # are ephemeral. Per-phase dirs we walk here are by definition
                # permanent (decisions/, open-questions/, known-issues/), so
                # permanent_only is mostly a no-op at this stage — but we still
                # honor it for forward-compat.
                if permanent_only and path.name.startswith("_inbox-"):
                    continue
                try:
                    body = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                out.append(_format_entry(f"{project}/{rel}/{path.stem}", body))
    return out


def phase_recall(
    phase: str,
    project: Optional[str],
    *,
    budget: Optional[int] = None,
    permanent_only: bool = False,
) -> str:
    """Return a markdown summary of recall context for `phase` + `project`.

    Empty string if vault unavailable (graceful-skip).
    """
    if phase not in _VALID_PHASES:
        raise ValueError(f"unknown phase: {phase!r}")
    v = vault_path()
    if v is None:
        return ""

    parts: list[str] = []
    # Always-load conventions first (highest signal, lowest noise).
    parts.extend(_load_always_load(v, permanent_only))
    # Per-project entries for the phase, if a project slug is known.
    if project:
        parts.extend(_load_project_entries(v, project, phase, permanent_only))

    if not parts:
        return ""

    cap = phase_budget(phase, budget)
    parts = _truncate_to_budget(parts, cap)

    header = (
        f"# Auto-context recall — phase: {phase}, project: {project or '(unknown)'}\n"
        f"(budget: ~{cap} tokens, entries: {len(parts)})\n\n"
    )
    return header + "\n".join(parts) + "\n"


# -----------------------------------------------------------------------------
# offer-save
# -----------------------------------------------------------------------------

def confidence_threshold() -> float:
    """Resolve HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD env (default 0.8)."""
    raw = os.environ.get("HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD", "").strip()
    if not raw:
        return 0.8
    try:
        v = float(raw)
    except ValueError:
        return 0.8
    if not 0.0 <= v <= 1.0:
        return 0.8
    return v


def auto_save_mode() -> str:
    """Resolve HARNESS_AUTO_SAVE_MODE env (default 'ask'). Returns one of ask|silent|off."""
    raw = (os.environ.get("HARNESS_AUTO_SAVE_MODE") or "ask").strip().lower()
    if raw not in ("ask", "silent", "off"):
        return "ask"
    return raw


def should_prompt(
    confidence: Optional[float],
    *,
    mode: Optional[str] = None,
    threshold: Optional[float] = None,
) -> bool:
    """Decide whether `offer-save` should fire an interactive prompt.

    - mode == 'off' → save never happens (caller checks separately)
    - mode == 'silent' → never prompt (always save)
    - mode == 'ask' (default) → confidence-modulated:
        * confidence is None OR < threshold → prompt
        * confidence >= threshold → no prompt (silent save with stderr notice)
    """
    m = (mode or auto_save_mode()).lower()
    if m == "silent":
        return False
    if m == "off":
        # Caller should short-circuit before reaching here, but treat as no-prompt.
        return False
    # ask mode
    thr = threshold if threshold is not None else confidence_threshold()
    if confidence is None:
        return True
    return confidence < thr


def _invoke_toolkit_save(
    *,
    vault: Path,
    project: str,
    kind: str,
    slug: str,
    body: str,
) -> int:
    """Shell out to toolkit `save.py`. Returns the subprocess return code.

    Returns 127 (POSIX convention) if the toolkit is not installed.
    """
    tk = toolkit_scripts_dir()
    if tk is None:
        return 127
    save_py = tk / "save.py"
    if not save_py.is_file():
        return 127
    group = f"personal-projects/{project}"
    cmd = [
        sys.executable,
        str(save_py),
        "--vault-path", str(vault),
        "--group", group,
        "--body-file", "-",
        kind,
        slug,
    ]
    try:
        result = subprocess.run(
            cmd,
            input=body,
            text=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[harness_memory] toolkit save invocation failed: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        # Surface toolkit-side error to operator.
        if result.stderr:
            sys.stderr.write(result.stderr)
    return result.returncode


def offer_save(
    *,
    phase: str,
    project: str,
    kind: str,
    slug: str,
    body: str,
    confidence: Optional[float] = None,
    confidence_reason: Optional[str] = None,
    stdin=None,
    stdout=None,
    stderr=None,
) -> int:
    """Implement `offer-save`. Returns 0 always on graceful-skip / op-skipped /
    success; non-zero only on a toolkit-save error.
    """
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr

    v = vault_path()
    if v is None:
        return 0  # graceful-skip

    mode = auto_save_mode()
    if mode == "off":
        print(f"[harness_memory] HARNESS_AUTO_SAVE_MODE=off — skip save: {kind}/{slug}", file=stderr)
        return 0

    if should_prompt(confidence, mode=mode):
        # Preview + prompt
        thr = confidence_threshold()
        preview_lines = [
            f"--- offer-save preview ({phase} → personal-projects/{project}) ---",
            f"kind: {kind}",
            f"slug: {slug}",
        ]
        if confidence is not None:
            preview_lines.append(f"confidence: {confidence:.2f} (threshold: {thr:.2f})")
            if confidence_reason:
                preview_lines.append(f"reason: {confidence_reason}")
        preview_lines.append("body:")
        preview_lines.append("---")
        preview_lines.append(body.rstrip())
        preview_lines.append("---")
        preview_lines.append("")
        for line in preview_lines:
            print(line, file=stdout)

        # Non-TTY default: skip (never-silent-action contract).
        if not stdin.isatty():
            print(
                f"[harness_memory] non-TTY stdin — defaulting to SKIP for {kind}/{slug}",
                file=stderr,
            )
            return 0
        try:
            print("save this entry? [y/N] ", end="", file=stdout, flush=True)
            answer = stdin.readline().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(f"[harness_memory] skipped {kind}/{slug}", file=stderr)
            return 0
        if answer not in ("y", "yes"):
            print(f"[harness_memory] skipped {kind}/{slug}", file=stderr)
            return 0
    else:
        # High-confidence or silent mode — proceed without prompt.
        if confidence is not None and mode == "ask":
            thr = confidence_threshold()
            print(
                f"[auto-saved high-confidence] {kind}/{slug} "
                f"(confidence={confidence:.2f} ≥ threshold={thr:.2f})",
                file=stderr,
            )
        else:
            print(f"[harness_memory] silent save: {kind}/{slug}", file=stderr)

    rc = _invoke_toolkit_save(
        vault=v, project=project, kind=kind, slug=slug, body=body,
    )
    if rc == 127:
        # Toolkit absent — treat like graceful-skip on the save path.
        print(
            f"[harness_memory] toolkit not installed — recorded intent only "
            f"for {kind}/{slug}",
            file=stderr,
        )
        return 0
    return rc


# -----------------------------------------------------------------------------
# plan-done-promotion
# -----------------------------------------------------------------------------

def _cursor_path(project_root: Path) -> Path:
    return project_root / _CURSOR_REL[0] / _CURSOR_REL[1]


def _progress_path(project_root: Path) -> Path:
    return project_root / _PROGRESS_REL[0] / _PROGRESS_REL[1]


def read_cursor(project_root: Path) -> int:
    cp = _cursor_path(project_root)
    if not cp.is_file():
        return 0
    try:
        return int(cp.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def write_cursor(project_root: Path, offset: int) -> None:
    cp = _cursor_path(project_root)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_bytes((str(int(offset)) + "\n").encode("utf-8"))


def plan_done_promotion(
    project_root: Path,
    *,
    advance_cursor: bool = True,
) -> str:
    """Return the unpromoted tail of progress.md (past the cursor).

    Advances the cursor on success (unless advance_cursor=False, for dry-run).
    Graceful-skip on vault absent OR progress.md absent → returns empty string.

    Idempotent: re-invoking after a full read returns empty string (cursor
    at end-of-file).
    """
    if vault_path() is None:
        return ""
    prog = _progress_path(project_root)
    if not prog.is_file():
        return ""
    try:
        raw = prog.read_bytes()
    except OSError:
        return ""
    cursor = read_cursor(project_root)
    if cursor >= len(raw):
        return ""  # idempotent: already promoted up to end-of-file
    tail = raw[cursor:]
    if advance_cursor:
        write_cursor(project_root, len(raw))
    try:
        return tail.decode("utf-8")
    except UnicodeDecodeError:
        return tail.decode("utf-8", errors="replace")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness_memory",
        description=(
            "Auto-context dispatcher for harness phases. Graceful-skips when "
            "MemoryVault is not installed (MEMORY_VAULT_PATH unset or missing)."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # recall
    p_recall = sub.add_parser(
        "recall",
        help="emit phase-specific recall context to stdout",
    )
    p_recall.add_argument("--phase", required=True, choices=list(_VALID_PHASES))
    p_recall.add_argument("--project", required=False, default=None)
    p_recall.add_argument("--budget", type=int, default=None)
    p_recall.add_argument("--permanent-only", action="store_true")

    # offer-save
    p_save = sub.add_parser(
        "offer-save",
        help="preview + ask + save (self-modulating by --confidence)",
    )
    p_save.add_argument("--phase", required=True, choices=list(_VALID_PHASES))
    p_save.add_argument("--project", required=True)
    p_save.add_argument("--kind", required=True)
    p_save.add_argument("--slug", required=True)
    p_save.add_argument("--content-file", required=True,
                        help="path to file containing the entry body, or '-' for stdin")
    p_save.add_argument("--confidence", type=float, default=None)
    p_save.add_argument("--confidence-reason", type=str, default=None)

    # plan-done-promotion
    p_pdp = sub.add_parser(
        "plan-done-promotion",
        help="dump progress.md tail past cursor; advance cursor",
    )
    p_pdp.add_argument("--project-root", default=None,
                       help="path to project root (default: cwd)")
    p_pdp.add_argument("--dry-run", action="store_true",
                       help="emit tail without advancing the cursor")

    # available
    sub.add_parser("available", help="exit 0 iff vault is accessible")

    return parser


def _read_content_file(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.cmd == "available":
        return 0 if is_available() else 1

    if args.cmd == "recall":
        out = phase_recall(
            args.phase,
            args.project,
            budget=args.budget,
            permanent_only=args.permanent_only,
        )
        if out:
            sys.stdout.write(out)
        return 0

    if args.cmd == "offer-save":
        try:
            body = _read_content_file(args.content_file)
        except OSError as exc:
            print(f"[harness_memory] cannot read --content-file: {exc}", file=sys.stderr)
            return 2
        return offer_save(
            phase=args.phase,
            project=args.project,
            kind=args.kind,
            slug=args.slug,
            body=body,
            confidence=args.confidence,
            confidence_reason=args.confidence_reason,
        )

    if args.cmd == "plan-done-promotion":
        root = Path(args.project_root).expanduser() if args.project_root else Path.cwd()
        tail = plan_done_promotion(root, advance_cursor=not args.dry_run)
        if tail:
            sys.stdout.write(tail)
        return 0

    # argparse should prevent this branch.
    return 2


if __name__ == "__main__":
    sys.exit(main())
