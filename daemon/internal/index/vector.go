package index

import (
	"database/sql"
	"encoding/binary"
	"math"
	"sort"
	"strings"
	"unicode/utf8"
)

// The vector arm lives in the same SQLite file as the lexical index and is a
// cache in exactly the same sense: every vector is reconstructible from the note
// it was computed from, so the file can be deleted and rebuilt with no loss.
// That is what buys the right to discard it on a schema change instead of
// carrying a migration.
//
// The store is deliberately boring — one or more rows per note (several when a
// note is longer than the embedder's window and gets chunked), brute-force
// cosine over the whole table, and a note scores by its best-matching chunk. At
// ten thousand notes and a thousand dimensions that is forty megabytes and a few
// milliseconds a query. An ANN index would add a second index format, a build
// step, and a recall/latency knob to defend, to save time nobody is currently
// spending.

// VectorRow is one chunk's embedding, ready to store. A note that fits the
// embedder's window in one piece has exactly one VectorRow at ChunkIdx 0; a
// longer note has several, one per window-sized slice ChunkText produced.
type VectorRow struct {
	DocID    int64
	ChunkIdx int
	MtimeNS  int64
	Vec      []float32
}

// PendingDoc is a note the vector arm has not embedded yet, or has embedded from
// a stale version.
type PendingDoc struct {
	ID      int64
	Path    string
	Title   string
	Body    string
	MtimeNS int64
}

// VectorStats is the vector arm's account of itself for the status surface.
type VectorStats struct {
	Model string `json:"model,omitempty"`
	// Vectors is the raw row count for this model — one per chunk, so a note
	// split into three chunks contributes three. Storage accounting reads this;
	// coverage reads Notes.
	Vectors int `json:"vectors"`
	// Notes is how many distinct in-scope notes have a current embedding (at
	// least one chunk stored at the note's present mtime), so "7,391 of 9,473
	// embedded" is a sentence about notes, not an inflated count of chunks.
	Notes int `json:"notes"`
	Dim   int `json:"dim,omitempty"`
	// InScope is how many notes the configured scope covers, so "7,391 of 9,473
	// embedded" is a sentence the status line can say rather than implying
	// completeness from a bare count.
	InScope int `json:"in_scope"`
	// Stale counts distinct notes, not rows: a stale note's several chunks all
	// carry the same outdated mtime (PutVectors replaces a note's whole chunk
	// set at once), so counting rows here would overstate staleness by the
	// average chunk count.
	Stale int `json:"stale"`
}

// Complete reports whether every in-scope note has a current embedding.
func (v VectorStats) Complete() bool {
	return v.InScope > 0 && v.Notes >= v.InScope && v.Stale == 0
}

// encodeVec packs a vector as little-endian float32.
//
// Endianness is pinned rather than left to the host because the index file is
// copied between machines — the corpus snapshots the scorecard restores are
// exactly that — and a vector decoded on the other endianness is not an error,
// it is a number that ranks wrongly.
func encodeVec(v []float32) []byte {
	b := make([]byte, 4*len(v))
	for i, f := range v {
		binary.LittleEndian.PutUint32(b[4*i:], math.Float32bits(f))
	}
	return b
}

func decodeVec(b []byte, into []float32) []float32 {
	n := len(b) / 4
	if cap(into) < n {
		into = make([]float32, n)
	}
	into = into[:n]
	for i := range into {
		into[i] = math.Float32frombits(binary.LittleEndian.Uint32(b[4*i:]))
	}
	return into
}

// scopeClause turns a list of vault-relative directory prefixes into a SQL
// predicate over docmeta.path.
//
// An empty scope matches nothing rather than everything. The scope is what keeps
// the vector arm off the parts of the vault whose notes do not embed whole; a
// misread config that silently widened it to the entire tree would embed
// 200,000-token meta files as single centroids and call the result retrieval.
func scopeClause(prefixes []string) (string, []any) {
	if len(prefixes) == 0 {
		return "0", nil
	}
	parts := make([]string, 0, len(prefixes))
	args := make([]any, 0, len(prefixes)*2)
	for _, p := range prefixes {
		p = strings.Trim(strings.TrimSpace(p), "/")
		if p == "" {
			continue
		}
		parts = append(parts, "(m.path = ? OR m.path LIKE ? ESCAPE '\\')")
		args = append(args, p, escapeLike(p)+"/%")
	}
	if len(parts) == 0 {
		return "0", nil
	}
	return "(" + strings.Join(parts, " OR ") + ")", args
}

// escapeLike neutralizes LIKE's wildcards so a directory named `desk_2026`
// matches only itself. `_` is a single-character wildcard, and a path containing
// one is ordinary.
func escapeLike(s string) string {
	r := strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`)
	return r.Replace(s)
}

// InScopeCount is how many indexed notes the vector scope covers.
func (x *Index) InScopeCount(scope []string) (int, error) {
	where, args := scopeClause(scope)
	x.mu.Lock()
	defer x.mu.Unlock()
	var n int
	err := x.db.QueryRow(
		`SELECT count(*) FROM docmeta m WHERE `+where, args...).Scan(&n)
	return n, err
}

// PendingEmbeds returns in-scope notes with no current embedding: never
// embedded, or embedded from an older revision of the file.
//
// "Current" is NOT EXISTS a chunk row at the note's present mtime, rather than a
// join on doc_id alone, because a chunked note's rows share one doc_id — a join
// would return that note once per chunk, and a WHERE that only compared one
// arbitrary row's mtime could pass a note whose chunk set is half-stale. Every
// chunk of one embedding pass is written with the same mtime_ns (PutVectors
// replaces a note's whole chunk set atomically), so NOT EXISTS a matching row is
// exactly "this note's stored chunks, if any, are not the current revision."
//
// Staleness is keyed on the same mtime the lexical reconcile pass compares, so a
// note that was re-indexed is also re-embedded, and one that was merely re-walked
// is not. Ordering is by path so a backfill interrupted halfway resumes
// deterministically instead of re-walking a random slice of the corpus.
func (x *Index) PendingEmbeds(model string, scope []string, limit int) ([]PendingDoc, error) {
	where, args := scopeClause(scope)
	q := `
		SELECT m.id, m.path, d.title, d.body, m.mtime_ns
		FROM docmeta m
		JOIN docs d ON d.rowid = m.id
		WHERE ` + where + `
		  AND NOT EXISTS (
		        SELECT 1 FROM embeddings e
		         WHERE e.doc_id = m.id AND e.model = ? AND e.mtime_ns = m.mtime_ns)
		ORDER BY m.path`
	full := append(append([]any{}, args...), model)
	if limit > 0 {
		q += ` LIMIT ?`
		full = append(full, limit)
	}

	x.mu.Lock()
	defer x.mu.Unlock()
	rows, err := x.db.Query(q, full...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []PendingDoc
	for rows.Next() {
		var d PendingDoc
		if err := rows.Scan(&d.ID, &d.Path, &d.Title, &d.Body, &d.MtimeNS); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

// PutVectors stores a batch of chunk embeddings in one transaction, replacing
// each note's whole chunk set rather than upserting individual chunks into it.
//
// Every doc_id present in rows has its existing chunk rows deleted before the
// new ones for that doc_id are inserted. A note that shrinks from three chunks
// to one on re-embed — a heavy edit, or a change to this package's own chunking
// — would otherwise leave chunk_idx 1 and 2 behind: rows no query deletes,
// invisible to every count, and still there to be scored the next time this
// note is searched.
func (x *Index) PutVectors(model string, rows []VectorRow) error {
	if len(rows) == 0 {
		return nil
	}
	x.mu.Lock()
	defer x.mu.Unlock()

	tx, err := x.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	del, err := tx.Prepare(`DELETE FROM embeddings WHERE doc_id = ?`)
	if err != nil {
		return err
	}
	defer del.Close()

	ins, err := tx.Prepare(
		`INSERT INTO embeddings(doc_id, chunk_idx, model, dim, mtime_ns, vec)
		 VALUES(?, ?, ?, ?, ?, ?)
		 ON CONFLICT(doc_id, chunk_idx) DO UPDATE SET
		   model=excluded.model, dim=excluded.dim,
		   mtime_ns=excluded.mtime_ns, vec=excluded.vec`)
	if err != nil {
		return err
	}
	defer ins.Close()

	// Cleared once per doc_id, before that doc_id's first insert — clearing
	// again on a later row for the same note would delete the chunk this same
	// call just wrote.
	cleared := make(map[int64]bool, len(rows))
	for _, r := range rows {
		if len(r.Vec) == 0 {
			continue
		}
		if !cleared[r.DocID] {
			if _, err := del.Exec(r.DocID); err != nil {
				return err
			}
			cleared[r.DocID] = true
		}
		if _, err := ins.Exec(
			r.DocID, r.ChunkIdx, model, len(r.Vec), r.MtimeNS, encodeVec(r.Vec)); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// DropVectors clears the vector table. Swapping the embedding model invalidates
// every vector at once — they are not comparable across models — and this is
// what makes that an explicit act rather than a slow drift into a table holding
// two incompatible geometries.
func (x *Index) DropVectors() error {
	x.mu.Lock()
	defer x.mu.Unlock()
	_, err := x.db.Exec(`DELETE FROM embeddings`)
	return err
}

// VectorStats reports coverage for the status surface.
func (x *Index) VectorStats(model string, scope []string) (VectorStats, error) {
	where, args := scopeClause(scope)

	x.mu.Lock()
	defer x.mu.Unlock()

	var s VectorStats
	if err := x.db.QueryRow(
		`SELECT count(*) FROM docmeta m WHERE `+where, args...).Scan(&s.InScope); err != nil {
		return s, err
	}
	var dim sql.NullInt64
	if err := x.db.QueryRow(
		`SELECT count(*), max(dim) FROM embeddings WHERE model = ?`, model,
	).Scan(&s.Vectors, &dim); err != nil {
		return s, err
	}
	if dim.Valid {
		s.Dim = int(dim.Int64)
	}
	if s.Vectors > 0 {
		s.Model = model
	}

	notesArgs := append([]any{model}, args...)
	if err := x.db.QueryRow(
		`SELECT count(DISTINCT m.id) FROM docmeta m
		   JOIN embeddings e ON e.doc_id = m.id AND e.model = ? AND e.mtime_ns = m.mtime_ns
		  WHERE `+where, notesArgs...).Scan(&s.Notes); err != nil {
		return s, err
	}
	staleArgs := append([]any{model}, args...)
	if err := x.db.QueryRow(
		`SELECT count(DISTINCT m.id) FROM docmeta m
		   JOIN embeddings e ON e.doc_id = m.id AND e.model = ?
		  WHERE `+where+` AND e.mtime_ns <> m.mtime_ns`, staleArgs...).Scan(&s.Stale); err != nil {
		return s, err
	}
	return s, nil
}

// VectorSearch ranks the vector table against a query vector by cosine and
// returns the top k notes, each scored by its best-matching chunk.
//
// The scan streams rows out of SQLite and scores them as it goes rather than
// materializing the table in memory. That keeps a one-shot process from paying a
// forty-megabyte load before its first comparison, and it removes the cache
// invalidation a resident copy would need — the correct vector set is whatever
// the table currently holds, by construction.
//
// A note longer than the embedder's window has several chunk rows sharing one
// doc_id; only the best-scoring one survives before the top-k cut, so a long
// note contributes exactly one candidate to the ranking however many chunks
// back it — the same one-path-per-hit shape every other arm returns, extended
// to notes the embedder could not see whole.
//
// Vectors are unit length when stored, so the dot product is the cosine. A row
// whose width disagrees with the query's is skipped rather than scored: it was
// written by a different model, and comparing across models produces a number
// that means nothing and looks like a ranking.
func (x *Index) VectorSearch(q []float32, model string, k int, after, before string) ([]Result, error) {
	if len(q) == 0 || k <= 0 {
		return nil, nil
	}
	x.mu.Lock()
	defer x.mu.Unlock()

	rows, err := x.db.Query(`
		SELECT m.id, m.path, m.flags, m.captured, m.captured_src, m.updated,
		       m.created, e.vec
		FROM embeddings e JOIN docmeta m ON m.id = e.doc_id
		WHERE e.model = ? AND e.dim = ?
		  AND (? = '' OR m.captured >= ?)
		  AND (? = '' OR m.captured <  ?)`,
		model, len(q), after, after, before, before)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	// A single reusable decode buffer: the scan touches every row and allocating
	// a vector per row would make the garbage collector the dominant cost of a
	// search that is otherwise arithmetic.
	buf := make([]float32, len(q))
	// best-per-note, keyed by doc_id (Result.rowid). A map rather than relying on
	// scan order because chunk rows for the same note are not guaranteed
	// adjacent — SQLite is free to return the table scan in whatever order it
	// finds rows.
	best := make(map[int64]Result)
	var order []int64
	for rows.Next() {
		var r Result
		var blob []byte
		if err := rows.Scan(&r.rowid, &r.Path, &r.Penalty, &r.Captured, &r.CapturedSource,
			&r.Updated, &r.Created, &blob); err != nil {
			return nil, err
		}
		if len(blob) != 4*len(q) {
			continue
		}
		buf = decodeVec(blob, buf)
		var dot float64
		for i, qi := range q {
			dot += float64(qi) * float64(buf[i])
		}
		r.Score = dot
		r.RawScore = dot
		if prev, seen := best[r.rowid]; !seen || dot > prev.Score {
			if !seen {
				order = append(order, r.rowid)
			}
			best[r.rowid] = r
		}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	out := make([]Result, 0, len(order))
	for _, id := range order {
		out = append(out, best[id])
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Score != out[j].Score {
			return out[i].Score > out[j].Score
		}
		return out[i].Path < out[j].Path
	})
	if len(out) > k {
		out = out[:k]
	}
	return out, nil
}

// deleteVectorLocked drops a note's embedding. Called from Delete, which already
// holds the mutex.
//
// This has to happen with the docmeta row and not on a later sweep: docmeta.id is
// the join key, and a re-added note takes a fresh id, so an embedding left behind
// would be an orphan pointing at nothing — invisible to every count and still
// occupying the table.
func deleteVectorLocked(tx *sql.Tx, id int64) error {
	_, err := tx.Exec(`DELETE FROM embeddings WHERE doc_id = ?`, id)
	return err
}

// VectorModels lists the models that currently have vectors stored, so a mixed
// table is reportable rather than silently half-scored.
func (x *Index) VectorModels() ([]string, error) {
	x.mu.Lock()
	defer x.mu.Unlock()
	rows, err := x.db.Query(`SELECT DISTINCT model FROM embeddings ORDER BY model`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var s string
		if err := rows.Scan(&s); err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, rows.Err()
}

// EmbedText is the text handed to the embedder for a note.
//
// Title and body are concatenated because the lexical arm already weights the
// title 4x and the vector arm has no column weights to express the same thing —
// a note named for its subject carries that subject in its filename and often
// nowhere else, which is measured on this vault at +3.8 hit@1.
func EmbedText(title, body string) string {
	title, body = strings.TrimSpace(title), strings.TrimSpace(body)
	switch {
	case title == "":
		return body
	case body == "":
		return title
	}
	return title + "\n\n" + body
}

// charsPerToken converts a context window in tokens into a budget in bytes.
//
// Three rather than the four English prose averages, because the corpus is
// markdown: headings, list markers, code fences, URLs and punctuation all
// tokenize far denser than prose. Four was tried first and a 7,936-byte cut came
// back as 2,092 tokens against a 2,048-token window — a 3.79 ratio on an ordinary
// daily digest, so the average was wrong for the median document and not merely
// for the tail.
//
// No ratio is correct, which is the point. The daemon has no tokenizer for these
// weights and adding one means linking the library the supervised-child design
// exists to avoid, so this is an estimate backed by a retry (see EmbedRetryCut)
// rather than a bound anything relies on.
const charsPerToken = 3

// windowHeadroom is tokens reserved, out of a model's context window, for the
// prompt scaffolding and special tokens the model wraps around whatever text
// it embeds — Model.WrapQuery's or Model.WrapDoc's own prefix, plus whatever
// the server's own template adds beyond that. Chosen once, here, for both
// callers windowBudget serves.
const windowHeadroom = 64

// windowBudget converts a context window in tokens into a byte budget for the
// text a caller is about to hand the model, reserving windowHeadroom tokens
// for the wrapping every embedded text gets. ChunkText (splits text too long
// to fit into several pieces) and TruncateQuery (cuts it to one, for the
// caller that only ever wants a single piece) both derive their budget from
// here rather than each carrying its own headroom constant — one notion of
// the window, not two.
func windowBudget(ctxTokens int) int {
	return (ctxTokens - windowHeadroom) * charsPerToken
}

// EmbedRetryCut shortens a text that the server rejected as too long.
//
// Halving rather than shaving: the estimate was wrong by an unknown factor, and
// stepping down a few percent at a time would mean a dozen round trips for one
// pathological note. Two or three halvings reach any real document, and each is
// one request.
func EmbedRetryCut(s string) string {
	if len(s) < 2 {
		return ""
	}
	return cutOnRune(s, len(s)/2)
}

// cutOnRune truncates without splitting a multi-byte character. A half rune is
// invalid UTF-8, and what a JSON encoder does with it is a worse problem than the
// truncation itself.
func cutOnRune(s string, n int) string {
	if n >= len(s) {
		return s
	}
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n]
}

// TruncateQuery defensively cuts a query to a model's window budget before it
// is embedded, on a rune boundary.
//
// Every query embedded before task 3.5 was a handful of AND-reduced terms,
// always far short of any model's window, so nothing needed this. Task 3.5
// embeds the natural question instead (see queryEmbedText, cmd/agentmd), and
// the production hook will eventually hand this path a whole pasted prompt —
// unbounded in a way the terms string never was. A query embeds as exactly
// one vector, so unlike ChunkText there is no "best of several pieces" to
// keep afterward; only the piece that fits, taken from the head, matters.
func TruncateQuery(text string, ctxTokens int) string {
	budget := windowBudget(ctxTokens)
	if budget <= 0 || len(text) <= budget {
		return text
	}
	return cutOnRune(text, budget)
}
