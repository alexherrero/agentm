#!/usr/bin/env python3
"""notes_link_discovery — read-only "missing link" audit for personal notes (V4 #43).

The complement to `vault_lint` (#33). Where vault_lint validates the *agent-shaped*
`AgentMemory/` entries and **skips** the operator's free-form personal notes, this
module audits **only those skipped personal notes** for *missing connections between
them*: "these two notes look related but aren't `[[linked]]` — consider connecting
them."

The corpus is the enclosing Obsidian vault **excluding `AgentMemory/` + `.obsidian/`**
(reusing `vault_lint._obsidian_root`). The exclusion **is** the domain boundary
(DC-2): suggestions are inherently personal↔personal — a personal note is never
related-linked to an `AgentMemory/` entry, because `AgentMemory/` is never in the
corpus as either source or target.

Relatedness is **content-based** (DC-3): the operator's notes have ~no tags, ~no
wikilinks, and only `title`/`created`/`updated` frontmatter, so those are dead
signals. v1 = hand-rolled **TF-IDF over title+body** + an **inverted index** (only
compare notes sharing terms) + **cosine similarity**, surfacing related-but-unlinked
pairs above a threshold. Folder + date proximity are available as weak secondary
context. (Task 3 adds an embedding signal alongside this lexical one.)

**Strictly read-only** (DC-1) — opens personal notes for read only, never edits one
and never auto-creates a link. The `report` mode writes a single operator-review
file to `AgentMemory/_meta/` (agent-controlled output), never to a personal note.
The operator applies suggestions by hand (A3 — these are *his* notes).

Stdlib-only (no sklearn — hand-rolled TF-IDF), cross-platform.

CLI:
    python3 notes_link_discovery.py [--vault PATH] [--format json|text]
                                    [--top N] [--min-score X]
    python3 notes_link_discovery.py --report [--out PATH]   # operator-review md
    python3 notes_link_discovery.py --embeddings [--report] # + semantic signal
    python3 notes_link_discovery.py --apply                 # opt-in: write links
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tarfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vault_lint  # noqa: E402  (reuse _obsidian_root + parse_frontmatter — same skill dir)

# Directories that are NOT operator personal notes — excluded from the corpus.
# `AgentMemory/` is the agent's own vault (the hard domain boundary, DC-2);
# `.obsidian/` is Obsidian's config; `.trash` is Obsidian's soft-delete bin.
_EXCLUDE_DIRS = frozenset({"AgentMemory", ".obsidian", ".trash", ".git"})

# Defaults — tuned against the live 397-note dogfood (task 2).
_DEFAULT_MIN_SCORE = 0.18
_DEFAULT_TOP = 40
# Embedding signal (task 3). BGE-large cosine runs hot (~0.3-0.5 even for
# unrelated prose), so the semantic threshold sits well above the TF-IDF one.
_DEFAULT_EMBED_MIN_SCORE = 0.70
# Cap embed input length — BGE truncates at ~512 tokens (~2k chars) anyway, and
# title + lede is the most topical signal. Keeps embedding fast + representative.
_EMBED_CHAR_CAP = 2000
# A term must appear in at least this many notes to be indexed at all (drops
# per-note typos/unique noise) but fewer than this fraction of the corpus (drops
# ubiquitous boilerplate the IDF would already down-weight — a hard cap keeps the
# inverted-index postings short so the pair scan stays bounded).
_MIN_DF = 2
_MAX_DF_RATIO = 0.5

# Tokenizer: lowercase alphanumeric runs of >= 3 chars (drops "a", "of", digits-
# only noise like years bleak less). Apostrophes are split (don't -> don, t-drop).
_TOKEN_RE = re.compile(r"[a-z][a-z0-9]{2,}")

# Many personal notes are clipped/pasted web content, so the raw text carries
# HTML tags, inline CSS, image embeds, and URLs whose tokens (hex colors, font
# names, `image1`) otherwise dominate the TF-IDF signal — the live #43 dogfood
# showed `fffaa5`/`serif`/`image1` crowding out real shared terms. Strip that
# markup before tokenizing so relatedness reflects prose, not clip boilerplate.
_MD_IMG_RE = re.compile(r"!\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\)")
_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>.*?</style>|<script[^>]*>.*?</script>",
                             re.DOTALL | re.IGNORECASE)
_CSS_BRACE_RE = re.compile(r"\{[^{}]*\}")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_ENTITY_RE = re.compile(r"&[a-z#0-9]+;", re.IGNORECASE)
# `#`-prefixed hex colors (#fefbbf, #fff, #f497d). Stripped contextually so
# all-alpha hex like `fefbbf` is dropped without false-dropping prose words
# (`decade`, `facade`) that the bare-token hex filter must keep.
_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
# A hex token (color/id like `fffaa5`, `f497d`) — all hex chars AND at least one
# digit (so real words made only of [a-f] like "decade"/"facade" are kept).
_HEX_TOKEN_RE = re.compile(r"^[0-9a-f]+$")
# Generic enumerated media refs: image1, img2, figure3, screenshot1, …
_MEDIA_TOKEN_RE = re.compile(r"^(?:image|img|figure|fig|photo|screenshot|icon|logo)\d+$")

# A compact English stopword set + Markdown/Obsidian + web-clip/CSS boilerplate.
# Hand-rolled (stdlib-only, no nltk). IDF already down-weights common terms; this
# just keeps the inverted index from being dominated by function words / markup.
_STOPWORDS = frozenset("""
the and that have for not with you this but his from they she will would there
their what about which when make can like time just him know take people into
year your good some could them than then now look only come its over think also
back after use two how our work first well way even new want because any these
give day most us was are were has had been being who why all out off too very
get got going one let going lets per via vs etc eg ie aka onto upon within
without across among around before behind below beneath beside between beyond
during except inside near since toward under until upon while https http www com
org net html md png jpg jpeg gif note notes link links page see also ref
serif sans monospace helvetica verdana tahoma calibri arial roboto cellpadding
cellspacing colspan rowspan valign nbsp rgba tbody thead
""".split())
# NB: only tokens that are ~never English/Spanish prose go above. `_strip_markup`
# already removes <style>/<script> blocks, inline `style=…`, `{…}` CSS rules, and
# `#hex` colors — so common words that double as CSS keywords (font, family,
# width, color, text, block, auto, times, …) are NOT stopwords; dropping them
# would blind a family-history corpus to its own vocabulary.

# The operator's corpus is bilingual (English + Spanish church/family notes), so
# Spanish function words leak in as "distinctive" terms. A compact Spanish
# stopword set, deliberately EXCLUDING tokens that are also meaningful English
# words (`son`, `sin`, `ante`, `como`-no) so we don't blind the English side.
_STOPWORDS_ES = frozenset("""
que los las una unos unas del con por para pero sus les ese esa eso eran ellos
ellas esto esta este estos estas fue fueron han hay muy sobre tambien hasta desde
cuando todo todos toda todas nos porque donde quien cual cuales entre hace asi
aqui alli cada otro otra otros otras mismo misma tan tanto tiene tienen puede
pueden debe deben hacer dice dijo dijeron segun aunque mientras ademas nuestra
nuestro nuestros nuestras ustedes nosotros vosotros aquel aquella aquello cuyo
cuya hacia tras durante mediante respecto solo solamente tambien siempre nunca
""".split())


# -----------------------------------------------------------------------------
# Data shapes
# -----------------------------------------------------------------------------

@dataclass
class Note:
    """A parsed personal note (corpus member)."""
    path: Path
    rel: str               # POSIX path relative to the Obsidian root (no .md)
    title: str
    folder: str            # top-level folder under the root ("" if at root)
    created: str
    updated: str
    body: str
    links: set = field(default_factory=set)   # resolved wikilink targets (stems + rel paths)
    tf: dict = field(default_factory=dict)     # term -> raw count (title double-weighted)


@dataclass
class Suggestion:
    a_rel: str
    b_rel: str
    a_title: str
    b_title: str
    a_folder: str
    b_folder: str
    score: float
    shared_terms: list      # top distinctive shared terms, by contribution (tfidf)
    same_folder: bool
    signal: str = "tfidf"   # "tfidf" (lexical overlap) | "embedding" (semantic)

    def to_dict(self) -> dict:
        return {
            "a": self.a_rel,
            "b": self.b_rel,
            "a_title": self.a_title,
            "b_title": self.b_title,
            "a_folder": self.a_folder,
            "b_folder": self.b_folder,
            "score": round(self.score, 4),
            "shared_terms": self.shared_terms,
            "same_folder": self.same_folder,
            "signal": self.signal,
        }


# -----------------------------------------------------------------------------
# Corpus build
# -----------------------------------------------------------------------------

def _strip_markup(text: str) -> str:
    """Remove HTML/CSS/markdown-embed/URL markup so its tokens don't pollute the
    TF-IDF signal. Order matters: drop whole <style>/<script> blocks before the
    generic tag strip, then leftover CSS `{…}` rules, image embeds, URLs,
    entities."""
    text = _STYLE_BLOCK_RE.sub(" ", text)
    text = _MD_IMG_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = _CSS_BRACE_RE.sub(" ", text)
    text = _HEX_COLOR_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _ENTITY_RE.sub(" ", text)
    return text


def _is_noise_token(t: str) -> bool:
    if t in _STOPWORDS or t in _STOPWORDS_ES:
        return True
    if _MEDIA_TOKEN_RE.match(t):
        return True
    # Hex color/id: all hex chars with at least one digit (keeps all-alpha words).
    if any(c.isdigit() for c in t) and _HEX_TOKEN_RE.match(t):
        return True
    return False


def _tokenize(text: str) -> list:
    return [t for t in _TOKEN_RE.findall(_strip_markup(text).lower())
            if not _is_noise_token(t)]


def _title_from(path: Path, fm: Optional[dict]) -> str:
    if fm:
        t = (fm.get("title") or "").strip().strip("'\"")
        if t:
            return t
    return path.stem


def _resolve_link(target: str) -> tuple:
    """Normalize a raw `[[target]]` to (stem, rel-or-None) for self-link matching.
    Strips alias, anchor, and a trailing .md."""
    t = target.split("|", 1)[0].split("#", 1)[0].split("^", 1)[0].strip()
    t = t.strip("/")
    if t.endswith(".md"):
        t = t[:-3]
    if not t:
        return "", None
    if "/" in t:
        return t.rsplit("/", 1)[-1], t
    return t, None


def build_corpus(vault: Path) -> list:
    """Walk the Obsidian root, parse every personal note (excluding AgentMemory/
    + Obsidian config), return a list[Note]. Read-only."""
    root = vault_lint._obsidian_root(Path(vault))
    notes = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded dirs in-place so os.walk doesn't descend into them.
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            p = Path(dirpath) / fn
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm, _order, body = vault_lint.parse_frontmatter(text)
            try:
                rel = p.relative_to(root).with_suffix("").as_posix()
            except ValueError:
                continue
            parts = rel.split("/")
            folder = parts[0] if len(parts) > 1 else ""
            # Links come from the FULL body (so a prior `--apply` Related section
            # counts as already-linked for dedup) …
            links = set()
            for m in vault_lint._WIKILINK_RE.finditer(body):
                stem, relpath = _resolve_link(m.group(1))
                if stem:
                    links.add(stem)
                if relpath:
                    links.add(relpath)
            # … but the SCORING body excludes our own marked `## Related` section.
            # Otherwise injected `[[link]]` text would feed back into the TF-IDF /
            # embedding signal and make linked notes look ever-more-similar — a
            # loop that breaks `--apply` idempotency on clustered series.
            scoring_body = _split_related(body)[0]
            note = Note(
                path=p,
                rel=rel,
                title=_title_from(p, fm),
                folder=folder,
                created=(fm.get("created", "").strip() if fm else ""),
                updated=(fm.get("updated", "").strip() if fm else ""),
                body=scoring_body,
                links=links,
            )
            # Title terms count double — a shared title term is a stronger signal
            # than a shared body term.
            counts = defaultdict(int)
            for tok in _tokenize(note.title):
                counts[tok] += 2
            for tok in _tokenize(note.body):
                counts[tok] += 1
            note.tf = dict(counts)
            notes.append(note)
    return notes


# -----------------------------------------------------------------------------
# TF-IDF + inverted index + cosine
# -----------------------------------------------------------------------------

@dataclass
class Model:
    notes: list                      # list[Note]
    idf: dict                        # term -> idf weight
    postings: dict                   # term -> list[int] (note indices)
    vectors: list                    # list[dict] term -> tf-idf weight (l2-normalized)
    norms: list                      # parallel l2 norms (== 1.0 unless degenerate)


def build_model(notes: list, *, min_df: int = _MIN_DF,
                max_df_ratio: float = _MAX_DF_RATIO) -> Model:
    n = len(notes)
    df = defaultdict(int)
    for note in notes:
        for term in note.tf:
            df[term] += 1
    # The boilerplate cap is `max_df_ratio * n`. Apply it ONLY when it sits above
    # the min_df floor; below that (corpus smaller than min_df/max_df_ratio notes)
    # the band [min_df, ratio_cap] would collapse to a single point (or invert),
    # silently deleting the very distinctive term a genuine pair shares — e.g. at
    # n=3 a term that a third note also mentions (df=3) would be dropped and the
    # vocab can collapse to empty. In that small-corpus regime there's not enough
    # evidence to call a term "too common", so disable the cap and let IDF do the
    # down-weighting. (Do NOT floor with max(min_df, …): that's what inverted the
    # band.)
    ratio_cap = int(max_df_ratio * n) if n else 0
    max_df = ratio_cap if ratio_cap >= min_df else n
    # Keep terms in the document-frequency band [min_df, max_df]. A term shared by
    # only one note can't connect a pair; a near-ubiquitous term is boilerplate.
    vocab = {t for t, c in df.items() if c >= min_df and c <= max_df}
    idf = {t: math.log((n + 1) / (df[t] + 1)) + 1.0 for t in vocab}

    postings = defaultdict(list)
    vectors = []
    norms = []
    for i, note in enumerate(notes):
        vec = {}
        for term, raw in note.tf.items():
            if term not in vocab:
                continue
            # sublinear tf damps long notes dominating on sheer repetition.
            w = (1.0 + math.log(raw)) * idf[term]
            if w > 0:
                vec[term] = w
        norm = math.sqrt(sum(w * w for w in vec.values()))
        if norm > 0:
            for term in vec:
                vec[term] /= norm
        for term in vec:
            postings[term].append(i)
        vectors.append(vec)
        norms.append(norm)
    return Model(notes=notes, idf=idf, postings=dict(postings),
                 vectors=vectors, norms=norms)


def _already_linked(a: Note, b: Note) -> bool:
    """True if either note already wikilinks the other (by stem or rel path).

    Known limitation: a bare `[[stem]]` link is matched by stem, so when two
    distinct notes share a filename stem (`Work/daily`, `Journal/daily`) a bare
    link to one can falsely suppress a suggestion to the other. This mirrors
    Obsidian's own bare-link ambiguity, costs at most one missed *suggestion*
    (never a wrong write — the tool is suggest-only), and is near-impossible in
    this corpus (≈1/397 notes carry any wikilink), so it's accepted, not guarded."""
    a_stem = a.path.stem
    b_stem = b.path.stem
    if b_stem in a.links or b.rel in a.links:
        return True
    if a_stem in b.links or a.rel in b.links:
        return True
    return False


def score_pairs(model: Model, *, min_score: float = _DEFAULT_MIN_SCORE,
                top: int = _DEFAULT_TOP) -> list:
    """Cosine-score every candidate pair (sharing >= 1 indexed term), drop
    already-linked + below-threshold pairs, return the top-K Suggestions."""
    notes = model.notes
    vectors = model.vectors
    # Accumulate dot products only over pairs that share a posting (inverted
    # index) — never the full O(n^2) cross product.
    dot = defaultdict(float)
    contrib = defaultdict(lambda: defaultdict(float))  # (i,j) -> term -> contribution
    for term, plist in model.postings.items():
        if len(plist) < 2:
            continue
        for x in range(len(plist)):
            i = plist[x]
            wi = vectors[i].get(term, 0.0)
            if wi == 0.0:
                continue
            for y in range(x + 1, len(plist)):
                j = plist[y]
                wj = vectors[j].get(term, 0.0)
                if wj == 0.0:
                    continue
                c = wi * wj
                key = (i, j)
                dot[key] += c
                contrib[key][term] += c

    suggestions = []
    for (i, j), sim in dot.items():
        if sim < min_score:
            continue
        a, b = notes[i], notes[j]
        if _already_linked(a, b):
            continue
        shared = sorted(contrib[(i, j)].items(), key=lambda kv: kv[1], reverse=True)
        shared_terms = [t for t, _ in shared[:6]]
        suggestions.append(Suggestion(
            a_rel=a.rel, b_rel=b.rel,
            a_title=a.title, b_title=b.title,
            a_folder=a.folder, b_folder=b.folder,
            score=sim, shared_terms=shared_terms,
            same_folder=(a.folder == b.folder and a.folder != ""),
        ))
    # Highest score first; stable tiebreak on the note paths for determinism.
    suggestions.sort(key=lambda s: (-s.score, s.a_rel, s.b_rel))
    if top and top > 0:
        suggestions = suggestions[:top]
    return suggestions


def discover(vault: Path, *, min_score: float = _DEFAULT_MIN_SCORE,
             top: int = _DEFAULT_TOP) -> tuple:
    """End-to-end: corpus -> model -> ranked suggestions. Returns (notes, suggestions)."""
    notes = build_corpus(vault)
    model = build_model(notes)
    suggestions = score_pairs(model, min_score=min_score, top=top)
    return notes, suggestions


# -----------------------------------------------------------------------------
# Embedding signal (task 3) — semantic relatedness the lexical TF-IDF misses
# -----------------------------------------------------------------------------
#
# A SECOND, independent relatedness signal: embed each personal note (title +
# lede) with the memory skill's local BGE model (embed.py) and cosine-score the
# pairs. This catches notes about the same topic/person/event that DON'T share
# surface vocabulary — the connections a no-tags/no-links corpus needs most and
# that TF-IDF structurally can't see.
#
# Domain separation (DC-2): the personal-notes embeddings live in their OWN
# cache under <vault>/_meta/notes-embeddings.json — never the AgentMemory
# sqlite-vec index (vec_index.py). The cache is a derived artifact keyed by
# content hash, so re-runs only re-embed changed notes; it is NEVER a write to a
# personal note.
#
# Graceful-skip (DC: matches recall.py): if sentence-transformers isn't
# installed AND a note needs embedding, the whole signal is skipped and the
# report falls back to TF-IDF-only — never a crash.


def default_embed_index_path(vault: Path) -> Path:
    """`<vault>/_meta/notes-embeddings.json` — the personal-notes embedding cache,
    deliberately separate from AgentMemory's `vec-index.db` (DC-2)."""
    return Path(vault) / "_meta" / "notes-embeddings.json"


def _embed_input(note: "Note") -> str:
    """The text fed to the embedder: title + markup-stripped body, capped. Title
    leads (most topical); markup is stripped so clip-HTML doesn't skew vectors."""
    text = note.title + "\n" + _strip_markup(note.body)
    return text[:_EMBED_CHAR_CAP]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(vec: list) -> list:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _cosine_unit(a: list, b: list) -> float:
    """Dot product of two already-unit-normalized vectors (== cosine). Returns 0
    for a dimension mismatch — `zip` would otherwise silently truncate to the
    shorter vector and report a garbage score (belt-and-suspenders; embed_corpus
    already guarantees a uniform dimension)."""
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _load_embed_cache(cache_path: Optional[Path]) -> dict:
    if not cache_path:
        return {}
    try:
        data = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_embed_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")


def embed_corpus(notes: list, *, mode: Optional[str] = None,
                 cache_path: Optional[Path] = None) -> Optional[dict]:
    """Return {rel: unit-normalized embedding} for the corpus, or None if the
    embedding signal is unavailable (sentence-transformers missing AND a note
    needs a fresh embed). Caches raw vectors by content hash at `cache_path`
    (under _meta/) so re-runs only re-embed changed notes.

    Dimension safety: the cache key is content-only, so a cache written under a
    different embedding model/dimension (the documented EMBEDDING_DIM 384->1024
    upgrade, or an AGENT_TOOLKIT_EMBEDDING_MODEL swap) would mix stale-dim cache
    hits with fresh-dim embeds — and `_cosine_unit`'s zip would truncate the
    mismatch into garbage scores. So fresh embeds define the authoritative dim
    this run; any reused cache entry whose dim differs is re-embedded (mirrors
    vec_index.py's dim-mismatch rebuild). The returned dict is always uniform."""
    import embed  # same skill dir; lazy so TF-IDF-only paths never import torch

    cache = _load_embed_cache(cache_path)
    by_rel = {n.rel: n for n in notes}
    hashes: dict = {}
    raws: dict = {}            # rel -> raw vec (final)
    pending: list = []         # rels needing a fresh embed
    fresh_dim: Optional[int] = None

    def _embed(rel: str) -> Optional[list]:
        nonlocal fresh_dim
        try:
            raw = embed.embed_text(_embed_input(by_rel[rel]), mode=mode)
        except embed.EmbeddingUnavailable:
            return None
        fresh_dim = len(raw)
        return raw

    # Pass 1: take cache hits (hash match), queue misses.
    for note in notes:
        h = _content_hash(_embed_input(note))
        hashes[note.rel] = h
        cached = cache.get(note.rel)
        if isinstance(cached, dict) and cached.get("hash") == h and cached.get("vec"):
            raws[note.rel] = list(cached["vec"])
        else:
            pending.append(note.rel)

    # Pass 2: fresh-embed the misses (learns the current model's dim).
    for rel in pending:
        raw = _embed(rel)
        if raw is None:
            return None  # graceful-skip the entire signal
        raws[rel] = raw

    # Pass 3: enforce a uniform dimension. Reused cache entries that don't match
    # the fresh dim are stale (model/dim changed) — re-embed them.
    dims = {len(v) for v in raws.values()}
    if len(dims) > 1:
        if fresh_dim is None:
            return None  # all-cache-hit but internally mixed (corrupt) — skip
        for rel in list(raws):
            if len(raws[rel]) != fresh_dim:
                raw = _embed(rel)
                if raw is None:
                    return None
                raws[rel] = raw

    if cache_path is not None:
        _save_embed_cache(Path(cache_path),
                          {rel: {"hash": hashes[rel], "vec": raws[rel]} for rel in raws})
    return {rel: _normalize(vec) for rel, vec in raws.items()}


def score_embedding_pairs(notes: list, vectors: dict, *,
                          min_score: float = _DEFAULT_EMBED_MIN_SCORE,
                          top: int = _DEFAULT_TOP) -> list:
    """Full pairwise cosine over the embedding vectors (O(n^2); n≈400 is fine).
    Drops already-linked + below-threshold pairs. Returns Suggestions with
    `signal='embedding'` and no shared_terms (the signal is semantic, not
    lexical)."""
    by_rel = {n.rel: n for n in notes}
    rels = [n.rel for n in notes if n.rel in vectors]
    out = []
    for x in range(len(rels)):
        a = by_rel[rels[x]]
        va = vectors[rels[x]]
        for y in range(x + 1, len(rels)):
            b = by_rel[rels[y]]
            sim = _cosine_unit(va, vectors[rels[y]])
            if sim < min_score:
                continue
            if _already_linked(a, b):
                continue
            out.append(Suggestion(
                a_rel=a.rel, b_rel=b.rel,
                a_title=a.title, b_title=b.title,
                a_folder=a.folder, b_folder=b.folder,
                score=sim, shared_terms=[],
                same_folder=(a.folder == b.folder and a.folder != ""),
                signal="embedding",
            ))
    out.sort(key=lambda s: (-s.score, s.a_rel, s.b_rel))
    if top and top > 0:
        out = out[:top]
    return out


def discover_embeddings(notes: list, *, mode: Optional[str] = None,
                        cache_path: Optional[Path] = None,
                        min_score: float = _DEFAULT_EMBED_MIN_SCORE,
                        top: int = _DEFAULT_TOP) -> Optional[list]:
    """Embed the corpus + score pairs. Returns the embedding Suggestions, or None
    when the signal is unavailable (graceful-skip → caller uses TF-IDF only)."""
    vectors = embed_corpus(notes, mode=mode, cache_path=cache_path)
    if vectors is None:
        return None
    return score_embedding_pairs(notes, vectors, min_score=min_score, top=top)


# -----------------------------------------------------------------------------
# Report (task 2; task 3 adds the embedding section) — operator-review markdown
# -----------------------------------------------------------------------------

def _stem(rel: str) -> str:
    """Filename stem from a POSIX rel path ('Church/Baptism' -> 'Baptism')."""
    return rel.rsplit("/", 1)[-1]


# Characters that break Obsidian `[[wikilink]]` targets: brackets (delimiters),
# pipe (alias), hash (heading anchor), caret (block ref).
_WIKILINK_UNSAFE = ("[", "]", "|", "#", "^")


def _paste_link(target_rel: str, *, ambiguous_stems: set) -> str:
    """A paste-ready Obsidian wikilink to `target_rel`. Uses the bare stem (how
    Obsidian autocompletes) unless that stem is shared by >1 note, in which case
    the full path disambiguates. If the chosen target contains wikilink-breaking
    characters (e.g. the bracketed-date meeting notes), don't emit a broken
    `[[…]]` — flag it so the operator links via Obsidian's picker instead."""
    stem = _stem(target_rel)
    target = target_rel if stem in ambiguous_stems else stem
    if any(ch in target for ch in _WIKILINK_UNSAFE):
        return f"`{target}` — link via Obsidian's `[[` picker (name has `[]`/`|`/`#`)"
    return f"[[{target}]]"


def _pair_key(s: "Suggestion") -> frozenset:
    return frozenset((s.a_rel, s.b_rel))


def _render_pair_block(out: list, i: int, s: "Suggestion", *, ambiguous: set,
                       reason: str, also_semantic: bool = False) -> None:
    rel_note = ("same folder" if s.same_folder
                else f"{s.a_folder or '(root)'} ⇄ {s.b_folder or '(root)'}")
    confirm = "  ·  ✓ also semantically related" if also_semantic else ""
    a_link = _paste_link(s.a_rel, ambiguous_stems=ambiguous)
    b_link = _paste_link(s.b_rel, ambiguous_stems=ambiguous)
    out.append(f"### {i}. score {s.score:.3f} — {rel_note}{confirm}")
    out.append(f"- `{s.a_rel}` — **{s.a_title}**")
    out.append(f"- `{s.b_rel}` — **{s.b_title}**")
    out.append(f"- {reason}")
    out.append(f"- paste into **{s.a_title}**: {b_link}  ·  "
               f"paste into **{s.b_title}**: {a_link}")
    out.append("")


def build_report(notes: list, suggestions: list, *, today: str,
                 embed_suggestions: Optional[list] = None,
                 embed_unavailable: bool = False) -> str:
    """Render an operator-review markdown report. The TF-IDF section lists pairs
    that share distinctive terms; when the embedding signal ran, a second section
    lists *semantically* related pairs TF-IDF missed (low surface overlap), and
    TF-IDF pairs the embeddings also confirm are flagged. Advisory only — nothing
    is applied, no personal note is touched."""
    # Stems shared by >1 note → those paste-links must use the full path.
    stem_counts = defaultdict(int)
    for note in notes:
        stem_counts[note.path.stem] += 1
    ambiguous = {s for s, c in stem_counts.items() if c > 1}

    tfidf_keys = {_pair_key(s) for s in suggestions}
    embed_suggestions = embed_suggestions or []
    embed_keys = {_pair_key(s) for s in embed_suggestions}
    # Embedding-only = the new coverage (semantically related, not lexically).
    embed_only = [s for s in embed_suggestions if _pair_key(s) not in tfidf_keys]

    involved = set()
    for s in suggestions:
        involved.add(s.a_rel)
        involved.add(s.b_rel)
    for s in embed_only:
        involved.add(s.a_rel)
        involved.add(s.b_rel)

    total = len(suggestions) + len(embed_only)
    if embed_unavailable:
        signal_note = ("> Signal: **TF-IDF only** — the embedding (semantic) signal "
                       "was skipped (sentence-transformers not installed).")
    elif embed_suggestions:
        signal_note = ("> Signals: **TF-IDF** (shared distinctive terms) + "
                       "**embedding** (semantic similarity). Pairs both agree on are "
                       "flagged `✓ also semantically related`.")
    else:
        signal_note = "> Signal: **TF-IDF** (shared distinctive terms)."

    out = [
        f"# Personal-notes link suggestions — {today}",
        "",
        f"**{total} suggestion(s) across {len(notes)} personal notes** — "
        f"{len(involved)} note(s) appear in at least one suggestion below; the rest "
        f"had no strong related-but-unlinked match.",
        "",
        "> Read-only audit (V4 #43). Each pair is a *suggestion*: these notes look "
        "related but aren't `[[linked]]`. Open this in Obsidian and add the links "
        "you agree with **by hand** — nothing here was changed automatically and no "
        "personal note was modified. Suggestions are personal↔personal only; never "
        "a link into `AgentMemory/`.",
        signal_note,
        "",
    ]
    if total == 0:
        out.append("No related-but-unlinked pairs above the threshold. 🎉")
        return "\n".join(out) + "\n"

    if suggestions:
        out.append("## Shared-vocabulary links (TF-IDF)")
        out.append("")
        for i, s in enumerate(suggestions, 1):
            _render_pair_block(
                out, i, s, ambiguous=ambiguous,
                reason=f"shared terms: {', '.join(s.shared_terms)}",
                also_semantic=_pair_key(s) in embed_keys)

    if embed_only:
        out.append("## Semantically related (embedding signal — TF-IDF missed these)")
        out.append("")
        out.append("> Same topic/person/event without shared surface wording — the "
                   "connections a no-tags/no-links corpus most needs. Skim these "
                   "extra-carefully; semantic matches are higher-recall, lower-precision.")
        out.append("")
        for i, s in enumerate(embed_only, 1):
            _render_pair_block(
                out, i, s, ambiguous=ambiguous,
                reason=f"semantically related (cosine {s.score:.3f}); no strong shared vocabulary")
    return "\n".join(out) + "\n"


def default_report_path(vault: Path, today: str) -> Path:
    """`<vault>/_meta/notes-links-<date>.md` — agent-controlled output, mirroring
    vault_lint's `vault-lint-<date>.md`. `vault` is the AgentMemory root
    (MEMORY_VAULT_PATH); the personal-notes corpus is the Obsidian parent, but the
    report lands inside the agent's own vault, never beside a personal note."""
    return Path(vault) / "_meta" / f"notes-links-{today}.md"


def is_safe_report_path(out_path: Path, vault: Path, note_paths: set) -> bool:
    """Guard for `--out` (DC-1): a report may ONLY be written inside the agent-
    controlled vault and must NEVER be (or overwrite) a personal note. In
    production personal notes live OUTSIDE the AgentMemory vault, so the
    `inside_vault` check alone suffices; the `note_paths` membership check is the
    belt-and-suspenders that also holds when a caller points `--vault` at the
    Obsidian root itself (the corpus we just walked is the authoritative set of
    personal notes). Without this, `--out <a personal note>` would clobber it."""
    out_r = out_path.expanduser().resolve()
    vault_r = Path(vault).expanduser().resolve()
    inside_vault = out_r == vault_r or vault_r in out_r.parents
    return inside_vault and out_r not in note_paths


# -----------------------------------------------------------------------------
# Apply mode (opt-in `--apply`) — write the suggested links INTO the notes
# -----------------------------------------------------------------------------
#
# The default tool is read-only (DC-1). `--apply` is the explicit, operator-
# directed escape hatch that actually writes the suggested `[[wikilinks]]` into a
# marked `## Related` section at the end of each source note. It ALWAYS backs the
# whole corpus up first (a tarball under `_meta/`), only ever touches a single
# agent-marked section (idempotent: re-running MERGES into that one section, never
# duplicates it), and only writes wikilink-safe targets the note doesn't already
# link. The operator opted in; A3 is satisfied because the operator directed it.

_RELATED_HEADING = "## Related"
_APPLY_MARKER = "Suggested by Agent M link-discovery"
_RELATED_LINK_RE = re.compile(r"^- (\[\[[^\]]+\]\])\s*$", re.MULTILINE)
# The agent block is anchored to its actual `%% … %%` comment LINE — never a bare
# substring of the marker. Otherwise a note that merely *mentions* the tool in
# prose (or carries a human-authored `## Related`) would be mis-split and its
# content destroyed. Must match an Obsidian-comment line whose text is the marker.
_AGENT_COMMENT_RE = re.compile(
    r"^%%\s*" + re.escape(_APPLY_MARKER) + r".*?%%\s*$", re.MULTILINE)


def _split_related(body: str) -> tuple:
    """Split a note body into (preserved_body, existing_agent_links). The agent's
    Related section is marked by its `%% … %%` comment line + lives at EOF;
    everything from the `## Related` heading that introduces that comment to EOF is
    the agent block. A note with no agent comment line returns (body, empty-set),
    so prose mentioning the tool — or a human-authored `## Related` — is never
    disturbed (the new section is appended below, not merged into)."""
    m = _AGENT_COMMENT_RE.search(body)
    if m is None:
        return body.rstrip("\n"), set()
    cut = body.rfind(_RELATED_HEADING, 0, m.start())
    if cut == -1:
        cut = m.start()
    preserved = body[:cut].rstrip("\n")
    existing = set(_RELATED_LINK_RE.findall(body[cut:]))
    return preserved, existing


def plan_apply(notes: list, suggestions: list) -> dict:
    """Map src_rel -> set of paste-ready link strings to ensure present, for both
    directions of each suggestion. Skips wikilink-unsafe targets (bracketed names)
    and any link the source note already has (the operator's or a prior apply's)."""
    by_rel = {n.rel: n for n in notes}
    stem_counts = defaultdict(int)
    for n in notes:
        stem_counts[n.path.stem] += 1
    ambiguous = {s for s, c in stem_counts.items() if c > 1}
    want = defaultdict(set)
    for s in suggestions:
        for src_rel, tgt_rel in ((s.a_rel, s.b_rel), (s.b_rel, s.a_rel)):
            if src_rel not in by_rel:
                continue
            link = _paste_link(tgt_rel, ambiguous_stems=ambiguous)
            if not link.startswith("[["):
                continue  # unsafe name — can't be a valid wikilink target
            src = by_rel[src_rel]
            if _stem(tgt_rel) in src.links or tgt_rel in src.links:
                continue  # already linked by the operator
            want[src_rel].add(link)
    return dict(want)


def backup_corpus(notes: list, vault: Path, *, today: str) -> Path:
    """Tar.gz every personal note (text) to `<vault>/_meta/notes-backup-<date>.tar.gz`
    so an apply is fully reversible. Returns the backup path."""
    root = vault_lint._obsidian_root(Path(vault))
    path = Path(vault) / "_meta" / f"notes-backup-{today}.tar.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for n in notes:
            tar.add(n.path, arcname=n.path.relative_to(root).as_posix())
    return path


def apply_links(notes: list, want: dict, *, today: str) -> tuple:
    """Write/merge the planned links into each note's single agent `## Related`
    section. Idempotent — merges with any existing agent links, never duplicates
    the section. Returns (notes_modified, links_added)."""
    by_rel = {n.rel: n for n in notes}
    modified = added = 0
    for rel, newlinks in want.items():
        if not newlinks or rel not in by_rel:
            continue
        p = by_rel[rel].path
        body = p.read_text(encoding="utf-8")
        preserved, existing = _split_related(body)
        merged = existing | set(newlinks)
        if merged == existing:
            continue  # nothing new to add
        section = (
            preserved + "\n\n" + _RELATED_HEADING + "\n\n"
            + f"%% {_APPLY_MARKER}, {today}. Review / edit / remove freely. %%\n"
            + "\n".join(f"- {lk}" for lk in sorted(merged)) + "\n"
        )
        if section != body:
            p.write_text(section, encoding="utf-8")
            modified += 1
            added += len(merged) - len(existing)
    return modified, added


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _render_text(notes: list, suggestions: list) -> str:
    out = [
        f"notes-link-discovery: {len(suggestions)} related-but-unlinked pair(s) "
        f"across {len(notes)} personal notes",
        "",
    ]
    for s in suggestions:
        a = f"{s.a_folder + '/' if s.a_folder else ''}{s.a_title}"
        b = f"{s.b_folder + '/' if s.b_folder else ''}{s.b_title}"
        out.append(f"  [{s.score:.3f}] {a}  <->  {b}")
        out.append(f"      shared: {', '.join(s.shared_terms)}")
    if not suggestions:
        out.append("  no related-but-unlinked pairs above threshold.")
    return "\n".join(out) + "\n"


def main(argv: Optional[list] = None) -> int:
    # Windows stdout defaults to cp1252, which can't encode some glyphs. Force
    # UTF-8 best-effort so the CLI never UnicodeEncodeErrors.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    p = argparse.ArgumentParser(
        prog="notes_link_discovery",
        description="Read-only missing-link audit for personal notes (V4 #43).")
    p.add_argument("--vault", default=None, help="vault root (else MEMORY_VAULT_PATH)")
    p.add_argument("--format", choices=("json", "text"), default="text")
    p.add_argument("--top", type=int, default=_DEFAULT_TOP, help="max suggestions (0 = all)")
    p.add_argument("--min-score", type=float, default=_DEFAULT_MIN_SCORE,
                   help="cosine-similarity threshold")
    p.add_argument("--report", action="store_true",
                   help="write an operator-review markdown report "
                        "(to --out or <vault>/_meta/notes-links-<date>.md)")
    p.add_argument("--out", default=None, help="report output path (with --report)")
    p.add_argument("--embeddings", action="store_true",
                   help="add the embedding (semantic) signal alongside TF-IDF "
                        "(loads the local BGE model; graceful-skip if unavailable)")
    p.add_argument("--mode", choices=("local", "stub"), default=None,
                   help="embedding mode (with --embeddings): local (default) or stub")
    p.add_argument("--embed-min-score", type=float, default=_DEFAULT_EMBED_MIN_SCORE,
                   help="cosine threshold for the embedding signal")
    p.add_argument("--apply", action="store_true",
                   help="WRITE the suggested links into the notes (opt-in; default "
                        "is read-only). Backs the corpus up first, then merges a "
                        "marked `## Related` section into each source note.")
    args = p.parse_args(argv)
    try:
        vault = vault_lint._resolve_vault(args.vault)
    except FileNotFoundError as e:
        print(f"notes_link_discovery: {e}", file=sys.stderr)
        return 2
    if not vault.is_dir():
        print(f"notes_link_discovery: vault not found: {vault}", file=sys.stderr)
        return 2

    notes, suggestions = discover(vault, min_score=args.min_score, top=args.top)

    embed_suggestions = None
    embed_unavailable = False
    if args.embeddings:
        embed_suggestions = discover_embeddings(
            notes, mode=args.mode, cache_path=default_embed_index_path(vault),
            min_score=args.embed_min_score, top=args.top)
        if embed_suggestions is None:
            embed_unavailable = True
            print("notes_link_discovery: embedding signal unavailable "
                  "(sentence-transformers not installed) — TF-IDF only.",
                  file=sys.stderr)

    if args.apply:
        today = date.today().isoformat()
        combined = list(suggestions)
        if embed_suggestions:
            tf_keys = {_pair_key(s) for s in suggestions}
            combined += [s for s in embed_suggestions if _pair_key(s) not in tf_keys]
        want = plan_apply(notes, combined)
        if not want:
            print("notes-link-discovery apply: no new links to write "
                  "(all suggestions already linked or unsafe-named).")
            return 0
        try:
            backup = backup_corpus(notes, vault, today=today)
        except OSError as e:
            print(f"notes_link_discovery: refusing to apply — backup failed: {e}",
                  file=sys.stderr)
            return 2
        modified, added = apply_links(notes, want, today=today)
        print(f"notes-link-discovery apply: wrote {added} link(s) into {modified} "
              f"note(s). Backup: {backup}")
        print(f"  revert with:  tar xzf '{backup}' -C "
              f"'{vault_lint._obsidian_root(vault)}'")
        return 0

    if args.report:
        today = date.today().isoformat()
        report = build_report(notes, suggestions, today=today,
                              embed_suggestions=embed_suggestions,
                              embed_unavailable=embed_unavailable)
        out_path = Path(args.out).expanduser() if args.out else default_report_path(vault, today)
        note_paths = {n.path.resolve() for n in notes}
        if not is_safe_report_path(out_path, vault, note_paths):
            print(f"notes_link_discovery: refusing to write the report outside the "
                  f"agent-controlled vault or onto a personal note: {out_path}",
                  file=sys.stderr)
            return 2
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        n_embed_only = 0
        if embed_suggestions:
            tf_keys = {_pair_key(s) for s in suggestions}
            n_embed_only = sum(1 for s in embed_suggestions if _pair_key(s) not in tf_keys)
        print(f"notes-link-discovery report: {len(suggestions)} TF-IDF + "
              f"{n_embed_only} embedding-only suggestion(s) across {len(notes)} "
              f"notes -> {out_path}")
        return 0

    if args.format == "json":
        payload = {
            "notes": len(notes),
            "suggestions": [s.to_dict() for s in suggestions],
        }
        if embed_suggestions is not None:
            payload["embedding_suggestions"] = [s.to_dict() for s in embed_suggestions]
        if embed_unavailable:
            payload["embedding_unavailable"] = True
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(_render_text(notes, suggestions), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
