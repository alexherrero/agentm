#!/usr/bin/env python3
"""card_shape — the card's field order and its rules, in one place.

agentm-vault § The card (session 1, decided) fixes what a memory note holds and
the order Obsidian's properties panel shows it in: what you read first, what the
machinery reads last. Every Python writer of a class card, the card backfill
(agentm-vault plan 06) and the card gates read this module, so the order has one
definition on this side. The Go writers keep the same two lists in
`daemon/internal/cardshape`, and `scripts/test_card_shape.py` fails when the two
part.

What lives here:

- `READ_ORDER` and `MACHINE_ORDER`, the two blocks. A key in neither is a
  record's own field, or a field the design has not placed. It sits after the
  read block and keeps its relative order.
- `reorder(text)`, the note with its top-level keys in that order and the body
  byte for byte.
- `order_findings(keys)`, what is out of order, in words.
- The fields a card requires, the fields the design retired, and what a record
  never carries.
- The naming rule: which slugs are counters standing in for a name, and the
  name a counter is renamed to.
- The two derivations a backfill may make without a model: a title from a
  first-line H1, and a summary from a body that is one sentence.
"""
from __future__ import annotations

import json
import re

# ── the two blocks ────────────────────────────────────────────────────────────

# What you read, in the order the properties panel shows it. `type` and `kind`
# share a slot (a note carries one). `lifecycle_since` sits beside `lifecycle`,
# because the two are one fact and every writer already puts them together.
# `source_url`, `source_id` and `source_fetched` sit beside `source`;
# `supersedes` and `superseded_by` beside `related`; `project` and `task`
# after them.
READ_ORDER: tuple[str, ...] = (
    "title", "type", "kind", "summary", "why", "importance",
    "status", "lifecycle", "lifecycle_since", "filing_confidence",
    "source", "source_url", "source_id", "source_fetched", "trust",
    "created", "updated", "tags",
    "related", "supersedes", "superseded_by",
    "project", "task",
)

# What the machinery reads, last. The design names the first seven; the rest are
# the machine fields the corpus already carries, in a fixed order so two writers
# never disagree about them.
MACHINE_ORDER: tuple[str, ...] = (
    "slug", "confidence", "enriched_by", "enriched_at", "rules_hash",
    "fingerprint", "importance_proposed",
    "aliases", "occurrences", "derived_from", "source_hash", "source_version",
    # A capture's own record before the engine's review marks, as the locked
    # order always had them.
    "via", "surface", "instructions", "review_flags",
    "promoted_at", "promoted_to", "probe", "backfilled",
)

_READ_INDEX = {k: i for i, k in enumerate(READ_ORDER)}
_MACHINE_INDEX = {k: i for i, k in enumerate(MACHINE_ORDER)}

# ── the card's rules ──────────────────────────────────────────────────────────

# Every card in a class directory carries these (agentm-vault part 06).
REQUIRED_CARD_FIELDS: tuple[str, ...] = (
    "title", "status", "lifecycle", "filing_confidence",
    "source", "trust", "created", "updated",
)

# Retired by the design. `captured` folds into `created`; the `mining_*` trio
# retired with the miner's shape; `excerpt_edges_unverified` had no reader;
# `evaluator_classification` and `rubric_score` belong to the watchlist's record.
RETIRED_FIELDS: tuple[str, ...] = (
    "altitude", "group", "always_load", "captured",
    "mining_confidence", "mining_rationale", "mining_occurrences",
    "excerpt_edges_unverified", "evaluator_classification", "rubric_score",
)

# A record (`kind:`) keeps its own fields and never carries these.
RECORD_NEVER: tuple[str, ...] = ("importance", "why", "filing_confidence", "trust")

# A record field renamed by the design: a trace names what it touched.
RENAMED_RECORD_FIELDS: dict[str, str] = {"entities": "touched"}

# The daemon's synthetic self-probe card carries this key. It is excluded from
# every measurement, and it names no transport.
PROBE_KEY = "probe"

# The class directories the card rules cover. `mocs/` is generated navigation
# and is regenerated nightly; agentm-vault plan 07 owns its shape.
SCOPE_CLASSES: tuple[str, ...] = ("semantic", "procedural", "episodic", "entities", "crystallized")

# Written into `memory/` by the applied card backfill. Until it exists the card
# gates report what the backfill would change and pass; once it exists they
# enforce.
MARKER_NAME = ".card-backfill-complete"

# The longest slug a writer produces.
SLUG_MAX = 72

# ── reading and writing a frontmatter block ───────────────────────────────────

_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:")


def split_note(text: str):
    """`(entries, rest)` for a note with a frontmatter block, or None.

    `entries` is `[(key, lines)]` in file order: a top-level key and the lines
    that belong to it (continuations, blanks and comments stay with the key
    above them). A key of None holds lines before the first key. `rest` starts
    at the closing fence and runs to the end of the note."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 3)
    if end == -1:
        return None
    block = text[4:end] if end > 3 else ""
    rest = text[end + 1:] if end > 3 else text[4:]
    entries: list[tuple[str | None, list[str]]] = []
    for line in block.split("\n") if block else []:
        m = _KEY_LINE.match(line)
        if m and line[:1] not in (" ", "\t", "-", "#"):
            entries.append((m.group(1), [line]))
        elif entries:
            entries[-1][1].append(line)
        else:
            entries.append((None, [line]))
    return entries, rest


def join_note(entries, rest: str) -> str:
    lines = [line for _, group in entries for line in group]
    return "---\n" + ("\n".join(lines) + "\n" if lines else "") + rest


def keys(text: str) -> list[str]:
    parsed = split_note(text)
    return [k for k, _ in parsed[0] if k] if parsed else []


def raw_value(entries, key: str) -> str | None:
    """The first line's value for `key`, as written, or None when absent."""
    for k, group in entries:
        if k == key:
            return group[0].split(":", 1)[1].strip()
    return None


def has_continuation(entries, key: str) -> bool:
    for k, group in entries:
        if k == key:
            return any(line.strip() for line in group[1:])
    return False


def scalar(raw: str | None) -> str | None:
    """A scalar value without its quotes."""
    if raw is None:
        return None
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except ValueError:
            return v[1:-1]
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def flow_list(raw: str | None) -> list[str]:
    """The items of a one-line flow list, unquoted. `[]` and absence are empty."""
    if raw is None:
        return []
    v = raw.strip()
    if not (v.startswith("[") and v.endswith("]")):
        s = scalar(v)
        return [s] if s else []
    inner = v[1:-1].strip()
    if not inner:
        return []
    return [scalar(part.strip()) for part in inner.split(",") if part.strip()]


def quote(value: str) -> str:
    """A string as a YAML double-quoted scalar (JSON escaping is valid YAML)."""
    return json.dumps(value, ensure_ascii=False)


def set_value(entries, key: str, rendered: str) -> list:
    """`entries` with `key: rendered` replacing the key's lines, or appended."""
    out, done = [], False
    for k, group in entries:
        if k == key and not done:
            out.append((k, [f"{key}: {rendered}"]))
            done = True
        elif k == key:
            continue
        else:
            out.append((k, group))
    if not done:
        out.append((key, [f"{key}: {rendered}"]))
    return out


def drop_keys(entries, *names: str) -> list:
    return [(k, g) for k, g in entries if k not in names]


# ── order ─────────────────────────────────────────────────────────────────────

def _rank(key: str | None) -> tuple[int, int]:
    if key is None:
        return (-1, 0)
    if key in _READ_INDEX:
        return (0, _READ_INDEX[key])
    if key in _MACHINE_INDEX:
        return (2, _MACHINE_INDEX[key])
    return (1, 0)


def order_entries(entries) -> list:
    return sorted(entries, key=lambda e: _rank(e[0]))


def reorder(text: str) -> str:
    """The note with its top-level keys in the card's order.

    The read block comes first, then any key neither block names in its
    original relative order, then the machine block. Every line stays with its
    key and the body is untouched. A note already in order comes back as the
    same string."""
    parsed = split_note(text)
    if parsed is None:
        return text
    entries, rest = parsed
    ordered = order_entries(entries)
    if ordered == entries:
        return text
    return join_note(ordered, rest)


def order_findings(note_keys: list[str]) -> list[str]:
    """What is out of the card's order, in words; empty when in order.

    A key neither block names may sit anywhere after the read block, so a
    writer that appends its own field at the end of a block stays in order."""
    out = []
    read = [k for k in note_keys if k in _READ_INDEX]
    if read != sorted(read, key=_READ_INDEX.__getitem__):
        out.append(f"read fields out of order: {read} (expected "
                   f"{sorted(read, key=_READ_INDEX.__getitem__)})")
    machine = [k for k in note_keys if k in _MACHINE_INDEX]
    if machine != sorted(machine, key=_MACHINE_INDEX.__getitem__):
        out.append(f"machine fields out of order: {machine} (expected "
                   f"{sorted(machine, key=_MACHINE_INDEX.__getitem__)})")
    first_other = next((i for i, k in enumerate(note_keys) if k not in _READ_INDEX), None)
    if first_other is not None:
        late = [k for k in note_keys[first_other:] if k in _READ_INDEX]
        if late:
            out.append(f"read field(s) {late} after `{note_keys[first_other]}`, "
                       "which the read block does not name")
    return out


# ── naming ────────────────────────────────────────────────────────────────────

_DUP = re.compile(r"~dup\d*$")
_COUNTER = re.compile(r"-([1-9]\d?)$")
_TALLY = re.compile(r"^(?:workflow|fix|preference|convention|reference|idea)-[a-z0-9]+-\d+$")
_DATE_SUFFIX = re.compile(r"\d{4}-\d{2}-\d{2}$")
_CHUNK = re.compile(r"-chunk-\d+$")


def kebab(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def cut_slug(slug: str, limit: int = SLUG_MAX) -> str:
    """`slug` cut at a word boundary to at most `limit` characters."""
    if len(slug) <= limit:
        return slug
    words, out = slug.split("-"), []
    for w in words:
        if len("-".join(out + [w])) > limit:
            break
        out.append(w)
    return "-".join(out) or slug[:limit]


def is_counter_slug(stem: str, title: str | None = None) -> bool:
    """True when the slug ends in a counter standing in for a name.

    `~dup` and `~dupN` always are, and so is a `<type>-<tool>-<n>` tally. A
    trailing `-1` to `-99` is one unless the card's own title ends in the same
    number (`Follow-up batch 3`), the slug ends in a date, or it numbers the
    chunks of one fetched page."""
    if _DUP.search(stem):
        return True
    m = _COUNTER.search(stem)
    if title and m and kebab(title).endswith("-" + m.group(1)):
        return False
    if _TALLY.match(stem):
        return True
    if not m or _DATE_SUFFIX.search(stem) or _CHUNK.search(stem):
        return False
    return True


def strip_counter(stem: str) -> str:
    return _COUNTER.sub("", _DUP.sub("", stem))


# The words a colliding name never grows by, because they say nothing about the
# note. The Go writer reads the same list (`cardshape.CollisionStopwords`), and
# scripts/test_card_shape.py fails when the two part.
COLLISION_STOPWORDS: tuple[str, ...] = (
    "the", "and", "for", "with", "that", "this", "from", "into",
    "are", "was", "but", "not", "its",
)


def meaningful(word: str) -> bool:
    """Whether a colliding name may grow by `word`: three characters or more,
    not only digits, and not a stopword."""
    return len(word) >= 3 and not word.isdigit() and word not in COLLISION_STOPWORDS


def free_name(base: str, words, is_taken, *, digest: str = "", limit: int = SLUG_MAX) -> str | None:
    """The collision rule for a writer: a meaningful word, never a counter.

    `base` when it is free. Otherwise `base` grows by the next of `words` it
    does not already hold, one word at a time, until a free name turns up. A
    word that is only digits, shorter than three characters or a stopword is
    skipped, so the grown name never reads as `-2` or `-a`. When no word frees
    it, the name ends in the first six characters of `digest`. None when even
    that is taken."""
    if not is_taken(base):
        return base
    held = set(base.split("-"))
    grown = base
    for word in words:
        if not word or word in held or not meaningful(word):
            continue
        cand = f"{grown}-{word}"
        if len(cand) > limit:
            break
        held.add(word)
        grown = cand
        if not is_taken(grown):
            return grown
    if digest:
        cand = f"{cut_slug(base, limit - 7)}-{digest[:6]}"
        if not is_taken(cand):
            return cand
    return None


def rename_target(stem: str, title: str | None, body: str, taken) -> str | None:
    """The name a counter slug is renamed to, or None when none is free.

    The card's title, kebab-cased and cut at a word, is the name. Without a
    title, the counter comes off; when that name is taken, the slug grows by the
    next words of the text it was cut from until it is free. `taken` holds every
    name already in use, lower-cased."""
    candidates = []
    if title:
        candidates.append(cut_slug(kebab(title)))
    base = strip_counter(stem)
    candidates.append(base)
    for cand in candidates:
        if cand and cand.lower() not in taken and not is_counter_slug(cand, title):
            return cand
    first = next((ln for ln in body.split("\n") if ln.strip()), "")
    words = kebab(first).split("-")
    stem_words = base.split("-")
    n = len(stem_words)
    for i in range(len(words) - n + 1):
        if words[i:i + n] == stem_words:
            grown = list(stem_words)
            for w in words[i + n:]:
                grown.append(w)
                cand = "-".join(grown)
                if len(cand) > SLUG_MAX:
                    break
                if cand.lower() not in taken and not is_counter_slug(cand, title):
                    return cand
            break
    return None


# ── what a backfill may derive without a model ────────────────────────────────

_H1 = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$")


def title_from_body(body: str) -> str | None:
    """The body's first line when it is an H1, as the note's title."""
    for line in body.split("\n"):
        if not line.strip():
            continue
        m = _H1.match(line)
        return m.group(1).strip() if m else None
    return None


def summary_from_body(body: str) -> str | None:
    """The body when it is one line that reads as a sentence: it opens with a
    capital or a digit, closes with terminal punctuation, and is not a
    fragment, a heading, a quote, a list item or code."""
    lines = [ln for ln in body.split("\n") if ln.strip()]
    if len(lines) != 1:
        return None
    s = lines[0].strip()
    if len(s) > 300 or s.startswith(("...", "…", "#", ">", "-", "*", "`", "|", "[", "!")):
        return None
    if not (s[0].isupper() or s[0].isdigit()):
        return None
    if not s.endswith((".", "!", "?")):
        return None
    return s
