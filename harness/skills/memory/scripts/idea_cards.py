#!/usr/bin/env python3
"""idea_cards.py — an idea card in `personal/ideas/`, the one shape its writers
produce (agentm-vault part 13).

An idea is a card at the vault root's `personal/ideas/<slug>.md`, flat, in the
card's shape with `type: idea` and an `area:` holding one of the operator's own
group names. `Ideas.md` is generated over the folder every night by the dreaming
binary, one heading per group the cards carry.

Two writers put a card there, and both go through `as_idea_card()` so the two
cannot disagree about what "filed as an idea" means:

- the inbox review, `/memory inbox --file NAME --type idea --area GROUP`, when
  the operator reads a card a chat surface dropped and says it is an idea and
  which group it belongs to (`inbox_review.file_one_idea`);
- the one-time migration from `memory/semantic/`, which applies the operator's
  corrected mapping (`scripts/ideas_migration.py`).

What "filed as an idea" means, on the operator's rulings of 2026-09-20: the card
becomes `status: active` and stays so whatever any later pass thinks of it; it
loses `lifecycle` and `lifecycle_since`, because `personal/` has no aging axis;
it gains its `area:`, and `dismissed:` when the idea was struck. Its
`filing_confidence` becomes `high`, because the operator filed it — `low` means
a candidate nobody has read, and this card has been read. Every other field it
carried travels with it unchanged: its title, its words, its `why`, its source
and its trust, and any stamps the night already wrote.

Nothing here decides a group. A group name is whatever the operator typed: one
word, lower-case, hyphens allowed — the shape that makes "rename or merge a group
by editing one word on a card" true.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import card_shape  # noqa: E402 — same skill dir
import vault_layout  # noqa: E402 — same skill dir

#: The folder, relative to the vault root. The daemon's `enrich.IdeasDir` and
#: the dreaming binary's `IdeasDirRel` spell the same two segments.
IDEAS_DIR_REL = ("personal", "ideas")

#: A group name: one word, lower-case kebab.
AREA_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

#: What an idea card never carries in `personal/`.
DROPPED_KEYS = ("lifecycle", "lifecycle_since")

#: The type every idea card holds, and the status it holds when it carries one.
#: A card with no `status` is read as `active`, because a card in the folder is
#: filed by being there (the daemon's `enrich.KeptFiling`), and the night writes
#: the field on its next pass.
IDEA_TYPE = "idea"
IDEA_STATUS = "active"

#: What every idea card carries: `type: idea`, which makes it one, and `area:`,
#: the group it is listed under. Nothing else is asked of a card the operator
#: makes by hand. The class card's required set does not apply here: it
#: requires `lifecycle`, which an idea card never carries.
REQUIRED_FIELDS = ("type", "area")

#: A `dismissed:` value: the day the idea was retired.
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Written into `memory/` by the ideas move's `--finish`
#: (`scripts/migrate/ideas_migration.py`) once every card in the folder has the
#: idea card's shape. Until it exists, `scripts/check-card-shape.py` lists what
#: it finds in the folder and passes; once it exists, the gate enforces.
MARKER_NAME = ".ideas-surface-complete"


def vault_root(memory_root) -> Path:
    """The vault root beside a memory root (the parent when the root is nested
    in an Obsidian vault; the root itself for a flat vault)."""
    return vault_layout.vault_root_candidates(memory_root)[0]


def ideas_dir(memory_root) -> Path:
    return vault_root(memory_root).joinpath(*IDEAS_DIR_REL)


def marker_path(memory_root) -> Path:
    return Path(memory_root) / "memory" / MARKER_NAME


def card_paths(memory_root) -> list:
    """Every idea card, in name order: the markdown files directly in the
    folder, with no dotfile and nothing in a subfolder. It is the set the
    daemon's `enrich.IsIdeaCard` offers the night and the dreaming binary
    lists."""
    folder = ideas_dir(memory_root)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.suffix == ".md" and not p.name.startswith(".") and p.is_file())


def valid_area(area: "str | None") -> bool:
    return bool(area) and bool(AREA_RE.match(area))


def as_idea_card(text: str, *, area: str, dismissed: "str | None" = None,
                 why: "str | None" = None, project: "str | None" = None,
                 today: "str | None" = None) -> str:
    """`text` as an idea card: the fields set, the aging axis dropped, the
    card's order restored, the body byte for byte as it was.

    `why` and `project` fill the field only when the card has none: they are the
    operator's words at the review pass, and a card that already carries a `why`
    carries the room's. A card with no frontmatter block at all gets one, with
    its first line as the title — the shape a card pasted in by hand can arrive
    in.
    """
    if not valid_area(area):
        raise ValueError(f"{area!r} is not a group name: one lower-case word, hyphens allowed")
    if dismissed is not None and not DAY_RE.match(dismissed):
        raise ValueError(f"dismissed: {dismissed!r} is not a YYYY-MM-DD day")
    parsed = card_shape.split_note(text)
    if parsed is None:
        first = next((l.strip("# ").strip() for l in text.splitlines() if l.strip()), "")
        entries = [("title", [f"title: {card_shape.quote(first[:120] or 'untitled')}"])]
        rest = "---\n\n" + text.lstrip("\n")
    else:
        entries, rest = parsed
    entries = card_shape.drop_keys(entries, *DROPPED_KEYS)
    entries = card_shape.set_value(entries, "type", IDEA_TYPE)
    entries = card_shape.set_value(entries, "area", area)
    entries = card_shape.set_value(entries, "status", IDEA_STATUS)
    entries = card_shape.set_value(entries, "filing_confidence", "high")
    if dismissed:
        entries = card_shape.set_value(entries, "dismissed", dismissed)
    if why and not card_shape.scalar(card_shape.raw_value(entries, "why")):
        entries = card_shape.set_value(entries, "why", card_shape.quote(why))
    if project and not card_shape.scalar(card_shape.raw_value(entries, "project")):
        entries = card_shape.set_value(entries, "project", project)
    entries = card_shape.set_value(entries, "updated", today or date.today().isoformat())
    return card_shape.reorder(card_shape.join_note(entries, rest))


# ── an idea that already has a card adds to it ────────────────────────────────
#
# agentm-vault § Capture, amended 2026-09-28 (the operator's ruling 6c): a
# captured idea is matched against the cards in `personal/ideas/` by slug,
# title and body. On a match it is appended to that card under a dated
# `## Added by capture` heading, with its source, and no semantic note is
# written; the 2026-09-24 survey found six ideas carried twice, once as a card
# and once as a semantic note. A new idea is filed as before. The Go door
# (`daemon/internal/capture/ideacard.go`) keeps the same rule.

ADDED_BY_CAPTURE = "## Added by capture"
#: The line a match must reach: the largest of the slug test (1 or 0), the
#: title overlap and the body overlap, each a Jaccard over meaningful words.
IDEA_MATCH_LINE = 0.5

_STOPWORDS = frozenset(
    "the a an and or of to in on for with is are was be it this that as at by from we i you our my "
    "your can could should would will just not but so if then into about".split())


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 3 and w not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _card_parts(text: str) -> tuple:
    """(title, body) of a card, the body without what capture or the night added."""
    parsed = card_shape.split_note(text)
    if parsed is None:
        return "", text
    entries, rest = parsed
    title = card_shape.scalar(card_shape.raw_value(entries, "title")) or ""
    body = rest.split("\n", 1)[1] if "\n" in rest else ""
    for heading in (ADDED_BY_CAPTURE, "## Added by dreaming"):
        body = body.split("\n" + heading, 1)[0]
    return title, body


def idea_match(slug: str, title: str, body: str, card_path: Path) -> float:
    """How far a captured idea is this card: 1.0 for the same slug, else the
    larger of the title and body word overlaps."""
    if slug and card_path.stem == slug:
        return 1.0
    try:
        card_title, card_body = _card_parts(card_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return 0.0
    return max(_jaccard(_words(title), _words(card_title)), _jaccard(_words(body), _words(card_body)))


def matching_card(memory_root, *, slug: str = "", title: str = "", body: str = "") -> "Path | None":
    """The idea card a captured idea matches at or above the line, the best
    first; None when none does."""
    best, best_score = None, 0.0
    for p in card_paths(memory_root):
        score = idea_match(slug, title, body, p)
        if score > best_score:
            best, best_score = p, score
    return best if best is not None and best_score >= IDEA_MATCH_LINE else None


def append_capture(card_path: Path, text: str, *, source: str, day: str) -> bool:
    """Add `text` to the card under `## Added by capture`, dated and sourced;
    False, writing nothing, when the card already holds those words. Nothing
    above the section changes, and a section the night added after it stays
    where it is."""
    current = card_path.read_text(encoding="utf-8")
    words = " ".join((text or "").split())
    if not words or words in " ".join(current.split()):
        return False
    entry = f"**{day} · {source}**\n\n{text.strip()}\n"
    at = current.find("\n" + ADDED_BY_CAPTURE + "\n")
    if at < 0:
        updated = current.rstrip("\n") + f"\n\n{ADDED_BY_CAPTURE}\n\n{entry}"
    else:
        end = current.find("\n## ", at + len(ADDED_BY_CAPTURE) + 2)
        section_end = len(current) if end < 0 else end
        head, tail = current[:section_end].rstrip("\n"), current[section_end:]
        updated = head + f"\n\n{entry}" + (("\n" + tail.lstrip("\n")) if tail else "")
    from vault_lock import atomic_write  # same skill dir
    atomic_write(card_path, updated)
    return True


def add_to_matching_card(memory_root, text: str, *, slug: str = "", title: str = "", source: str,
                         day: "str | None" = None, lock_timeout: float = 10.0) -> "Path | None":
    """Append a captured idea to the card it matches, under the vault mutex, and
    return the card; None when no card matches (the idea is filed as before). A
    card that already holds the words is returned unchanged: the idea is home."""
    from vault_lock import vault_mutex  # same skill dir
    with vault_mutex(memory_root, timeout=lock_timeout):
        card = matching_card(memory_root, slug=slug, title=title, body=text)
        if card is None:
            return None
        append_capture(card, text, source=source, day=day or date.today().isoformat())
        return card
