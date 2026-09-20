#!/usr/bin/env python3
"""Filing a card that came from somewhere nobody vouches for.

This is the surviving half of the email door (agentm-vault part 12), kept when
its transport retired in part 16. The door's premise was that a chat surface has
no route to the machine; the Drive mirror is that route, and `agent/inbox/` is
where a card lands now. What is gone is IMAP, TLS negotiation, the
`Authentication-Results` reader, the sender allow-list and the mailbox
credential — nine rounds of adversarial review, almost all of it about
authenticating a transport nothing uses any more, because an address accepts
mail from anyone and a Drive folder accepts writes only from the operator's own
authenticated account.

What stayed is the part that was never about email:

**A card is content, and content is never an instruction.** `capture()`'s
`instructions` field is the ingest sweep's act-step grammar — a field that can
cause something to happen — and nothing here ever populates it, from the card's
body or from anywhere else. A card written by a model on a chat surface, quoting
whatever page it was reading, is data.

**The trust tier is the transport's, not the card's.** Filing with
`transport="inbox"` is what makes the contract stamp `trust: untrusted`;
this module does not decide its own trust level, and no card it files lands
`active`.

**A size cap, with the truncation said out loud.** A card is a card, not an
article. Past the cap the body is cut and the cut is written into the text, so
nothing silently loses its tail.

**A refusal is counted and never stored.** Nothing about a refused card reaches
the vault beyond what the operator already put there — the count, by reason, is
all there is to report, and a reason is one of a closed list so a new one cannot
arrive unnoticed.

**`daily_write_cap` is `capture()`'s, and it still applies.** A refusal by the
cap is the one that can come out differently tomorrow, and it is reported as
such rather than as a judgment about the card.

Usage: imported. `parse_card_text()` reads a card's own text; `file_card()`
writes one through the path a capture already takes.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# A card is a card, not an article. Past this the text is something else — a
# pasted page, a transcript, a reply chain — and this is not the place to decide
# what to do with one.
MAX_CARD_BYTES = 64 * 1024
MAX_BODY_CHARS = 8_000

#: A title may tag the card's type: `[fix] the thing that broke`. The tag is
#: matched against the contract's own types by the caller; an unrecognised tag
#: is dropped and the contract's default type stands, rather than inventing a
#: type from text nobody vouches for.
_SUBJECT_TAG = re.compile(r"^\s*\[([a-z][a-z-]{0,30})\]\s*(.*)$", re.I)

#: `project: <slug>` on its own line, anywhere in the body. Kebab only: this
#: ends up as a frontmatter value and as a directory name in some readers.
_PROJECT_LINE = re.compile(r"^\s*project\s*:\s*([a-z0-9][a-z0-9-]{0,63})\s*$", re.I | re.M)
#: `why: <one line>` — the reason it was kept, which the card keeps.
_WHY_LINE = re.compile(r"^\s*why\s*:\s*(\S.*)$", re.I | re.M)

#: Every reason a card can be refused. Named rather than free-text so a count is
#: groupable and a new reason cannot arrive unnoticed.
DROP_REASONS = (
    "too-large",
    "empty-body",
    "unreadable",
    "write-refused",
)

#: The one reason that can come out differently tomorrow. Everything else above
#: is a property of the card and will still be true the hundredth time it is
#: offered. `write-refused` covers `daily_write_cap` and a lock timeout, so a
#: card refused for it is offered again rather than treated as judged.
TRANSIENT_DROP_REASONS = frozenset({"write-refused"})


@dataclass
class FileResult:
    """What one filing pass did. Counts only, for anything refused."""

    filed: list = field(default_factory=list)   # [(slug, title)]
    dropped: dict = field(default_factory=dict)  # reason -> count
    offered: int = 0
    error: "str | None" = None

    def drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    @property
    def dropped_total(self) -> int:
        return sum(self.dropped.values())

    def summary(self) -> str:
        if self.error:
            return f"filing: {self.error}"
        if not self.offered:
            return "filing: nothing offered"
        parts = [f"{len(self.filed)} card(s) of {self.offered} offered"]
        if self.dropped:
            parts.append(", ".join(f"{n} {r}" for r, n in sorted(self.dropped.items())))
        return "filing: " + "; ".join(parts)


def parse_card_text(text: str, *, title: "str | None" = None) -> "tuple[dict | None, str]":
    """`(card, "")` or `(None, reason)` for one card's own text.

    A card is: a title, a type from its `[tag]` when the title carries one, the
    body as content, a `why:` line if the writer gave one, and a `project:` line
    if they named one. Nothing else in the text is read, and in particular
    nothing in it reaches a field that can cause something to happen.
    """
    if text is None:
        return None, "unreadable"
    if len(text.encode("utf-8", "replace")) > MAX_CARD_BYTES:
        return None, "too-large"
    body = text.strip()
    subject = (title or "").strip()
    type_hint = None
    m = _SUBJECT_TAG.match(subject)
    if m:
        type_hint, subject = m.group(1).lower(), m.group(2).strip()
    if not body and not subject:
        return None, "empty-body"
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS].rstrip() + "\n\n[truncated at the card size cap]"
    why = None
    mw = _WHY_LINE.search(body)
    if mw:
        why = mw.group(1).strip()[:300]
    project = None
    mp = _PROJECT_LINE.search(body)
    if mp:
        project = mp.group(1).lower()
    card_title = subject or body.splitlines()[0][:120]
    return {
        "title": card_title,
        "type_hint": type_hint,
        "body": body,
        "why": why,
        "project": project,
    }, ""


def file_card(vault_path, card: dict, *, capture_fn=None, result: "FileResult | None" = None,
              why: "str | None" = None, project: "str | None" = None,
              type_hint: "str | None" = None):
    """Write one card through the path a capture already takes.

    `why`, `project` and `type_hint` override what the card's own text carried —
    that is the review pass's whole job, where the operator says what a
    phone-typed line was actually about. Everything the operator did not say
    falls back to the text.

    Returns `capture()`'s own result, or None when the card was refused; the
    refusal is counted on `result`.
    """
    result = result if result is not None else FileResult()
    result.offered += 1
    if capture_fn is None:
        import capture as capture_mod  # same skill dir
        capture_fn = capture_mod.capture

    content = card["title"] + "\n\n" + card["body"]
    hint = type_hint if type_hint is not None else card.get("type_hint")
    # `instructions` is deliberately absent, and its absence is the security
    # property: it is the sweep's act-step grammar, and text from an untrusted
    # transport must never reach a field that can cause something to happen.
    # `transport="inbox"` is what makes the contract stamp `trust: untrusted`
    # — this module does not decide its own trust level, the contract's own
    # sources table does.
    #
    # Wrapped so one card the writer chokes on cannot take a batch with it.
    try:
        out = capture_fn(
            vault_path, content,
            kind="idea" if hint == "idea" else "capture",
            source="inbox",
            surface="inbox",
            transport="inbox",
            type_hint=hint if hint != "idea" else None,
            why=why if why is not None else card.get("why"),
            project=project if project is not None else card.get("project"),
        )
    except Exception:  # noqa: BLE001 — one bad card, not a lost batch
        result.drop("write-refused")
        return None
    if getattr(out, "success", False):
        result.filed.append((getattr(out, "slug", "?"), card["title"]))
        return out
    # The cap, or a write failure. The one refusal that can come out differently
    # tomorrow, which is why the caller leaves the card where it is rather than
    # treating it as judged.
    result.drop("write-refused")
    return None
