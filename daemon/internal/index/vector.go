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
// The store is deliberately boring — one row per note, brute-force cosine over
// the whole table. At ten thousand notes and a thousand dimensions that is forty
// megabytes and a few milliseconds a query. An ANN index would add a second index
// format, a build step, and a recall/latency knob to defend, to save time nobody
// is currently spending.

// VectorRow is one note's embedding, ready to store.
type VectorRow struct {
	DocID   int64
	MtimeNS int64
	Vec     []float32
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
	Model   string `json:"model,omitempty"`
	Vectors int    `json:"vectors"`
	Dim     int    `json:"dim,omitempty"`
	// InScope is how many notes the configured scope covers, so "7,391 of 9,473
	// embedded" is a sentence the status line can say rather than implying
	// completeness from a bare count.
	InScope int `json:"in_scope"`
	Stale   int `json:"stale"`
}

// Complete reports whether every in-scope note has a current vector.
func (v VectorStats) Complete() bool {
	return v.InScope > 0 && v.Vectors >= v.InScope && v.Stale == 0
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

// PendingEmbeds returns in-scope notes with no vector, or whose vector was
// computed from an older revision of the file.
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
		LEFT JOIN embeddings e ON e.doc_id = m.id AND e.model = ?
		WHERE ` + where + `
		  AND (e.doc_id IS NULL OR e.mtime_ns <> m.mtime_ns)
		ORDER BY m.path`
	full := append([]any{model}, args...)
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

// PutVectors stores a batch of embeddings in one transaction.
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

	stmt, err := tx.Prepare(
		`INSERT INTO embeddings(doc_id, model, dim, mtime_ns, vec) VALUES(?, ?, ?, ?, ?)
		 ON CONFLICT(doc_id) DO UPDATE SET
		   model=excluded.model, dim=excluded.dim,
		   mtime_ns=excluded.mtime_ns, vec=excluded.vec`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, r := range rows {
		if len(r.Vec) == 0 {
			continue
		}
		if _, err := stmt.Exec(r.DocID, model, len(r.Vec), r.MtimeNS, encodeVec(r.Vec)); err != nil {
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
	staleArgs := append([]any{model}, args...)
	if err := x.db.QueryRow(
		`SELECT count(*) FROM docmeta m
		   JOIN embeddings e ON e.doc_id = m.id AND e.model = ?
		  WHERE `+where+` AND e.mtime_ns <> m.mtime_ns`, staleArgs...).Scan(&s.Stale); err != nil {
		return s, err
	}
	return s, nil
}

// VectorSearch ranks the whole vector table against a query vector by cosine and
// returns the top k.
//
// The scan streams rows out of SQLite and scores them as it goes rather than
// materializing the table in memory. That keeps a one-shot process from paying a
// forty-megabyte load before its first comparison, and it removes the cache
// invalidation a resident copy would need — the correct vector set is whatever
// the table currently holds, by construction.
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
		SELECT m.id, m.path, m.flags, m.captured, m.captured_src, e.vec
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
	var out []Result
	for rows.Next() {
		var r Result
		var blob []byte
		if err := rows.Scan(&r.rowid, &r.Path, &r.Penalty, &r.Captured, &r.CapturedSource, &blob); err != nil {
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
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, err
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

// TruncateForModel cuts an embedding input to what the model's context window can
// actually see, and reports whether it cut.
func TruncateForModel(s string, ctxTokens int) (string, bool) {
	if ctxTokens <= 0 {
		return s, false
	}
	// Headroom for the prompt scaffolding the model wraps around this, plus the
	// special tokens it adds either side.
	budget := (ctxTokens - 64) * charsPerToken
	if budget <= 0 || len(s) <= budget {
		return s, false
	}
	return cutOnRune(s, budget), true
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
