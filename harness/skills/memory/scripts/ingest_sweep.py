#!/usr/bin/env python3
"""ingest_sweep.py — the runner job behind `/memory ingest`'s automated
half, capture part 3 (`designs/friday/agentm-capture.md`,
capture-phone-ingest-sweep plan).

Six duties, one hourly job (per the design's own "don't split into
separate jobs" instruction):

  1. Fetch forwarded links/documents (`source_url` present, not a clip)
     and stage the fetched text ON THE ORIGINATING `_inbox/` CANDIDATE
     ITSELF (`status: ingest_staged` + the text appended under a
     `## Fetched content` heading) -- no new file, no new directory. The
     candidate is already recall-invisible (it's in `_inbox/`, and
     `recall.py`'s existing exclusion already covers it) and was never
     vec_index-enqueued (`capture.py`'s writer never calls
     `vec_index.enqueue`), so staging needs zero new mechanism.
  2. Clip-skips-fetch: a `source: clipper` candidate already carries its
     full content inline -- same in-place staging patch, no network call.
  3. Promote: a candidate staged for at least one full sweep cycle, still
     `status: ingest_staged` (nothing rejected it), gets its stored text
     handed to `ingest.ingest()` -- UNCHANGED, part 2's own pre-flight/
     rollback-safe multi-file writer -- for a real, indexed,
     `save_entry()`-backed write at `personal/domain-reference/`. This is
     the asymmetric-trust boundary this plan exists to build: an
     automated fetch only becomes durable, recall-visible memory after
     surviving a real review window; an explicit, human-invoked
     `/memory ingest` call (part 2) is unaffected and stays direct.
  4. The act step: a candidate's `instructions` field, if present, is
     matched against a small, FIXED, deterministic action grammar
     (`tag:<value>`, `file-under:<topic>`) -- never interpreted by a
     model, never allowed to touch anything beyond placement/metadata on
     THIS candidate's own eventual entries. Anything that doesn't match
     the grammar is left unexecuted (`instructions_acted` stays unset)
     and surfaces in the digest for an attended session -- the worst case
     under a prompt-injection attempt against `instructions` is an inert,
     surfaced string, never an executed arbitrary action.
  5. Idea-ledger fold: `kind: idea` candidates fold into `Ideas.md` via
     the existing, real `ideas_surface.append_idea_to_surface()`.
  6. Timestamp re-stamp: a chat-surface candidate's model-estimated
     `captured:` is corrected against the file's own creation time when
     they disagree.

See the plan's "Mechanism correction" section for why staging patches the
candidate in place rather than writing a new nested-directory batch (the
original design didn't survive contact with `save_entry`'s own group
validator, which rejects any `_`-prefixed path segment -- the same reason
`capture.py` bypasses `save_entry` for `_inbox` in the first place).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import ingest  # noqa: E402  (fetch_url / extract_title_and_text / _slugify / ingest())
from vault_lock import atomic_write, vault_mutex  # noqa: E402

try:
    from ideas_surface import append_idea_to_surface  # noqa: E402
except Exception:  # pragma: no cover — degrade gracefully if the ideas surface path is unresolvable
    append_idea_to_surface = None  # type: ignore[assignment]

_INBOX_SUBDIR = ("personal", "_inbox")
_FETCHED_CONTENT_HEADING = "## Fetched content"
_DEFAULT_STAGING_WINDOW_SECONDS = 3600  # one sweep cycle — see the plan's own reasoning

# The act step's entire vocabulary. Deliberately tiny and closed: a
# candidate's `instructions` either matches one of these exactly, or it
# does nothing. No model ever sees this value; a regex either matches or
# it doesn't.
_TAG_INSTRUCTION_RE = re.compile(r"^tag:([a-z0-9-]+)$")
_FILE_UNDER_INSTRUCTION_RE = re.compile(r"^file-under:([a-z0-9-]+)$")


@dataclass
class SweepResult:
    fetched: "list[str]" = field(default_factory=list)
    staged_clips: "list[str]" = field(default_factory=list)
    promoted: "list[str]" = field(default_factory=list)
    fetch_failures: "list[tuple[str, str]]" = field(default_factory=list)
    duplicates_skipped: "list[tuple[str, str]]" = field(default_factory=list)
    acted: "list[tuple[str, str]]" = field(default_factory=list)
    surfaced_instructions: "list[tuple[str, str]]" = field(default_factory=list)
    idea_folded: "list[str]" = field(default_factory=list)
    idea_fold_denied: "list[tuple[str, str]]" = field(default_factory=list)
    restamped: "list[str]" = field(default_factory=list)


# -----------------------------------------------------------------------------
# Minimal frontmatter helpers — per-module reimplementation, matching
# inbox_triage.py's own documented idiom ("not centralized anywhere in this
# codebase today, so this module follows the same pattern rather than
# introducing a new shared dependency").
# -----------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> "tuple[dict, str]":
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end == -1:
        return {}, content
    fm_text = content[4:end]
    body = content[end + 5:]
    fm: dict = {}
    for line in fm_text.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            try:
                value = json.loads(value)
            except (ValueError, json.JSONDecodeError):
                value = value[1:-1]
        fm[key] = value
    return fm, body


def _patch_frontmatter(content: str, updates: dict) -> str:
    if not content.startswith("---\n"):
        lines = ["---"] + [f"{k}: {v}" for k, v in updates.items()] + ["---"]
        return "\n".join(lines) + "\n" + content
    end = content.find("\n---\n", 4)
    if end == -1:
        return content
    fm_text = content[4:end]
    body = content[end + 5:]
    lines = fm_text.split("\n")
    remaining = dict(updates)
    new_lines = []
    for line in lines:
        if ":" in line:
            key = line.partition(":")[0].strip()
            if key in remaining:
                new_lines.append(f"{key}: {remaining.pop(key)}")
                continue
        new_lines.append(line)
    for k, v in remaining.items():
        new_lines.append(f"{k}: {v}")
    return "---\n" + "\n".join(new_lines) + "\n---\n" + body


def _utcnow_iso(now: "float | None" = None) -> str:
    now = now if now is not None else time.time()
    return datetime.fromtimestamp(now, tz=timezone.utc).replace(microsecond=0).isoformat()


def _iter_inbox_candidates(vault: Path) -> "list[Path]":
    """Direct children of `personal/_inbox/*.md` only — matches
    `inbox_triage.py::_iter_inbox_files`'s own non-recursive glob, which is
    exactly why staging in place (rather than a nested subdirectory) never
    collides with that module's own scan."""
    inbox_dir = Path(vault).joinpath(*_INBOX_SUBDIR)
    if not inbox_dir.exists():
        return []
    return sorted(p for p in inbox_dir.glob("*.md") if p.is_file())


# -----------------------------------------------------------------------------
# Duties 1+2 — fetch/clip, then stage in place on the candidate itself
# -----------------------------------------------------------------------------

def _suggest_topic(title: "str | None", fallback: str) -> str:
    return ingest._slugify(title) if title else ingest._slugify(fallback)


def _find_duplicate_by_source_url(vault: Path, source_url: str, exclude: Path) -> "Path | None":
    """A bounded, targeted check — NOT a general dedup mechanism (that's
    auto-organization's job, parts 5-7). This exists because this sweep's
    OWN behavior creates a specific race `inbox_triage.py`'s own dedup
    structurally cannot catch: this sweep stages every eligible candidate
    in one pass, so a same-cycle resend pair (the design's own named risk
    — "the Drive connector can create files but never update or delete
    them... an uncertain capture sometimes lands twice") both flip out of
    `status: inbox` before any separate triage invocation could ever see
    both of them still untriaged (confirmed empirically, not assumed, at
    /work time). Checks sibling `_inbox/*.md` candidates only (a bounded,
    small set) for an exact `source_url` match already staged or
    ingested — narrower than fuzzy near-duplicate text matching, but it's
    exactly the shape of duplicate this sweep itself can introduce."""
    for sibling in _iter_inbox_candidates(vault):
        if sibling == exclude:
            continue
        fm, _ = _parse_frontmatter(sibling.read_text(encoding="utf-8"))
        if fm.get("status") in ("ingest_staged", "ingested") and fm.get("source_url") == source_url:
            return sibling
    return None


def stage_candidate(vault: Path, path: Path, *, now: "float | None" = None) -> "tuple[bool, str]":
    """Fetch (or read the clip's own inline content) and patch the
    candidate in place. Returns (staged, detail) — detail is an error
    string on failure, or the resolved topic on success. Never raises: a
    fetch failure leaves the candidate untouched at `status: inbox` with
    nothing recorded as an error on THIS pass (the digest surfaces
    fetch_failures explicitly instead — see `run_ingest_sweep`)."""
    raw = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(raw)
    if fm.get("status") != "inbox":
        return False, "not eligible (status != inbox)"

    source = fm.get("source")
    source_url = fm.get("source_url")

    if source_url:
        dup = _find_duplicate_by_source_url(vault, source_url, exclude=path)
        if dup is not None:
            atomic_write(path, _patch_frontmatter(raw, {
                "status": "ingest_duplicate",
                "duplicate_of": dup.stem,
            }))
            return False, f"duplicate of {dup.stem} (same source_url, resend not re-fetched)"

    if source == "clipper":
        if not body.strip():
            return False, "clip candidate has no inline content"
        raw_text = body
        fetched_at = None  # nothing was fetched — captured: stands in, per task 3
    elif source_url:
        try:
            raw_text = ingest.fetch_url(source_url)
        except ingest.FetchError as e:
            return False, str(e)
        fetched_at = _utcnow_iso(now)
    else:
        return False, "not a link/clip candidate — outside this sweep's scope"

    title, text = ingest.extract_title_and_text(raw_text)
    if not text.strip():
        return False, "fetched/read content is empty"
    topic = _suggest_topic(title, path.stem)

    updates = {"status": "ingest_staged", "staged_topic": topic}
    if fetched_at:
        updates["source_fetched"] = fetched_at
    patched = _patch_frontmatter(raw, updates)
    # This function only ever runs on status: inbox candidates (checked
    # above), and the orchestration loop only calls it on status: inbox
    # candidates too — a candidate that already carries a "## Fetched
    # content" section is unreachable here, so no need to strip a prior one.
    final_content = patched.rstrip("\n") + f"\n\n{_FETCHED_CONTENT_HEADING}\n\n{text}\n"

    with vault_mutex(Path(path).parents[2]):
        atomic_write(path, final_content)

    return True, topic


# -----------------------------------------------------------------------------
# Duty 3 — promotion after the staging window
# -----------------------------------------------------------------------------

def _staged_at(fm: dict) -> "str | None":
    return fm.get("source_fetched") or fm.get("captured")


def _is_past_staging_window(fm: dict, *, now_dt: datetime, window_seconds: float) -> bool:
    staged_at = _staged_at(fm)
    if not staged_at:
        return False
    try:
        staged_dt = datetime.fromisoformat(staged_at)
    except ValueError:
        return False
    if staged_dt.tzinfo is None:
        staged_dt = staged_dt.replace(tzinfo=timezone.utc)
    return (now_dt - staged_dt).total_seconds() >= window_seconds


def promote_candidate(vault: Path, path: Path, *, now: "float | None" = None) -> "tuple[bool, str]":
    """Read the staged text back off `path` and hand it to `ingest.ingest()`
    for the real, permanent, indexed write. Returns (promoted, detail)."""
    raw = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(raw)
    if fm.get("status") != "ingest_staged":
        return False, "not eligible (status != ingest_staged)"

    topic = fm.get("staged_topic")
    if not topic:
        return False, "staged candidate missing staged_topic"

    if _FETCHED_CONTENT_HEADING not in body:
        return False, "staged candidate missing its fetched-content section"
    staged_text = body.split(_FETCHED_CONTENT_HEADING, 1)[1].strip("\n")

    result = ingest.ingest(
        vault, fm.get("slug", path.stem), topic=topic, raw_content=staged_text,
        source_url=fm.get("source_url"), source_fetched=fm.get("source_fetched"),
    )
    if not result.success:
        return False, result.error or "ingest() failed"

    derived = [str(result.document.relative_to(vault)).replace(os.sep, "/")]
    derived += [str(c.relative_to(vault)).replace(os.sep, "/") for c in result.chunks]
    updates = {
        "status": "ingested",
        "derived_from": "[" + ", ".join(derived) + "]",
        "ingested_at": _utcnow_iso(now),
    }
    patched = _patch_frontmatter(raw, updates)
    # The fetched-content section did its job; drop it now that the real
    # content lives at the permanent location.
    patched = patched.split(_FETCHED_CONTENT_HEADING, 1)[0].rstrip("\n") + "\n"
    atomic_write(path, patched)

    return True, str(result.document.relative_to(vault))


# -----------------------------------------------------------------------------
# Duty 4 — the act step: a closed, deterministic action grammar
# -----------------------------------------------------------------------------

def dispatch_instruction(instructions: "str | None") -> "tuple[str | None, str | None]":
    """(action, value) if `instructions` matches the fixed grammar, else
    (None, None) — meaning "leave unexecuted, surface it." No model, no
    free-text interpretation: a regex match or nothing. The kebab-case
    requirement is baked into the grammar itself (`[a-z0-9-]+` inside the
    capture group), not a separate post-match check — a value smuggled
    inside an otherwise-matching command (e.g. `tag:../../escape`) simply
    never matches at all, so it surfaces as an unrecognized instruction
    rather than executing with a malformed value."""
    if not instructions:
        return None, None
    m = _TAG_INSTRUCTION_RE.match(instructions.strip())
    if m:
        return "tag", m.group(1)
    m = _FILE_UNDER_INSTRUCTION_RE.match(instructions.strip())
    if m:
        return "file-under", m.group(1)
    return None, None


def apply_act_step(path: Path, *, now: "float | None" = None) -> "tuple[str | None, str | None]":
    """Returns (action, detail): action is "tag"/"file-under"/None
    (unmatched — surfaced, not executed) /"skip" (no instructions at all)."""
    raw = path.read_text(encoding="utf-8")
    fm, _body = _parse_frontmatter(raw)
    if fm.get("instructions_acted"):
        return "skip", "already acted"
    instructions = fm.get("instructions")
    if not instructions:
        return "skip", "no instructions"

    action, value = dispatch_instruction(instructions)
    if action is None:
        # Does not match the grammar -- left unexecuted, surfaced by the
        # caller's digest. instructions_acted stays unset so a later cycle
        # doesn't need to re-decide this (the digest line is the record).
        return None, instructions

    if action == "tag":
        existing = [t.strip() for t in fm.get("tags", "[]").strip("[]").split(",") if t.strip()]
        if value not in existing:
            existing.append(value)
            raw = _patch_frontmatter(raw, {"tags": "[" + ", ".join(existing) + "]"})
    elif action == "file-under":
        raw = _patch_frontmatter(raw, {"staged_topic": value})

    raw = _patch_frontmatter(raw, {"instructions_acted": _utcnow_iso(now)})
    atomic_write(path, raw)
    return action, value


# -----------------------------------------------------------------------------
# Duty 5 — idea-ledger fold
# -----------------------------------------------------------------------------

def fold_idea_candidate(vault: Path, path: Path) -> "tuple[bool, str]":
    """Fold a `kind: idea` candidate into Ideas.md via the real, existing
    surface. `append_idea_to_surface()` itself gates on the A3 permeable-
    write-boundary confirmation (`Ideas.md` lives outside `MemoryVault/`),
    which DENIES by default in a non-interactive/unattended context --
    exactly this sweep's own execution context. Returns (folded, detail):
    `folded=False` with a clear reason when the boundary denies -- the
    candidate is left untouched (still `status: inbox`, available for a
    later cycle once the operator opts in, or an attended `/memory inbox`
    review) rather than silently dropped or the boundary silently
    overridden by force-passing a bypass mode.

    `vault` is passed through as `ideas_path=<vault>/../Ideas.md` explicitly
    -- the sweep already knows its own concrete vault path, so this must
    not fall back to `ideas_surface`'s own independent env-var/config
    resolution guesswork (which resolves against whatever vault is
    configured on the machine, not necessarily the one this sweep run was
    given, e.g. a scratch vault under test)."""
    if append_idea_to_surface is None:
        return False, "ideas_surface unavailable"
    raw = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(raw)
    if fm.get("kind") != "idea" or fm.get("status") != "inbox":
        return False, "not an eligible idea candidate"
    title = fm.get("slug", path.stem).replace("-", " ")
    ideas_path = Path(vault).parent / "Ideas.md"
    try:
        written = append_idea_to_surface(title, body.strip(), incubator_slug=fm.get("slug"), ideas_path=ideas_path)
    except (ValueError, FileNotFoundError) as e:
        return False, str(e)
    if written is None:
        return False, (
            "denied by the permeable-write-boundary gate (unattended context) -- "
            "set MEMORY_REVIEW_MODE=silent for this job to opt in, or fold manually via /memory inbox"
        )
    atomic_write(path, _patch_frontmatter(raw, {"status": "promoted", "promoted_to": "Ideas.md"}))
    return True, str(written)


# -----------------------------------------------------------------------------
# Duty 6 — timestamp re-stamp
# -----------------------------------------------------------------------------

def restamp_candidate(path: Path) -> bool:
    """Correct `captured:` against the file's own filesystem creation/
    modification time when they disagree by more than a few seconds
    (floating-point/clock-skew tolerant). Returns True if corrected."""
    raw = path.read_text(encoding="utf-8")
    fm, _body = _parse_frontmatter(raw)
    captured = fm.get("captured")
    if not captured:
        return False
    try:
        captured_dt = datetime.fromisoformat(captured)
    except ValueError:
        return False
    if captured_dt.tzinfo is None:
        captured_dt = captured_dt.replace(tzinfo=timezone.utc)

    fs_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    if abs((fs_dt - captured_dt).total_seconds()) <= 5:
        return False

    corrected = fs_dt.replace(microsecond=0).isoformat()
    atomic_write(path, _patch_frontmatter(raw, {"captured": corrected}))
    return True


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------

def run_ingest_sweep(
    vault_path: "Path | str", *, now: "float | None" = None,
    staging_window_seconds: float = _DEFAULT_STAGING_WINDOW_SECONDS,
) -> SweepResult:
    vault = Path(vault_path)
    now = now if now is not None else time.time()
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    result = SweepResult()

    for path in _iter_inbox_candidates(vault):
        raw = path.read_text(encoding="utf-8")
        fm, _body = _parse_frontmatter(raw)
        status = fm.get("status", "inbox")

        if status == "inbox":
            if fm.get("kind") == "idea":
                folded, detail = fold_idea_candidate(vault, path)
                if folded:
                    result.idea_folded.append(str(path))
                else:
                    result.idea_fold_denied.append((str(path), detail))
                continue
            if fm.get("source") == "clipper" or fm.get("source_url"):
                staged, detail = stage_candidate(vault, path, now=now)
                if staged:
                    (result.staged_clips if fm.get("source") == "clipper" else result.fetched).append(str(path))
                elif detail.startswith("duplicate of "):
                    result.duplicates_skipped.append((str(path), detail))
                elif "not eligible" not in detail and "outside this sweep's scope" not in detail:
                    result.fetch_failures.append((str(path), detail))
            action, detail = apply_act_step(path, now=now)
            if action not in (None, "skip"):
                result.acted.append((str(path), f"{action}:{detail}"))
            elif action is None and detail:
                result.surfaced_instructions.append((str(path), detail))
            if restamp_candidate(path):
                result.restamped.append(str(path))

        elif status == "ingest_staged":
            fm2, _b2 = _parse_frontmatter(path.read_text(encoding="utf-8"))
            if _is_past_staging_window(fm2, now_dt=now_dt, window_seconds=staging_window_seconds):
                promoted, detail = promote_candidate(vault, path, now=now)
                if promoted:
                    result.promoted.append(detail)

    return result


def _render_digest(result: SweepResult) -> str:
    lines = ["# Ingest-sweep digest", ""]
    lines.append(f"Fetched + staged: {len(result.fetched)}")
    lines.append(f"Clips staged: {len(result.staged_clips)}")
    lines.append(f"Promoted to permanent memory: {len(result.promoted)}")
    if result.fetch_failures:
        lines.append("")
        lines.append("## Fetch failures (left in place, not silently dropped)")
        for path, err in result.fetch_failures:
            lines.append(f"- {path}: {err}")
    if result.duplicates_skipped:
        lines.append("")
        lines.append("## Resends skipped (same source_url already staged/ingested)")
        for path, detail in result.duplicates_skipped:
            lines.append(f"- {path}: {detail}")
    if result.surfaced_instructions:
        lines.append("")
        lines.append("## Instructions needing an attended session (not auto-executed)")
        for path, instr in result.surfaced_instructions:
            lines.append(f"- {path}: {instr!r}")
    if result.acted:
        lines.append("")
        lines.append("## Mechanical actions applied")
        for path, action in result.acted:
            lines.append(f"- {path}: {action}")
    if result.idea_folded:
        lines.append("")
        lines.append(f"Ideas folded into the ledger: {len(result.idea_folded)}")
    if result.idea_fold_denied:
        lines.append("")
        lines.append("## Idea folds needing an operator opt-in (Ideas.md is outside the vault)")
        for path, reason in result.idea_fold_denied:
            lines.append(f"- {path}: {reason}")
    if result.restamped:
        lines.append("")
        lines.append(f"Timestamps corrected: {len(result.restamped)}")
    return "\n".join(lines) + "\n"


def _resolve_vault(cli_arg: "str | None") -> "Path | None":
    if cli_arg:
        p = Path(cli_arg)
        return p if p.is_dir() else None
    env = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    return None


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ingest-sweep",
        description="The automated half of /memory ingest — fetch, stage, promote, act, fold, re-stamp.",
    )
    parser.add_argument("--vault-path", help="vault root (default: $MEMORY_VAULT_PATH env var)")
    parser.add_argument(
        "--staging-window-seconds", type=float, default=_DEFAULT_STAGING_WINDOW_SECONDS,
        help="how long a fetched candidate stays staged before promotion (default: one sweep cycle)",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    vault = _resolve_vault(args.vault_path)
    if vault is None:
        print("[ingest-sweep] no vault resolved — pass --vault-path or configure MEMORY_VAULT_PATH", file=sys.stderr)
        return 2

    result = run_ingest_sweep(vault, staging_window_seconds=args.staging_window_seconds)
    print(_render_digest(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
