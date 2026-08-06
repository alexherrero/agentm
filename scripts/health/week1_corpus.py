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

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)
_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


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


# ---------------------------------------------------------------------------
# Lexical surface — SQLite FTS5 + BM25
# ---------------------------------------------------------------------------

def build_lexical_index(vault, db_path, *, exclude_dirs=None, paths=None, verbose=False):
    """Build (or reuse) the FTS5 index at `db_path`. Returns (sqlite3.Connection, n_docs).

    Reuses an existing index when its stored fingerprint matches the live
    corpus, so a repeated run does not re-read 17MB off a Google Drive mount.
    """
    db_path = Path(db_path)
    if paths is None:
        paths = iter_markdown_paths(vault, exclude_dirs=exclude_dirs)
    fp = corpus_fingerprint(paths, vault)

    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
            if row and row[0] == fp:
                n = conn.execute("SELECT count(*) FROM docs").fetchone()[0]
                if verbose:
                    print(f"[week1-corpus] lexical index reused ({n} docs)", file=sys.stderr)
                return conn, n
        except sqlite3.Error:
            pass
        conn.close()
        db_path.unlink()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE docs USING fts5("
            "path UNINDEXED, title, body, tokenize='porter unicode61')"
        )
    except sqlite3.OperationalError as e:
        raise CorpusError(
            f"SQLite has no FTS5 support in this interpreter ({sys.executable}): {e}"
        ) from e
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

    n = 0
    started = time.monotonic()
    for p in paths:
        doc = read_document(p, vault)
        if doc is None:
            continue
        conn.execute("INSERT INTO docs (path, title, body) VALUES (?, ?, ?)", doc)
        n += 1
        if verbose and n % 1000 == 0:
            print(f"[week1-corpus] indexed {n}/{len(paths)}…", file=sys.stderr)
    conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fp,))
    conn.commit()
    if verbose:
        print(
            f"[week1-corpus] lexical index built: {n} docs in "
            f"{time.monotonic() - started:.1f}s -> {db_path}",
            file=sys.stderr,
        )
    return conn, n


def _sanitize_fts_query(query):
    """Rewrite free text into an FTS5 expression that cannot be a syntax error.

    Every token is quoted (so `foo-bar`, `v5.3`, and `what's` are literals, not
    operators) and OR-joined, which is the widest reading of the agent's intent.
    """
    tokens = _FTS_TOKEN_RE.findall(query)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def search_lexical(conn, query, k=5):
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
    """
    query = (query or "").strip()
    if not query:
        return [], "empty query"
    sql = (
        "SELECT path, bm25(docs, ?, ?, ?) AS s, snippet(docs, 2, '[', ']', ' … ', 24) "
        "FROM docs WHERE docs MATCH ? ORDER BY s LIMIT ?"
    )
    note = None
    try:
        rows = conn.execute(sql, (*_BM25_WEIGHTS, query, k)).fetchall()
    except sqlite3.OperationalError:
        sanitized = _sanitize_fts_query(query)
        if sanitized is None:
            return [], "query contained no searchable terms"
        note = (
            f"query was not valid FTS5 syntax; searched for any of its terms "
            f"instead ({sanitized})"
        )
        try:
            rows = conn.execute(sql, (*_BM25_WEIGHTS, sanitized, k)).fetchall()
        except sqlite3.OperationalError as e:
            return [], f"search failed: {e}"
    results = [
        {"path": r[0], "score": round(-r[1], 4), "snippet": " ".join((r[2] or "").split())}
        for r in rows
    ]
    return results, note


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
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    vault = resolve_vault(args.vault_path)
    work = Path(args.work_dir)
    paths = iter_markdown_paths(vault, exclude_dirs=args.exclude_dir)
    print(f"[week1-corpus] {len(paths)} .md files under {vault}", file=sys.stderr)
    build_lexical_index(vault, work / "lexical.db", paths=paths, verbose=True)
    if not args.lexical_only:
        build_vector_index(
            vault, work / f"vectors-{args.embed_mode}.npz",
            paths=paths, mode=args.embed_mode, verbose=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
