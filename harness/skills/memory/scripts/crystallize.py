#!/usr/bin/env python3
"""crystallize.py — phase-close crystallization (AG Wave E experience plan,
task 2).

At the close of a completed exploration, distil it into a structured
five-field digest — **question · investigation · findings · lessons · open
threads** — instead of leaving raw transcript fragments behind. Distinct
from per-session reflection (`reflect.py`, fired by the `memory-reflect-
stop` / `memory-reflect-idle` hooks on every session regardless of whether
an exploration closed) — this is the phase-close counterpart, one digest
per closed exploration, not one per session.

**Trigger — designed and built (2026-07-26): `post-work` and `post-release`
stage candidates; composing a digest stays manual.** At authoring time, "a
completed exploration" was not a bounded, detectable thing anywhere in this
codebase. That premise expired: crickets PR #214 shipped
`agentm_bridge.py`'s `phase-dispatch` verb, and `orchestration_phase.py`'s
`stage_crystallization_candidate()` now rides both events as a sibling step,
staging a bare marker under `<vault>/_crystallize-staging/` (session id +
transcript pointer — see that module and `wiki/designs/agentm-experience-
and-dreaming.md` § Crystallization's phase-close trigger for the full design).
What the trigger deliberately does NOT do is judge whether a session merits
a digest or compose one: an orchestration chain fires outside the agent loop
and cannot dispatch a sub-agent, so both stay behind
`exploration_judge_available()` (always `False` today). Composing a digest
from a staged candidate — via `crystallize_exploration(vault_path, slug,
digest)` below — is still an operator (or future agent-side) action; the
trigger only makes sure a candidate is waiting to be picked up.

**Schema — shared with `PLAN-wave-e-v6-index` task 7's consolidation work**
("the phase-close counterpart to dreaming's whole-corpus pass; the digest
schema is V6 work", per `wiki/designs/agentm-experience-and-dreaming.md`).
Authored once here; V6-4's tier-transition consolidation (not yet built at
authoring time — confirmed via that plan's own worktree, still on task 2)
should wire into `CrystallizationDigest` / `DIGEST_KIND` / `parse_digest`
rather than redefining the shape.

**Distillation is NOT this module's job.** `crystallize_exploration` takes
already-composed field values (a `CrystallizationDigest`) and writes them —
it does not itself read or mine a raw transcript. The "instead of raw
transcript fragments" contract is satisfied trivially in v1: nothing here
ever touches a transcript, so there is no raw fragment to accidentally
persist alongside the digest. Whatever produces the five field values
(an operator, or a future LLM-assisted pass) is out of this module's scope.

**No new store.** Routes through the existing memory engine
(`save.py`'s `save_entry`) exactly like every other kind-classified entry —
per the design's own "everything routes through the existing memory
engine — no new store" principle. Lands at `<vault>/<group>/crystallized/
<slug>.md` (the as-built `vault/group/kind/slug.md` convention `save.py`
already uses — NOT the designed-for-but-unmigrated `Memory/<kind>/<slug>.md`
three-tier layout `agentm-memory-system.md` sketches, which no current
script actually writes to yet).

Public surface:

    CrystallizationDigest(question, investigation, findings, lessons,
                            open_threads)
        The locked five-field schema. All fields are plain strings.

    crystallize_exploration(vault_path, slug, digest, *, group="memory",
                             tags=None) -> Path
        Writes the digest as a `kind: crystallized` entry via `save_entry`.
        Raises whatever `save_entry` raises (e.g. `FileExistsError` on a
        slug collision — same "never silently overwrite" contract as any
        other kind).

    parse_digest(entry_path) -> CrystallizationDigest
        Reads a written crystallized entry back into its five fields —
        the round-trip the red-test uses to assert the schema matches
        exactly, and the shape a future consolidation consumer reads.

DIGEST_KIND = "crystallized"; DIGEST_FIELDS = the five field names in
schema order.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import engine_state  # noqa: E402
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from save import save_entry  # noqa: E402

__all__ = [
    "CrystallizationDigest",
    "DIGEST_KIND",
    "DIGEST_FIELDS",
    "crystallize_exploration",
    "parse_digest",
    "MalformedDigestError",
    "STAGING_DIRNAME",
    "stage_candidate",
    "list_candidates",
    "drop_candidate",
    "count_pending_candidates",
    "exploration_judge_available",
]

DIGEST_KIND = "crystallized"
DIGEST_FIELDS = ("question", "investigation", "findings", "lessons", "open_threads")

# ── phase-close trigger: staging (agentm-experience-and-dreaming.md
# § Crystallization's phase-close trigger, locked calls 4-5-7-8) ─────────────
#
# The trigger fires on real phase-dispatch events (post-work, post-release)
# but cannot judge whether a session merits a digest or compose its five
# fields — the chain that fires cannot dispatch a sub-agent
# (orchestration_idle.py:24). So it stages a bare marker instead: a session id
# and a transcript pointer, nothing else. `orchestration_phase.py` resolves
# WHICH session to stage (it owns `.harness/` marker discovery); this module
# owns WHERE staged candidates live in the vault and how they're managed.

STAGING_DIRNAME = "crystallize-staging"  # under the engine state dir (filing-v2 2a)
# Calibration-era cap (call 5) — no measured pending volume behind it yet. A
# cap that trips is evidence the pickup surface isn't being read, not a bound
# to raise reflexively.
_MAX_PENDING_CANDIDATES = 50


def _staging_dir(vault_path: Path | str) -> Path:
    del vault_path  # staging left the vault with the machine state (2a);
    # the stage→confirm→revert contract itself is untouched — part 6 owns
    # retiring it into the dreaming binary.
    return engine_state.engine_state_dir() / STAGING_DIRNAME


def _candidate_path(vault_path: Path | str, phase: str, session_id: str) -> Path:
    return _staging_dir(vault_path) / f"{phase}-{session_id}.json"


def stage_candidate(
    vault_path: Path | str,
    phase: str,
    session_id: str,
    transcript: str,
    *,
    now: Optional[str] = None,
) -> dict:
    """Stage (or refresh) a crystallization candidate. Idempotent on
    `(phase, session_id)` (call 5) — never writes a second candidate for the
    same session; re-firing bumps `fire_count` and refreshes `last_fired`
    instead. No cooldown: this write is a few hundred bytes, and gating it on
    a shared clock would silently drop whole sessions' candidates.

    Returns a result dict: `{"status": "staged"|"refreshed"|"capped",
    "path": str|None, "fire_count": int}`. Never raises OSError from the
    filesystem calls it makes directly — a caller invoked from a
    non-blocking orchestration chain wraps this and must not be wedged by it,
    but this function's own contract is to surface a status, not swallow one.
    """
    if now is None:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    staging_dir = _staging_dir(vault_path)
    path = _candidate_path(vault_path, phase, session_id)

    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["last_fired"] = now
        data["fire_count"] = int(data.get("fire_count", 1)) + 1
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return {"status": "refreshed", "path": str(path), "fire_count": data["fire_count"]}

    staging_dir.mkdir(parents=True, exist_ok=True)
    try:
        existing = sum(1 for p in staging_dir.glob("*.json") if p.is_file())
    except OSError:
        existing = 0
    if existing >= _MAX_PENDING_CANDIDATES:
        return {"status": "capped", "path": None, "fire_count": 0}

    data = {
        "phase": phase,
        "session_id": session_id,
        "transcript": str(transcript),
        "first_fired": now,
        "last_fired": now,
        "fire_count": 1,
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return {"status": "staged", "path": str(path), "fire_count": 1}


def list_candidates(vault_path: Path | str) -> list:
    """All pending candidates, oldest first. Never raises — a malformed
    candidate is skipped rather than breaking the surface reading it (the
    same hook-contract shape every push surface in this codebase follows)."""
    staging_dir = _staging_dir(vault_path)
    if not staging_dir.is_dir():
        return []
    try:
        paths = sorted(staging_dir.glob("*.json"))
    except OSError:
        return []
    out = []
    for p in paths:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def drop_candidate(vault_path: Path | str, phase: str, session_id: str) -> bool:
    """Delete a candidate — picked up into a digest, or dismissed. Never
    archived (call 8): the marker holds no content worth retaining once a
    digest exists or the operator has passed. Returns True if a file was
    removed, False if it did not exist."""
    path = _candidate_path(vault_path, phase, session_id)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def count_pending_candidates(vault_path: Path | str) -> int:
    """Bare directory count for the push surfaces (session_brief.py,
    console.py — call 6). Deliberately not a parse of each file's content:
    the same clobber-proof glob shape `session_brief.count_parked` uses,
    since `_meta/needs-your-eye.json`'s overwrite-every-cycle contract would
    silently lose an appended item. Never raises."""
    staging_dir = _staging_dir(vault_path)
    if not staging_dir.is_dir():
        return 0
    try:
        return sum(1 for p in staging_dir.glob("*.json") if p.is_file())
    except OSError:
        return 0


def exploration_judge_available() -> bool:
    """Unbuilt seam (call 7): whether a staged session merits a five-field
    digest, and what the fields are, both need a model the orchestration
    chain cannot call (`orchestration_idle.py:24`). Always `False` today —
    the fourth seam of this exact shape, alongside
    `opinion_routing.assist_tier_available()`,
    `dream.cheap_model_tier_available()`, and
    `dream_confirm.higher_tier_model_available()`. Flip all four together
    when a chain-time model primitive exists; do not build a heuristic in
    the meantime (transcript length, fire_count, files touched) — that would
    encode a guess about what makes a session investigative as though it
    were a measurement."""
    return False

_SECTION_TITLES = {
    "question": "Question",
    "investigation": "Investigation",
    "findings": "Findings",
    "lessons": "Lessons",
    "open_threads": "Open threads",
}


class MalformedDigestError(ValueError):
    """`parse_digest` could not find all five locked sections in an entry."""


@dataclass(frozen=True)
class CrystallizationDigest:
    question: str
    investigation: str
    findings: str
    lessons: str
    open_threads: str


def _render_body(digest: CrystallizationDigest) -> str:
    parts = []
    for field in DIGEST_FIELDS:
        title = _SECTION_TITLES[field]
        value = getattr(digest, field).strip()
        parts.append(f"## {title}\n\n{value}\n")
    return "\n".join(parts)


def crystallize_exploration(
    vault_path: Path | str,
    slug: str,
    digest: CrystallizationDigest,
    *,
    group: str = "memory",
    tags: Optional[list] = None,
) -> Path:
    """Write `digest` as a `kind: crystallized` entry. The digest IS the
    persisted artifact — nothing else (no raw transcript, no intermediate
    file) is written by this call."""
    body = _render_body(digest)
    return save_entry(vault_path, DIGEST_KIND, slug, body, group=group, tags=tags or [])


def parse_digest(entry_path: Path | str) -> CrystallizationDigest:
    """Read a crystallized entry back into its five fields. Raises
    `MalformedDigestError` if any of the five locked `## <Title>` sections
    is missing — the schema is exact, not best-effort."""
    text = Path(entry_path).read_text(encoding="utf-8")
    # Strip frontmatter (delimited by the first two `---` lines) — the
    # sections live in the body only.
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        body = text[end + 5:] if end != -1 else text
    else:
        body = text

    values = {}
    for field in DIGEST_FIELDS:
        title = re.escape(_SECTION_TITLES[field])
        pattern = rf"^## {title}\n\n(.*?)(?=\n## |\Z)"
        m = re.search(pattern, body, flags=re.MULTILINE | re.DOTALL)
        if m is None:
            raise MalformedDigestError(
                f"{entry_path}: missing locked section '## {_SECTION_TITLES[field]}'"
            )
        values[field] = m.group(1).strip()

    return CrystallizationDigest(**values)


# ── CLI ──────────────────────────────────────────────────────────────────────
# The module docstring above describes this as "the callable an operator ...
# invokes once an exploration is judged closed" — but until this entrypoint
# existed the module was importable-only, so an operator could not actually
# invoke it. This is the thin manual path the design names as the right first
# step ("the same thin-manual-path-first precedent dreaming (`/dream`) and
# forward learning already set"), not the deferred phase-close trigger.
#
# It deliberately does NOT distil. `crystallize_exploration` takes an
# already-composed digest by design, and turning a transcript into the five
# fields is the other half of this module's `[PENDING-IMPL]`. The CLI reads
# the five fields you give it and writes the entry; building a summarizer in
# here would be shipping the deferred work through the back door.


def _parse_digest_text(text: str) -> "CrystallizationDigest":
    """Parse the five locked sections out of raw digest markdown.

    Shares `parse_digest`'s section grammar deliberately: an operator writes
    the same `## Question` / `## Investigation` / ... shape the entry itself
    uses, so what you hand in reads like what comes back out. Frontmatter is
    tolerated but not required, which lets you pipe an existing crystallized
    entry straight back in.
    """
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    values = {}
    for field in DIGEST_FIELDS:
        title = re.escape(_SECTION_TITLES[field])
        m = re.search(rf"^## {title}\n\n(.*?)(?=\n## |\Z)", text,
                      flags=re.MULTILINE | re.DOTALL)
        if m is None:
            raise MalformedDigestError(
                f"missing locked section '## {_SECTION_TITLES[field]}' — the "
                f"schema is exact; all five of "
                f"{', '.join(_SECTION_TITLES[f] for f in DIGEST_FIELDS)} are required"
            )
        values[field] = m.group(1).strip()
    return CrystallizationDigest(**values)


def _build_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog="crystallize.py",
        description="Write or read a five-field crystallization digest.",
        epilog="The digest is yours to compose — this does not summarize a "
               "transcript. Automatic phase-close crystallization is deferred; "
               "see the design's [PENDING-IMPL].",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="write a digest as a crystallized entry")
    w.add_argument("--vault-path", required=True)
    w.add_argument("--slug", required=True, help="entry slug (kebab-case)")
    w.add_argument("--digest-file", default=None,
                   help="markdown file with the five '## <Title>' sections "
                        "(default: read stdin)")
    w.add_argument("--group", default="memory")
    w.add_argument("--tags", default=None, help="comma-separated")

    r = sub.add_parser("read", help="parse a crystallized entry back into its fields")
    r.add_argument("entry_path")
    return p


def main(argv: "list[str] | None" = None) -> int:
    ns = _build_parser().parse_args(argv)

    if ns.cmd == "read":
        try:
            digest = parse_digest(ns.entry_path)
        except MalformedDigestError as e:
            print(f"crystallize: {e}", file=sys.stderr)
            return 1
        except OSError as e:
            print(f"crystallize: cannot read {ns.entry_path}: {e}", file=sys.stderr)
            return 1
        for field in DIGEST_FIELDS:
            print(f"## {_SECTION_TITLES[field]}\n\n{getattr(digest, field)}\n")
        return 0

    raw = (Path(ns.digest_file).read_text(encoding="utf-8")
           if ns.digest_file else sys.stdin.read())
    if not raw.strip():
        print("crystallize: empty digest input", file=sys.stderr)
        return 1
    try:
        digest = _parse_digest_text(raw)
    except MalformedDigestError as e:
        print(f"crystallize: {e}", file=sys.stderr)
        return 1

    tags = [t.strip() for t in ns.tags.split(",") if t.strip()] if ns.tags else []
    try:
        written = crystallize_exploration(
            ns.vault_path, ns.slug, digest, group=ns.group, tags=tags,
        )
    except FileExistsError:
        # save_entry's never-silently-overwrite contract. Surface it as a
        # sentence rather than a traceback — a slug collision is an ordinary
        # thing to hit, not a crash.
        print(f"crystallize: an entry already exists for slug {ns.slug!r} "
              f"(nothing was overwritten); choose another slug", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"crystallize: could not write entry: {e}", file=sys.stderr)
        return 1

    print(written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
