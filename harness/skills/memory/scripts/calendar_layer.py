"""The episodic capture layer, and the rule that keeps it evidence.

`calendar/YYYY/YYYY-MM-DD_<slug>.md` records what happened on a day and what was
touched. It is written during session ingestion, and it is the answer to the
question this arc opened with — logs of what happened, addressable by entity,
task or project. The calendar holds the trace; the entity index makes it
reachable from the other direction, so "what happened involving X" is a lookup
rather than a scan.

# The trace is never rewritten

Dreaming consolidates old traces into crystallized cards, and consolidation
writes a **new** card carrying `consolidated_from` back to the days it was built
from. It does not touch the days.

That is the whole design of this file. A consolidation that edited its own
sources would leave the derived claim and no way to check it — the card would say
what the system now believes and nothing would say what it believed that from.
Both survive here, and either can be read on its own.

The rule is enforced rather than intended: `consolidate` takes a reader and a
writer, the reader is never handed to the writer, and the test that matters
compares the traces byte for byte before and after.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

CALENDAR_ROOT = "calendar"

# The one field that makes a crystallized card checkable: which days it was built
# from. Without it the card is an assertion with no way back to its evidence,
# which is the same failure `source:` and `derived_from:` exist to prevent
# everywhere else in this design.
CONSOLIDATED_FROM = "consolidated_from"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, fallback: str = "session") -> str:
    """A filename-safe slug, or `fallback` when nothing survives.

    A day whose title was entirely punctuation still gets a trace — losing the
    record because the title was unusable is the opposite of what an episodic
    layer is for.
    """
    slug = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return slug or fallback


def trace_path(when: date, slug: str) -> str:
    """`calendar/YYYY/YYYY-MM-DD_<slug>.md`, vault-relative.

    Sharded by year rather than flat, for the reason every other shard in this
    vault exists: a directory with ten thousand entries is one nothing opens.
    """
    return f"{CALENDAR_ROOT}/{when:%Y}/{when:%Y-%m-%d}_{slugify(slug)}.md"


@dataclass
class Trace:
    """One day's episodic record."""

    when: date
    slug: str
    # Touched is what the session actually changed, which is the half that makes
    # a trace useful later — "what happened" without "to what" is a diary entry.
    touched: list = field(default_factory=list)
    # Entities are the references this day is about, so the entity index makes
    # the day reachable from the other direction.
    entities: list = field(default_factory=list)
    summary: str = ""
    body: str = ""

    @property
    def path(self) -> str:
        return trace_path(self.when, self.slug)

    def render(self) -> str:
        """The trace as it goes on disk."""
        lines = [
            "---",
            "type: reference",
            f"kind: conversation",
            f"date: {self.when:%Y-%m-%d}",
            "status: active",
            f"title: {_yaml_scalar(self.summary or self.slug)}",
        ]
        if self.entities:
            lines.append("entities: [" + ", ".join(
                _yaml_scalar(e) for e in sorted(set(self.entities))) + "]")
        lines += ["---", ""]
        if self.summary:
            lines += [self.summary, ""]
        if self.body:
            lines += [self.body.rstrip("\n"), ""]
        if self.touched:
            lines += ["## Touched", ""]
            lines += [f"- [[{t}]]" for t in sorted(set(self.touched))]
            lines.append("")
        return "\n".join(lines)


def write_trace(vault_path, trace: Trace, *, write=None) -> str:
    """Write one day's trace and return its vault-relative path.

    Appends rather than replacing when the day already has a trace under this
    slug. A session is not a day: two sessions on one afternoon are two things
    that happened, and the second must not delete the first.
    """
    vault_path = Path(vault_path)
    rel = trace.path
    abs_path = vault_path / rel
    writer = write or _default_write

    existing = ""
    try:
        existing = abs_path.read_text(encoding="utf-8")
    except OSError:
        pass

    if existing:
        body = existing.rstrip("\n") + "\n\n---\n\n" + _without_frontmatter(trace.render())
    else:
        body = trace.render()
    writer(abs_path, body)
    return rel


def _default_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _without_frontmatter(body: str) -> str:
    if not body.startswith("---"):
        return body
    rest = body[3:]
    end = rest.find("\n---")
    if end < 0:
        return body
    return rest[end + 4:].lstrip("\n")


def _yaml_scalar(value: str) -> str:
    v = (value or "").strip()
    if not v or any(c in v for c in ':#[]{}&*!|>%@`"\'\n'):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'
    return v


# ── consolidation ──────────────────────────────────────────────────────────

@dataclass
class Consolidation:
    """A crystallized card built from days, and the days it was built from."""

    slug: str
    lesson: str
    sources: list = field(default_factory=list)

    @property
    def path(self) -> str:
        return f"memory/crystallized/{slugify(self.slug, fallback='lesson')}.md"

    def render(self) -> str:
        lines = [
            "---",
            "type: convention",
            "status: active",
            f"title: {_yaml_scalar(self.slug)}",
            CONSOLIDATED_FROM + ": [" + ", ".join(
                _yaml_scalar(s) for s in self.sources) + "]",
            "---",
            "",
            self.lesson.rstrip("\n"),
            "",
        ]
        return "\n".join(lines)


def consolidate(vault_path, consolidation: Consolidation, *, write=None) -> str:
    """Write a crystallized card from a set of calendar traces.

    The traces are read by the caller and named here; this never opens them and
    never writes to them. That is not a convention — it is why the writer is the
    only path out of this function, and why the test that matters hashes every
    trace before and after.

    A card with no sources is refused. The whole value of a crystallized lesson
    is that somebody can ask what it was built from, and one that cannot answer
    is an assertion wearing the shape of a conclusion.
    """
    if not consolidation.sources:
        raise ValueError(
            "a crystallized card needs the days it was built from; without "
            f"{CONSOLIDATED_FROM} the lesson is an assertion with no way back to "
            "its evidence"
        )
    if not consolidation.lesson.strip():
        raise ValueError("a crystallized card with no lesson says nothing")

    vault_path = Path(vault_path)
    rel = consolidation.path
    (write or _default_write)(vault_path / rel, consolidation.render())
    return rel


def digest_traces(vault_path, rels: list) -> dict:
    """A content hash per trace, for proving consolidation touched none of them.

    Used by the test rather than by the pass. A rule that is only stated is one
    that eventually stops being true.
    """
    vault_path = Path(vault_path)
    out = {}
    for rel in rels:
        try:
            blob = (vault_path / rel).read_bytes()
        except OSError:
            continue
        out[rel] = hashlib.sha256(blob).hexdigest()
    return out
