#!/usr/bin/env python3
"""week1_corpus.py — the two searchable views of the vault the week-1 experiment scores.

Backs `agentm-rescope-week1-experiment.md`. Builds and queries the two
retrieval surfaces the experiment's arms are made of:

  - **lexical** — SQLite FTS5 + BM25 over every `.md` file in the vault, using
    the FTS5 module compiled into SQLite itself. No loadable extension, so this
    runs under Apple's system Python (`/usr/bin/python3`, 3.9.6, SQLite 3.51.0),
    which the existing sqlite-vec path cannot.
  - **vector** — brute-force cosine similarity over chunk embeddings of the same
    corpus, produced by `embed.py`. No ANN index: 8.5k files is small enough that
    a single dense matmul answers a query in milliseconds, and an index would be
    a second thing to be wrong about for one experiment.

Both are built once per run and cached to disk under `--work-dir`, keyed by a
corpus fingerprint (relative path + mtime + size of every file). A changed vault
rebuilds; an unchanged one reloads.

Corpus scope
------------
Every `.md` file under the vault root, excluding only dot-directories
(`.obsidian/`, `.git/`). That is deliberately wider than `recall.py`'s
`_iter_entry_paths`, which additionally drops `_dream-staging/`, `_inbox/`, and
`_archive/`. Those are recall *policy*, and the experiment is measuring what is
retrievable underneath policy — a note that FTS5 cannot find is not going to be
rescued by a ranking penalty. `--exclude-dir` narrows the corpus for sensitivity
runs; `_dream-staging` (1,052 of 8,588 files at time of writing) is the obvious
candidate, since it is scratch that never surfaces to an agent.

Chunking, and why the vector arm is not doc-truncated
-----------------------------------------------------
FTS5 indexes each document whole — there is no length ceiling. If the vector arm
embedded only each document's first N characters, Arm B would be handicapped on
exactly the long entries where the answer sits deep in the body, and the
experiment would report a lexical win that was really a truncation artifact.
That failure is not hypothetical here: `recall.py`'s own BM25 pass carried a
fixed 500-character window until V6-10 traced measured recall gaps straight to
it. So the vector side chunks with `chunking.chunk_text` and scores a document
by its best chunk (max-passage), which is what the lexical side effectively
already does.

Chunk size is 1,500 characters rather than `chunking.CHUNK_CHARS` (500). That
module's default was tuned for BM25 term-frequency saturation; here the binding
constraint is the embedding model's context window, and ~375 tokens uses BGE's
512-token budget without overflowing it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_MEMORY_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_MEMORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_MEMORY_SCRIPTS))

# Vector-arm chunking. See the module docstring for why this is not
# chunking.CHUNK_CHARS.
VECTOR_CHUNK_CHARS = 1500
VECTOR_CHUNK_OVERLAP = 200

# FTS5 bm25() column weights, in table-column order (path, title, body). `path`
# is UNINDEXED and contributes nothing regardless of weight; `title` (the note's
# slug plus any frontmatter title) is weighted above body because a filename
# match on this vault is a strong signal — notes are named for their subject.
_BM25_WEIGHTS = (0.0, 4.0, 1.0)

# Bumped whenever the on-disk index schema changes, so a cached index built by an
# older revision is rebuilt rather than queried through a schema it does not have.
_SCHEMA_VERSION = "3"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)
_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


# ---------------------------------------------------------------------------
# Lexical variants
# ---------------------------------------------------------------------------
# Each variant is one FTS5 schema. They differ only in tokenizer and column
# layout, so a run can attribute a score change to exactly one of those and not
# to a bundle of them. `baseline` is byte-for-byte what the 2026-08-06 run
# indexed — porter stemming and a 4x title boost were already in it, so the two
# knobs the follow-up brief named as untried variants are the control, and the
# variants below are the ablation (`plain`, `flat`) and the extension (`fields`).
#
# `fields` adds one column rather than moving text between columns: `meta`
# carries the note's `aliases` and `tags` values, which stay in `body` too. So
# the only difference from baseline is that those two fields are counted twice,
# the second time at a higher weight — nothing becomes less searchable.
LEXICAL_VARIANTS = {
    "baseline": {"tokenize": "porter unicode61", "fields": "merged",
                 "weights": (0.0, 4.0, 1.0)},
    "fields": {"tokenize": "porter unicode61", "fields": "split",
               "weights": (0.0, 4.0, 3.0, 1.0)},
    "plain": {"tokenize": "unicode61", "fields": "merged",
              "weights": (0.0, 4.0, 1.0)},
    "flat": {"tokenize": "porter unicode61", "fields": "merged",
             "weights": (0.0, 1.0, 1.0)},
}
DEFAULT_VARIANT = "baseline"


def variant_spec(variant):
    try:
        return LEXICAL_VARIANTS[variant]
    except KeyError:
        raise CorpusError(
            f"unknown lexical variant {variant!r}; expected one of "
            f"{', '.join(sorted(LEXICAL_VARIANTS))}"
        ) from None


def lexical_db_name(variant):
    """Per-variant index filename. `baseline` keeps the original name so the
    already-built index on this machine is reused rather than rebuilt."""
    return "lexical.db" if variant == DEFAULT_VARIANT else f"lexical-{variant}.db"


# ---------------------------------------------------------------------------
# Rank penalty — the classifier
# ---------------------------------------------------------------------------
# 3,413 of this vault's 8,687 notes are miner fragments: a truncated sentence
# clipped out of a session transcript, wrapped in frontmatter, and written as its
# own file. They carry the operator's vocabulary because they are quotations of
# it, which is exactly why they rank — they compete on every query and win on
# some. `agentm-rescope-memory.md` calls for demoting them.
#
# Demote, never exclude. A note that cannot be returned at all cannot be returned
# when it is the only answer, and a filter that silently removed a class of
# results is the mechanism that left recall dead for four months. Everything here
# multiplies a score; nothing here drops a row.
FRAGMENT_OPENERS = (
    "User stated:",
    "Fix observed:",
    "User corrected the agent:",
)

# Statuses that mean "not filed yet" or "no longer true". The rescope design
# names `unfiled`; this corpus predates that vocabulary and spells the same
# condition `inbox`, so both are listed and the daemon inherits the pair.
PENALIZED_STATUSES = frozenset({"unfiled", "inbox", "superseded", "expired"})

# Statuses that mean a human or dreaming has looked at this note and kept it.
# A promoted note that still *looks* like a miner fragment is one, because the
# promotion pipeline promoted the fragment's body verbatim — 232 of the 234
# notes in `personal/preferences/` are exactly this. Filing is the signal that
# overrides the miner's fingerprint, so their shape stops being evidence.
PROMOTED_STATUSES = frozenset({
    "active", "filed", "final", "promoted", "done", "ratified", "rendered",
})

# Where a mid-word slug is evidence of a fragment. Both directories were filled
# by the same miner, and a note whose filename starts partway into a word
# ("rver-s-vault-hardwiring-can-t-1") was cut out of a longer sentence.
FRAGMENT_SLUG_DIRS = ("memory/idea", "memory/fix")

# Short words that legitimately start a slug, so a real note is not read as a
# truncation just for beginning with one.
_REAL_SHORT_WORDS = frozenset("""
a an and the of to in on at by for is it as or no not new old all one two six
add api cli fix log run see set use web why how who has had was are can may
day end few far git job key lab map max min net now off out own pdf per put
raw ref sdk sql ssh sum tag ten tip top ui url v1 v2 v3 v4 v5 v6 v7 v8 yes
also auto back base best both call case chat code copy core cost data date
docs done down draft drop each edit else even fail file find flag flow form
free from full gate give gold good half hand hard have head help here hold
hook host idea into join jump just keep kind know last left less line link
list live load lock long look loop main make many mark menu mode more most
move much must name need next node note only open over page part pass past
path plan play plus pull push read real repo rest role room root rule runs
safe same save scan seed seem self send sent ship show side sign site size
skip slow slug some sort spec stay step stop such sure sync take task team
tell term test text than that them then they this thus time tool turn type
unit upon used user very view wait walk want ways week well what when will
with word work wrap year your zero
""".split())
_ELLIPSIS_OPENERS = ("...", "…")

# Every class the classifier can emit. Used to reject a typo'd weight rather
# than silently running an arm with one fewer penalty than it claims.
PENALTY_CLASSES = frozenset({"fragment", "fragment-promoted", "status", "staging"})

# One weight per class, applied multiplicatively to the BM25 score.
#
# The values are not taste: a 125-point sweep over [0.02, 1.0] per class
# produced four distinct outcomes, and every setting at or below 0.6 for a class
# ranked identically. Strength is not a parameter — only whether a class is
# penalized at all. These numbers are a legible constant, nothing more.
#
# `fragment-promoted` is absent on purpose, which is what gates the shape rule
# on status: an unlisted class multiplies by 1.0, so a fragment-shaped note that
# filing already promoted keeps its score. That spares 1,288 notes for a
# measured cost of 0.000 tool-level hit@5 and 0.001 MRR, and it is the shape the
# daemon should implement.
DEFAULT_PENALTY_WEIGHTS = {"fragment": 0.30, "status": 0.60, "staging": 0.30}

# What the 2026-08-07 replicates actually ran: no gate, so a promoted note was
# demoted for its shape like any other fragment. Kept nameable so the twelve
# committed scorecards stay reproducible — a preset that quietly became the
# recommended one would make those numbers unverifiable.
AS_MEASURED_PENALTY_WEIGHTS = {
    "fragment": 0.30, "fragment-promoted": 0.30, "status": 0.60, "staging": 0.30,
}

# How deep to look before re-ranking. A penalty can only promote a note the
# first fetch actually saw, so the window has to be wide enough that a real note
# buried under a wall of fragments can climb into the top five. 200 is ~2.3% of
# the corpus and costs under a millisecond; k alone would make the penalty a
# no-op, since re-sorting five rows cannot introduce a sixth.
PENALTY_OVERFETCH = 200

_STATUS_RE = re.compile(r"^status:\s*(\S+)", re.MULTILINE)
_MINING_RE = re.compile(r"^mining_confidence:", re.MULTILINE)
_ALIASES_RE = re.compile(r"^aliases:\s*(.*)$", re.MULTILINE)
_TAGS_RE = re.compile(r"^tags:\s*(.*)$", re.MULTILINE)
_PROPOSAL_RE = re.compile(r"\A#\s*Proposal\s+\d+\s*:", re.MULTILINE)


def _looks_truncated_slug(stem):
    """True when a slug appears to start partway into a word.

    Deliberately conservative: it fires on a short leading segment that is not a
    real short word, which catches `mber-…`, `rver-…`, `ps-…` and leaves
    `read-multi-agent-collective-memory-vault` alone. It cannot catch every case
    without a dictionary, and it does not need to — this class is 32 files out
    of 8,687 and the ellipsis check below covers most of what it misses.
    """
    first = stem.split("-", 1)[0].lower()
    if not first:
        return False
    if first.isdigit():
        return True
    return len(first) <= 4 and first not in _REAL_SHORT_WORDS


def classify_document(rel, raw):
    """Return the set of rank-penalty classes a note falls into.

    `rel` is the vault-relative POSIX path, `raw` the file's full text.
    Classes are independent and a note can carry several:

      `fragment` — a miner artifact. Detected three ways, because no single
        signal covers the population: the body opens with one of the miner's
        stock lead-ins; the frontmatter carries `mining_confidence`, which only
        the miner writes; or the note sits in one of the two miner-filled
        directories under a slug that starts mid-word.
      `status`   — frontmatter status is unfiled/inbox/superseded/expired.
      `staging`  — a dream-staging proposal. Not in the brief's list, and
        reported separately for that reason. It earns a class because each
        proposal quotes the full text of the two notes it is about, so it is a
        fragment counted twice, and 1,052 of them sit in the corpus.
    """
    flags = set()
    m = _FRONTMATTER_RE.match(raw)
    head = m.group(1) if m else ""
    after = (raw[m.end():] if m else raw).lstrip()

    st = _STATUS_RE.search(head)
    status = st.group(1).strip().strip("'\"").lower() if st else ""

    shaped = False
    if any(after.startswith(o) for o in FRAGMENT_OPENERS):
        shaped = True
    elif _MINING_RE.search(head):
        shaped = True
    elif rel.rsplit("/", 1)[0] in FRAGMENT_SLUG_DIRS:
        stem = rel.rsplit("/", 1)[-1][:-3]
        shaped = _looks_truncated_slug(stem) or after.startswith(_ELLIPSIS_OPENERS)

    # The shape rule splits on whether filing has already passed judgement. Both
    # halves are recorded so the caller decides, by which weights it supplies,
    # whether a promoted fragment is demoted — rather than the index deciding at
    # build time and forcing a rebuild to change its mind.
    if shaped:
        flags.add("fragment-promoted" if status in PROMOTED_STATUSES else "fragment")

    if status in PENALIZED_STATUSES:
        flags.add("status")

    if rel.startswith("desk/scratch/") and (
        rel.endswith(".proposal.md") or _PROPOSAL_RE.match(after)
    ):
        flags.add("staging")

    return flags


def penalty_multiplier(flags, weights):
    """Compound the per-class weights a note's flags earn. 1.0 when unflagged.

    Multiplicative rather than additive so the classes compose without any one
    of them being able to zero a score out — a note that is both a fragment and
    unfiled is demoted twice, and still ranked.
    """
    w = 1.0
    for flag in flags:
        w *= weights.get(flag, 1.0)
    return w


def extract_meta_text(head):
    """The `aliases` + `tags` values as plain text, for the `fields` variant."""
    parts = []
    for rx in (_ALIASES_RE, _TAGS_RE):
        m = rx.search(head)
        if m:
            parts.append(re.sub(r"[\[\],'\"]", " ", m.group(1)))
    return " ".join(" ".join(parts).split())


class CorpusError(RuntimeError):
    """Raised when a corpus surface cannot be built or served."""


# ---------------------------------------------------------------------------
# Corpus walk
# ---------------------------------------------------------------------------

def iter_markdown_paths(vault, exclude_dirs=None):
    """Yield every `.md` path under `vault`, sorted, as `Path` objects.

    Dot-directories are always skipped. `exclude_dirs` is matched against
    directory *names* at any depth, the same shape `recall.py` uses for
    `_EXCLUDE_DIR_NAMES`.
    """
    exclude = set(exclude_dirs or ())
    out = []
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in exclude
        )
        for name in sorted(filenames):
            if name.endswith(".md"):
                out.append(Path(dirpath) / name)
    return out


def corpus_fingerprint(paths, vault):
    """A cheap, exact identity for a corpus state: path + mtime + size of each file.

    Used to decide whether a cached index is still valid. Content hashing all
    17MB would also work and would be slower for no gain — an mtime change we
    treat as a rebuild is at worst a wasted rebuild, and a content change
    without an mtime change cannot happen through any writer this vault has.
    """
    h = hashlib.sha256()
    for p in paths:
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(p.relative_to(vault).as_posix().encode("utf-8"))
        h.update(f"|{st.st_mtime_ns}|{st.st_size}\n".encode("utf-8"))
    h.update(f"|n={len(paths)}".encode("utf-8"))
    return h.hexdigest()


def read_document(path, vault):
    """Return `(relpath, title, body)` for one note, or None if unreadable.

    `title` is the frontmatter `title:` when present, else the filename stem
    with separators spaced out so `vault-path-convention` also matches a query
    phrased as "vault path convention".
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    rel = path.relative_to(vault).as_posix()
    body = raw
    title = None
    m = _FRONTMATTER_RE.match(raw)
    if m:
        body = raw[m.end():]
        tm = _TITLE_RE.search(m.group(1))
        if tm:
            title = tm.group(1).strip().strip("'\"")
    stem_words = path.stem.replace("-", " ").replace("_", " ")
    title = f"{title} {stem_words}" if title else stem_words
    # The frontmatter block itself stays searchable: tags and kind are real
    # query surface ("what's my convention for X" hits `kind: convention`).
    return rel, title, (m.group(1) + "\n" + body if m else body)


def read_document_ex(path, vault):
    """`read_document` plus the two things indexing needs and scoring does not.

    Returns `(rel, title, body, meta, flags)`, or None if unreadable. Split from
    `read_document` rather than folded into it so the vector arm — which wants
    neither the penalty classes nor the `fields` variant's extra column — keeps
    reading exactly the text it read before, and the two arms stay comparable.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    doc = read_document(path, vault)
    if doc is None:
        return None
    rel, title, body = doc
    m = _FRONTMATTER_RE.match(raw)
    meta = extract_meta_text(m.group(1)) if m else ""
    return rel, title, body, meta, classify_document(rel, raw)


# ---------------------------------------------------------------------------
# Lexical surface — SQLite FTS5 + BM25
# ---------------------------------------------------------------------------

def build_lexical_index(vault, db_path, *, exclude_dirs=None, paths=None,
                        variant=DEFAULT_VARIANT, verbose=False):
    """Build (or reuse) the FTS5 index at `db_path`. Returns (sqlite3.Connection, n_docs).

    Reuses an existing index when its stored fingerprint matches the live
    corpus, so a repeated run does not re-read 17MB off a Google Drive mount.
    The fingerprint now covers the variant and the schema version too, so
    switching either rebuilds instead of querying a schema that is not there.

    Every variant stores the same `docflags` table, which is what lets one index
    serve both a penalized and an unpenalized run: the penalty is applied at
    query time, so a with/without comparison is guaranteed to be reading the
    identical index rather than two builds that might differ for another reason.
    """
    db_path = Path(db_path)
    spec = variant_spec(variant)
    if paths is None:
        paths = iter_markdown_paths(vault, exclude_dirs=exclude_dirs)
    fp = f"{_SCHEMA_VERSION}:{variant}:{corpus_fingerprint(paths, vault)}"

    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
            if row and row[0] == fp:
                n = conn.execute("SELECT count(*) FROM docs").fetchone()[0]
                if verbose:
                    print(f"[week1-corpus] lexical index reused ({n} docs, "
                          f"variant {variant})", file=sys.stderr)
                return conn, n
        except sqlite3.Error:
            pass
        conn.close()
        try:
            db_path.unlink()
        except PermissionError:
            # Windows refuses to delete a file another connection still holds
            # open, and POSIX does not — so the "delete and start over" rebuild
            # works everywhere except the one platform where a stale handle is
            # visible. Dropping the tables achieves the same thing through the
            # handle we do have, which is also the honest fix: the goal was
            # never to remove the file, it was to discard what is in it.
            conn = sqlite3.connect(str(db_path))
            for table in ("docs", "meta", "docflags"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.commit()
            conn.close()

    split = spec["fields"] == "split"
    columns = "path UNINDEXED, title, meta, body" if split else "path UNINDEXED, title, body"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            f"CREATE VIRTUAL TABLE docs USING fts5("
            f"{columns}, tokenize='{spec['tokenize']}')"
        )
    except sqlite3.OperationalError as e:
        raise CorpusError(
            f"SQLite has no FTS5 support in this interpreter ({sys.executable}): {e}"
        ) from e
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE docflags (path TEXT PRIMARY KEY, flags TEXT)")

    n = 0
    flag_counts = {}
    started = time.monotonic()
    for p in paths:
        doc = read_document_ex(p, vault)
        if doc is None:
            continue
        rel, title, body, meta_text, flags = doc
        if split:
            conn.execute(
                "INSERT INTO docs (path, title, meta, body) VALUES (?, ?, ?, ?)",
                (rel, title, meta_text, body))
        else:
            conn.execute(
                "INSERT INTO docs (path, title, body) VALUES (?, ?, ?)",
                (rel, title, body))
        if flags:
            conn.execute("INSERT INTO docflags VALUES (?, ?)",
                         (rel, ",".join(sorted(flags))))
            for f in flags:
                flag_counts[f] = flag_counts.get(f, 0) + 1
        n += 1
        if verbose and n % 1000 == 0:
            print(f"[week1-corpus] indexed {n}/{len(paths)}…", file=sys.stderr)
    conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fp,))
    conn.execute("INSERT INTO meta VALUES ('variant', ?)", (variant,))
    conn.commit()
    if verbose:
        print(
            f"[week1-corpus] lexical index built: {n} docs in "
            f"{time.monotonic() - started:.1f}s (variant {variant}) -> {db_path}",
            file=sys.stderr,
        )
        print(f"[week1-corpus] penalty classes: "
              f"{', '.join(f'{k}={v}' for k, v in sorted(flag_counts.items())) or 'none'}",
              file=sys.stderr)
    return conn, n


def load_doc_flags(conn):
    """`{path: frozenset(flags)}` for every flagged note. Read once, held in memory.

    Only flagged notes are stored, so this is ~6k rows on this vault and an
    empty dict on a corpus with nothing to demote.
    """
    try:
        rows = conn.execute("SELECT path, flags FROM docflags").fetchall()
    except sqlite3.Error:
        return {}
    return {p: frozenset(f.split(",")) for p, f in rows if f}


def _sanitize_fts_query(query):
    """Rewrite free text into an FTS5 expression that cannot be a syntax error.

    Every token is quoted (so `foo-bar`, `v5.3`, and `what's` are literals, not
    operators) and OR-joined, which is the widest reading of the agent's intent.
    """
    tokens = _FTS_TOKEN_RE.findall(query)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


_QUOTED_RE = re.compile(r'"([^"]+)"')


def or_join_query(query):
    """OR-join a query's terms, keeping any double-quoted phrase intact.

    The difference from `_sanitize_fts_query` is the phrase handling, and it is
    the whole point: an agent that writes `"F0" prompt pack` means the literal
    `F0` and then three hints, and flattening the quotes throws away the one
    piece of the query it was most sure about.

    Why this exists at all: FTS5's bare `docs MATCH 'a b c'` is an implicit AND
    across every term, so a six-word paraphrase has to find a note containing
    all six words or it returns nothing. On the 2026-08-06 Opus run that emptied
    32 of 206 queries outright and left 62 returning fewer than five results.
    Ranking cannot rescue an empty result set, which is why this is measured
    alongside the rank penalty rather than after it.
    """
    query = (query or "").strip()
    if not query:
        return None
    terms, rest = [], []
    pos = 0
    for m in _QUOTED_RE.finditer(query):
        rest.append(query[pos:m.start()])
        inner = m.group(1).strip()
        if inner:
            terms.append('"' + inner.replace('"', "") + '"')
        pos = m.end()
    rest.append(query[pos:])
    for tok in _FTS_TOKEN_RE.findall(" ".join(rest)):
        # `OR`/`AND`/`NOT` written by the agent are operators it meant, not terms
        # to search for; dropping them here is what makes the join total.
        if tok.upper() in ("OR", "AND", "NOT", "NEAR"):
            continue
        terms.append(f'"{tok}"')
    if not terms:
        return None
    return " OR ".join(terms)


QUERY_MODES = ("as-is", "or")


def search_lexical(conn, query, k=5, *, weights=None, doc_flags=None, penalty=None,
                   query_mode="as-is"):
    """BM25-ranked search. Returns `(results, note)`.

    `results` is a list of `{path, score, snippet}`, best first. `score` is the
    negated `bm25()` value, so larger is better and the sign convention matches
    the vector arm — SQLite's own bm25() is negative-is-better, which reads as
    a bug to anyone comparing the two tools' output side by side.

    `note` is a human-readable string when the raw query was not valid FTS5 and
    a sanitized form was used instead, else None. The agent is told, because a
    silent rewrite would make its next reformulation a guess. A hard error would
    be worse still: it would burn one of six tool calls and quietly bias the
    measurement toward whichever arm happened to phrase queries more plainly.

    With `penalty` set, the query fetches `PENALTY_OVERFETCH` rows instead of
    `k`, multiplies each score by the weight its penalty classes earn, re-sorts,
    and returns the top `k`. Rows are re-ordered and never dropped: a penalized
    note that is the best thing the corpus has still comes back first, because
    every other row was multiplied by 1.0 and it was multiplied by something
    greater than zero. `score` in the output is the adjusted score, with the
    original alongside it as `raw_score` and the classes as `penalty` so a
    demotion is visible in the call log rather than inferred from a number
    moving.
    """
    query = (query or "").strip()
    if not query:
        return [], "empty query"
    weights = tuple(weights or _BM25_WEIGHTS)
    prefix_note = None
    if query_mode == "or":
        joined = or_join_query(query)
        if joined is None:
            return [], "query contained no searchable terms"
        # The note is for the agent's benefit, so it only fires when the rewrite
        # actually widened anything. A one-term query is rewritten to itself in
        # quotes, and announcing a change there would just be noise the agent
        # has to reason about.
        if " OR " in joined:
            prefix_note = "matched any of the query's terms, best-matching first"
        query = joined
    limit = max(k, PENALTY_OVERFETCH) if penalty else k
    placeholders = ", ".join("?" * len(weights))
    # The snippet column index is the body column, which is last in either layout.
    sql = (
        f"SELECT path, bm25(docs, {placeholders}) AS s, "
        f"snippet(docs, {len(weights) - 1}, '[', ']', ' … ', 24) "
        f"FROM docs WHERE docs MATCH ? ORDER BY s LIMIT ?"
    )
    note = None
    try:
        rows = conn.execute(sql, (*weights, query, limit)).fetchall()
    except sqlite3.OperationalError:
        sanitized = _sanitize_fts_query(query)
        if sanitized is None:
            return [], "query contained no searchable terms"
        note = (
            f"query was not valid FTS5 syntax; searched for any of its terms "
            f"instead ({sanitized})"
        )
        try:
            rows = conn.execute(sql, (*weights, sanitized, limit)).fetchall()
        except sqlite3.OperationalError as e:
            return [], f"search failed: {e}"

    results = [
        {"path": r[0], "score": round(-r[1], 4), "snippet": " ".join((r[2] or "").split())}
        for r in rows
    ]
    if penalty:
        doc_flags = doc_flags or {}
        for r in results:
            flags = doc_flags.get(r["path"], frozenset())
            mult = penalty_multiplier(flags, penalty)
            r["raw_score"] = r["score"]
            r["score"] = round(r["score"] * mult, 4)
            if flags:
                r["penalty"] = ",".join(sorted(flags))
        # Ties broken by path so the ordering is total and a re-run is identical.
        results.sort(key=lambda r: (-r["score"], r["path"]))
        results = results[:k]
    return results, note or prefix_note


# ---------------------------------------------------------------------------
# Vector surface — brute-force cosine over chunk embeddings
# ---------------------------------------------------------------------------

def _load_embedder(mode="local"):
    """Return a `encode(list_of_texts) -> ndarray` callable with the model resident.

    Reaches past `embed.embed_text` for the batch path on purpose: `embed_text`
    encodes one string per call, and 13k chunks through a one-at-a-time forward
    pass is an order of magnitude slower than batching them. Mode resolution,
    model choice, cache directory, and the `EmbeddingUnavailable` contract all
    still come from `embed.py` — this borrows its loaded model, it does not
    reimplement its configuration.
    """
    import numpy as np
    import embed  # noqa: E402

    if embed._resolve_mode(mode) == "stub":
        def encode_stub(texts, batch_size=64):
            return np.asarray([embed._embed_stub(t) for t in texts], dtype="float32")
        return encode_stub

    # Force the model resident through embed.py's own loader, then reuse the
    # instance it cached. This is the warm load: it happens once, here.
    embed._embed_local("warm")
    model = embed._LOCAL_MODEL_INSTANCES[embed._resolve_model()]

    def encode_local(texts, batch_size=64):
        return np.asarray(
            model.encode(list(texts), batch_size=batch_size, show_progress_bar=False),
            dtype="float32",
        )
    return encode_local


def _normalize(matrix):
    import numpy as np
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _doc_hash(title, body):
    return hashlib.sha256((title + "\0" + body).encode("utf-8")).hexdigest()[:16]


def build_vector_index(
    vault, cache_path, *, exclude_dirs=None, paths=None, mode="local", verbose=False
):
    """Build (or extend) the chunk-embedding matrix. Returns `(encode, doc_paths, matrix)`.

    `matrix` is L2-normalized float32 of shape (n_chunks, dim); `doc_paths[i]` is
    the note each row came from, so a query is one matmul plus a per-document max.

    The cache is incremental, keyed per document by a hash of its content. Only
    notes that are new or edited since the last build get embedded; everything
    else reuses its stored rows. This matters more than it looks: embedding the
    whole vault costs ~35 minutes at ~15 chunks/s, and this vault turns over
    about 1,500 notes a week, so a whole-corpus cache keyed on one fingerprint
    would re-pay that half hour on essentially every run. The design calls for
    re-running this scorecard on a cadence, and a 35-minute startup tax is how a
    cadence quietly becomes a one-off.

    Rows for deleted or edited notes are dropped rather than left to rot, so the
    matrix never serves a vector for text the vault no longer contains.
    """
    import numpy as np

    cache_path = Path(cache_path)
    if paths is None:
        paths = iter_markdown_paths(vault, exclude_dirs=exclude_dirs)

    encode = _load_embedder(mode)

    import chunking  # noqa: E402

    started = time.monotonic()
    docs = []
    for i, p in enumerate(paths):
        doc = read_document(p, vault)
        if doc is None:
            continue
        rel, title, body = doc
        docs.append((rel, title, body, _doc_hash(title, body)))
        if verbose and (i + 1) % 2000 == 0:
            print(f"[week1-corpus] read {i + 1}/{len(paths)}…", file=sys.stderr)
    if not docs:
        raise CorpusError(f"no readable .md files under {vault}")

    cached_vectors, cached_rows = None, {}
    if cache_path.exists():
        try:
            cached = np.load(str(cache_path), allow_pickle=False)
            if str(cached["mode"]) == mode:
                cached_vectors = cached["vectors"]
                for i, (rel, h) in enumerate(
                    zip(cached["doc_paths"], cached["doc_hashes"])
                ):
                    cached_rows.setdefault((str(rel), str(h)), []).append(i)
        except (OSError, KeyError, ValueError):
            cached_vectors, cached_rows = None, {}

    reuse_indices, reuse_meta, pending = [], [], []
    for rel, title, body, h in docs:
        rows = cached_rows.get((rel, h)) if cached_vectors is not None else None
        if rows:
            reuse_indices.extend(rows)
            reuse_meta.extend([(rel, h)] * len(rows))
            continue
        for chunk in chunking.chunk_text(
            body, chunk_chars=VECTOR_CHUNK_CHARS, overlap_chars=VECTOR_CHUNK_OVERLAP
        ) or [""]:
            # The title rides on every chunk: a mid-document passage otherwise
            # loses all trace of what note it belongs to, which is most of the
            # signal on a vault whose filenames name their subject.
            pending.append((rel, h, f"{title}\n\n{chunk}"))

    if verbose and cached_vectors is not None:
        reused_docs = len(set(reuse_meta))
        print(
            f"[week1-corpus] cache: reusing {len(reuse_indices)} chunks from "
            f"{reused_docs} unchanged notes; {len(docs) - reused_docs} notes need "
            f"embedding",
            file=sys.stderr,
        )

    texts = [t for _, _, t in pending]
    doc_paths = [rel for rel, _ in reuse_meta] + [rel for rel, _, _ in pending]
    doc_hashes = [h for _, h in reuse_meta] + [h for _, h, _ in pending]
    # Encoded in visible slices rather than one opaque `encode(texts)` call. At
    # roughly 15 chunks/s this is a ~35-minute build, and a half-hour of silence
    # is indistinguishable from a hang — which is precisely the diagnosis this
    # experiment was set up to avoid making by guesswork.
    # Encoded in visible slices rather than one opaque `encode(texts)` call. A
    # full build is ~35 minutes at ~15 chunks/s, and half an hour of silence is
    # indistinguishable from a hang — which is exactly the diagnosis this
    # experiment exists to stop making by guesswork.
    if verbose and texts:
        print(
            f"[week1-corpus] embedding {len(texts)} chunks (cached to "
            f"{cache_path.name})…", file=sys.stderr,
        )
    slice_size = 512
    encoded, started_embed = [], time.monotonic()
    for start in range(0, len(texts), slice_size):
        encoded.append(encode(texts[start:start + slice_size]))
        done = min(start + slice_size, len(texts))
        if verbose:
            elapsed = time.monotonic() - started_embed
            rate = done / elapsed if elapsed else 0.0
            eta = (len(texts) - done) / rate if rate else 0.0
            print(
                f"[week1-corpus] embedded {done}/{len(texts)} chunks "
                f"({rate:.1f}/s, ~{eta / 60:.1f} min left)",
                file=sys.stderr, flush=True,
            )

    blocks = []
    if reuse_indices:
        blocks.append(cached_vectors[reuse_indices])
    if encoded:
        blocks.append(_normalize(np.vstack(encoded)))
    vectors = np.vstack(blocks) if len(blocks) > 1 else blocks[0]

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        str(cache_path),
        vectors=vectors,
        doc_paths=np.array(doc_paths),
        doc_hashes=np.array(doc_hashes),
        mode=np.array(mode),
    )
    if verbose:
        print(
            f"[week1-corpus] vector index ready: {vectors.shape[0]} chunks from "
            f"{len(docs)} notes ({len(texts)} newly embedded) in "
            f"{time.monotonic() - started:.1f}s -> {cache_path}",
            file=sys.stderr,
        )
    return encode, doc_paths, vectors


def search_vector(encode, doc_paths, vectors, query, k=5):
    """Brute-force cosine search, max-passage aggregated per document.

    Returns `(results, note)` in the same shape `search_lexical` returns, so the
    two tools are interchangeable to everything above this module.
    """
    import numpy as np

    query = (query or "").strip()
    if not query:
        return [], "empty query"
    qv = _normalize(encode([query]))[0]
    # numpy 2.0 on Apple's Accelerate BLAS raises divide-by-zero / overflow /
    # invalid flags on float32 matmul even when both operands are finite and
    # unit-norm — the vectorized kernel sets FPU flags on its padding lanes.
    # Verified spurious here, not assumed: against a float64 einsum reference on
    # this exact matrix the results agreed to 5e-9, and a 64-row slice raises the
    # same flags as the full 33k-row product. The suppression is narrow and the
    # output is still checked below, so a real non-finite result would surface
    # rather than hide behind this.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        sims = vectors @ qv
    if not np.isfinite(sims).all():
        raise CorpusError(
            "vector search produced non-finite similarities — the embedding "
            "matrix or query vector is corrupt, not merely flagged"
        )
    best = {}
    for idx, path in enumerate(doc_paths):
        s = float(sims[idx])
        if s > best.get(path, -2.0):
            best[path] = s
    top = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return [{"path": p, "score": round(s, 4), "snippet": ""} for p, s in top], None


def main(argv=None):
    """Build both surfaces without running an experiment — the slow part, on demand."""
    import argparse

    sys.path.insert(0, str(_HERE))
    from week1_retrieval_experiment import resolve_vault  # noqa: E402

    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--vault-path", default=None)
    ap.add_argument("--work-dir", default=str(Path.home() / ".agentm" / "week1-experiment"))
    ap.add_argument("--exclude-dir", action="append", default=[])
    ap.add_argument("--embed-mode", default="local", choices=("local", "stub"))
    ap.add_argument("--lexical-only", action="store_true")
    ap.add_argument("--variant", default=DEFAULT_VARIANT,
                    choices=sorted(LEXICAL_VARIANTS), help="lexical index variant")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    vault = resolve_vault(args.vault_path)
    work = Path(args.work_dir)
    paths = iter_markdown_paths(vault, exclude_dirs=args.exclude_dir)
    print(f"[week1-corpus] {len(paths)} .md files under {vault}", file=sys.stderr)
    build_lexical_index(vault, work / lexical_db_name(args.variant), paths=paths,
                        variant=args.variant, verbose=True)
    if not args.lexical_only:
        build_vector_index(
            vault, work / f"vectors-{args.embed_mode}.npz",
            paths=paths, mode=args.embed_mode, verbose=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
