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

**Trigger, and why it's operator-invoked in v1 (a scope decision, not an
oversight):** at research time, "a completed exploration" is NOT a bounded,
detectable thing anywhere in this codebase — no hook, marker, or session
type of that name exists (confirmed by grep across wiki/, harness/,
AGENTS.md). Wiring an automatic phase-boundary trigger (e.g. off the
crickets developer-workflows phase loop, or a new session-boundary marker)
is real, undecided infrastructure work this task does not invent from
scratch. Instead this module ships the callable an operator — or, later, a
phase hook once one is designed — invokes once an exploration is judged
closed: `crystallize_exploration(vault_path, slug, digest)`. This mirrors
this wave's own established precedent: dreaming shipped its manual `/dream`
before any scheduled trigger; forward-learning shipped one deterministic
pass before a semantic judge. Automatic phase-boundary wiring is a natural
v2 graduation, not built here.

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

    crystallize_exploration(vault_path, slug, digest, *, group="personal",
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

import re
import sys
from dataclasses import dataclass
from pathlib import Path
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
]

DIGEST_KIND = "crystallized"
DIGEST_FIELDS = ("question", "investigation", "findings", "lessons", "open_threads")

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
    group: str = "personal",
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
