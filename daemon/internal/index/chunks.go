package index

import (
	"database/sql"
	"fmt"

	"github.com/alexherrero/agentm/daemon/internal/extract"
)

// The chunk index: a note's retrievable sections, each carrying the heading
// ancestry that led to it.
//
// # Two kinds of chunking, and why both
//
// This package already chunked, and the temptation is to read the new
// requirement as replacing the old one. It does not, and treating them as
// alternatives would regress a measured fix.
//
//   - `ChunkText` splits by *byte budget with overlap*, sized to the embedder's
//     context window. It exists because 562 of 9,473 notes exceed that window and
//     used to lose everything past their head. It is about what the model can
//     read.
//   - `HeaderChunks` splits by *markdown heading*. It exists so a match points at
//     a section rather than a file — the fix for a 38KB design document taking
//     all five top slots from a 1.1KB focused note. It is about what a person
//     asked for.
//
// A long section blows the window whatever the headings say, so the second does
// not subsume the first. The split is two-level: header first, then window-split
// any header chunk still over budget, with every resulting row carrying the
// header path of the section it came from. One table, one `chunk_idx` space.
//
// The table is a cache like every other index here. It is rebuilt from the
// markdown, it is never authoritative, and deleting it costs a reconcile pass.

// chunkBudgetTokens is the window the second-level split works to.
//
// Fixed rather than read from the live embedder on purpose. The chunk table is a
// retrieval structure and has to be stable across an embedder swap: keying its
// row boundaries to whichever model happens to be installed would mean every
// model change silently re-cut every note, and a `<path>#<n>` reference would
// stop meaning what it meant. The vector arm re-chunks to its own live window
// when it embeds; that is where model-specific sizing belongs.
const chunkBudgetTokens = 2048

// Chunk is one row of the chunk index.
type Chunk struct {
	ChunkIdx   int
	HeaderPath string
	Content    string
}

// BuildChunks produces the rows for one note.
//
// `budget` is the byte budget a single chunk may occupy — the same one
// `ChunkText` works to. A budget of zero disables the second level entirely,
// which is what a caller that only wants sections passes.
func BuildChunks(title, body string, budget int) []Chunk {
	sections := extract.HeaderChunks(body)
	if len(sections) == 0 {
		return nil
	}

	var out []Chunk
	for _, section := range sections {
		pieces := []string{section.Content}
		if budget > 0 {
			// The second level. `ChunkText` prepends the title to every piece,
			// which is deliberate there — the title is worth +3.8 hit@1 on this
			// corpus and every embedded piece should carry it.
			pieces = ChunkText(title, section.Content, budget)
		}
		for _, piece := range pieces {
			out = append(out, Chunk{
				ChunkIdx:   len(out),
				HeaderPath: section.HeaderPath,
				Content:    piece,
			})
		}
	}
	return out
}

// ReplaceChunks rewrites one note's chunk rows.
//
// Delete-then-insert rather than an upsert: a note that lost a section should
// lose its rows, and reconciling row-by-row against an edited document is how a
// stale chunk survives an edit that removed the text it holds.
func (x *Index) ReplaceChunks(docID int64, chunks []Chunk) error {
	tx, err := x.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := replaceChunksTx(tx, docID, chunks); err != nil {
		return err
	}
	return tx.Commit()
}

// replaceChunksTx is the same operation inside a caller's transaction, so the
// chunk rows and the lexical row commit together and a note is never half
// indexed.
func replaceChunksTx(tx *sql.Tx, docID int64, chunks []Chunk) error {
	if _, err := tx.Exec(`DELETE FROM chunks WHERE doc_id = ?`, docID); err != nil {
		return fmt.Errorf("clearing chunks for doc %d: %w", docID, err)
	}
	if len(chunks) == 0 {
		return nil
	}
	stmt, err := tx.Prepare(
		`INSERT INTO chunks (doc_id, chunk_idx, header_path, content) VALUES (?, ?, ?, ?)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, c := range chunks {
		if _, err := stmt.Exec(docID, c.ChunkIdx, c.HeaderPath, c.Content); err != nil {
			return fmt.Errorf("inserting chunk %d for doc %d: %w", c.ChunkIdx, docID, err)
		}
	}
	return nil
}

// ChunksFor returns one note's chunk rows in index order.
func (x *Index) ChunksFor(docID int64) ([]Chunk, error) {
	rows, err := x.db.Query(
		`SELECT chunk_idx, header_path, content FROM chunks WHERE doc_id = ? ORDER BY chunk_idx`,
		docID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Chunk
	for rows.Next() {
		var c Chunk
		if err := rows.Scan(&c.ChunkIdx, &c.HeaderPath, &c.Content); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// CountChunks is what the status surface and the rebuild gate ask.
func (x *Index) CountChunks() (int, error) {
	var n int
	err := x.db.QueryRow(`SELECT COUNT(*) FROM chunks`).Scan(&n)
	if err == sql.ErrNoRows {
		return 0, nil
	}
	return n, err
}
