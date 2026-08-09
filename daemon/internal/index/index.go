// Package index owns the one FTS5 file.
//
// The index is a cache. The files are truth. Everything here is reconstructible
// from the vault with no loss, which is why the daemon is allowed to delete and
// rebuild it whenever its schema changes rather than carrying a migration path.
package index

import (
	"database/sql"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/note"

	_ "modernc.org/sqlite" // pure-Go SQLite with FTS5 built in; no cgo.
)

// Column weights for bm25(), in table-column order: path, title, meta, body.
//
// `path` is UNINDEXED and contributes nothing regardless of weight. `title` (the
// note's slug plus any frontmatter title) is weighted 4x above body because a
// filename match on this vault is a strong signal — notes are named for their
// subject — and it was measured at +3.8 hit@1. `meta` (aliases plus tags) is
// weighted 3x above body; it measures as a no-op today because only 5.5% of the
// corpus has anything in that column, and it is built anyway because dreaming's
// alias backfill lands next and needs somewhere to land.
const (
	weightPath  = 0.0
	weightTitle = 4.0
	weightMeta  = 3.0
	weightBody  = 1.0
	bodyColumn  = 3 // snippet() column index
)

// tokenizer: porter stemming is worth +5.7 hit@5 and is the only knob measured to
// move hit@5 at all.
const tokenizer = "porter unicode61"

// Index is the FTS5 index plus the sidecar metadata the penalty and the temporal
// bounds read.
type Index struct {
	db    *sql.DB
	vault string
	path  string

	// SQLite serializes writers anyway, and a single resident process has no
	// reason to fight itself over a lock. One connection removes a whole class of
	// "database is locked" flake for a cost that is unmeasurable at this size.
	mu sync.Mutex

	// snippetedDocs counts the documents handed to snippet() since this index was
	// opened. It exists to be asserted on: snippet() scans whatever document it
	// is called on, so the one thing that must stay true is that it is called for
	// the k rows a caller reads and not for the 200-row over-fetch window. That
	// invariant is otherwise invisible — it shows up as a slow search months
	// later, which is exactly how it was found the first time. Guarded by mu.
	snippetedDocs int64
}

// snippetedDocs reports the counter above. Unexported: the number is a claim
// about how the index did its work, not about the corpus, and only the package's
// own tests have any business reading it.
func (x *Index) snippeted() int64 {
	x.mu.Lock()
	defer x.mu.Unlock()
	return x.snippetedDocs
}

// Open opens or creates the index at dbPath. A schema-version mismatch discards
// the file and starts over: it is a cache, so a rebuild is the cheap correct
// answer and a migration would be ceremony over a derived artifact.
func Open(dbPath, vault string) (*Index, error) {
	if err := os.MkdirAll(filepath.Dir(dbPath), 0o755); err != nil {
		return nil, fmt.Errorf("index directory: %w", err)
	}

	open := func() (*sql.DB, error) {
		dsn := "file:" + dbPath +
			"?_pragma=journal_mode(WAL)&_pragma=busy_timeout(10000)&_pragma=synchronous(NORMAL)"
		db, err := sql.Open("sqlite", dsn)
		if err != nil {
			return nil, err
		}
		db.SetMaxOpenConns(1)
		return db, db.Ping()
	}

	db, err := open()
	if err != nil {
		return nil, fmt.Errorf("opening index %s: %w", dbPath, err)
	}

	stale, err := schemaIsStale(db)
	if err != nil {
		db.Close()
		return nil, err
	}
	if stale {
		db.Close()
		for _, suffix := range []string{"", "-wal", "-shm"} {
			if err := os.Remove(dbPath + suffix); err != nil && !errors.Is(err, os.ErrNotExist) {
				return nil, fmt.Errorf("discarding stale index %s%s: %w", dbPath, suffix, err)
			}
		}
		if db, err = open(); err != nil {
			return nil, fmt.Errorf("reopening index %s: %w", dbPath, err)
		}
	}

	idx := &Index{db: db, vault: vault, path: dbPath}
	if err := idx.migrate(); err != nil {
		db.Close()
		return nil, err
	}
	return idx, nil
}

func schemaIsStale(db *sql.DB) (bool, error) {
	var version string
	err := db.QueryRow(`SELECT value FROM meta WHERE key='schema_version'`).Scan(&version)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		return true, nil
	case err != nil:
		// No meta table at all: either a fresh file or something we did not
		// write. Either way, starting over is correct and lossless.
		return false, nil
	}
	return version != config.SchemaVersion, nil
}

func (x *Index) migrate() error {
	stmts := []string{
		fmt.Sprintf(`CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
			path UNINDEXED, title, meta, body, tokenize='%s')`, tokenizer),
		`CREATE TABLE IF NOT EXISTS docmeta (
			id            INTEGER PRIMARY KEY AUTOINCREMENT,
			path          TEXT UNIQUE NOT NULL,
			flags         TEXT NOT NULL DEFAULT '',
			captured      TEXT NOT NULL DEFAULT '',
			captured_src  TEXT NOT NULL DEFAULT '',
			mtime_ns      INTEGER NOT NULL DEFAULT 0,
			size          INTEGER NOT NULL DEFAULT 0)`,
		`CREATE INDEX IF NOT EXISTS docmeta_captured ON docmeta(captured)`,
		`CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)`,
	}
	for _, s := range stmts {
		if _, err := x.db.Exec(s); err != nil {
			if strings.Contains(err.Error(), "fts5") {
				return fmt.Errorf(
					"this build has no FTS5 support, which should be impossible with "+
						"modernc.org/sqlite: %w", err)
			}
			return fmt.Errorf("index schema: %w", err)
		}
	}
	_, err := x.db.Exec(
		`INSERT INTO meta(key, value) VALUES('schema_version', ?)
		 ON CONFLICT(key) DO UPDATE SET value=excluded.value`, config.SchemaVersion)
	return err
}

// Close releases the index handle.
func (x *Index) Close() error { return x.db.Close() }

// Path is the index file's location, for reporting.
func (x *Index) Path() string { return x.path }

// capturedFormat is stored so a lexicographic string compare is also a
// chronological one, which is what makes the after:/before: bounds a plain
// indexed range scan.
const capturedFormat = "2006-01-02T15:04:05Z"

// CapturedFormat is the layout captured timestamps are written and stored in.
// Capture writes it into frontmatter so the value the index compares is the value
// the file claims, with no reformatting step between them.
func CapturedFormat() string { return capturedFormat }

// Upsert indexes one note, replacing any previous entry for the same path.
func (x *Index) Upsert(n note.Note, mtimeNS int64, size int64) error {
	x.mu.Lock()
	defer x.mu.Unlock()
	return x.upsertLocked(n, mtimeNS, size)
}

func (x *Index) upsertLocked(n note.Note, mtimeNS int64, size int64) error {
	tx, err := x.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	var id int64
	err = tx.QueryRow(`SELECT id FROM docmeta WHERE path = ?`, n.Rel).Scan(&id)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		res, err := tx.Exec(
			`INSERT INTO docmeta(path, flags, captured, captured_src, mtime_ns, size)
			 VALUES(?, ?, ?, ?, ?, ?)`,
			n.Rel, strings.Join(n.Flags, ","), n.Captured.UTC().Format(capturedFormat),
			n.CapturedSource, mtimeNS, size)
		if err != nil {
			return err
		}
		if id, err = res.LastInsertId(); err != nil {
			return err
		}
	case err != nil:
		return err
	default:
		// The capture date is immutable: it records an event in the daemon's own
		// life, not a claim about the world, and the shard a note is born into is
		// the one it dies in. So an update keeps the date already recorded unless
		// the file itself now asserts one.
		//
		// This matters more than it looks. Only 19 of the operator's 8,864 notes
		// carry a `captured:` field, so 8,845 of them are dated by filesystem
		// mtime — and recomputing that on every write would mean editing a note
		// moves its capture date, while a cloud-sync client rewriting mtimes would
		// silently shift every after:/before: bound in the corpus. Remembering the
		// first date observed pins them without touching a single file.
		captured, capturedSrc := n.Captured.UTC().Format(capturedFormat), n.CapturedSource
		if capturedSrc == "mtime" {
			var prev, prevSrc string
			if err := tx.QueryRow(
				`SELECT captured, captured_src FROM docmeta WHERE id=?`, id,
			).Scan(&prev, &prevSrc); err == nil && prev != "" {
				captured, capturedSrc = prev, prevSrc
			}
		}
		if _, err := tx.Exec(
			`UPDATE docmeta SET flags=?, captured=?, captured_src=?, mtime_ns=?, size=?
			 WHERE id=?`,
			strings.Join(n.Flags, ","), captured, capturedSrc, mtimeNS, size, id); err != nil {
			return err
		}
		if _, err := tx.Exec(`DELETE FROM docs WHERE rowid = ?`, id); err != nil {
			return err
		}
	}

	if _, err := tx.Exec(
		`INSERT INTO docs(rowid, path, title, meta, body) VALUES(?, ?, ?, ?, ?)`,
		id, n.Rel, n.Title, n.Meta, n.Body); err != nil {
		return err
	}
	return tx.Commit()
}

// Delete drops a note from the index. Removing a file from the vault removes it
// from search; nothing here touches the vault.
func (x *Index) Delete(rel string) error {
	x.mu.Lock()
	defer x.mu.Unlock()
	tx, err := x.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	var id int64
	err = tx.QueryRow(`SELECT id FROM docmeta WHERE path = ?`, rel).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return tx.Commit()
	}
	if err != nil {
		return err
	}
	if _, err := tx.Exec(`DELETE FROM docs WHERE rowid = ?`, id); err != nil {
		return err
	}
	if _, err := tx.Exec(`DELETE FROM docmeta WHERE id = ?`, id); err != nil {
		return err
	}
	return tx.Commit()
}

// IndexFile reads one vault file and upserts it. `rel` is vault-relative POSIX.
func (x *Index) IndexFile(rel string) error {
	abs := filepath.Join(x.vault, filepath.FromSlash(rel))
	info, err := os.Stat(abs)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return x.Delete(rel)
		}
		return err
	}
	raw, err := os.ReadFile(abs)
	if err != nil {
		return err
	}
	n := note.Parse(rel, string(raw), info.ModTime())
	return x.Upsert(n, info.ModTime().UnixNano(), info.Size())
}

// fileState is what the reconcile pass compares against.
type fileState struct {
	mtimeNS int64
	size    int64
}

// ReconcileReport is what one full pass changed.
type ReconcileReport struct {
	Scanned int
	Added   int
	Updated int
	Removed int
	Errors  []string
	Elapsed time.Duration

	// Changed and Gone are the paths this pass touched, so the caller can commit
	// them. Reporting only counts here would leave every change the filesystem
	// notifier missed indexed but never committed — and since the notifier is the
	// unreliable half on a cloud-sync mount, that is most of them.
	Changed []string
	Gone    []string
}

// Reconcile walks the vault and makes the index agree with it: new files added,
// changed files re-indexed, vanished files dropped.
//
// This exists because the filesystem notifier cannot be trusted on its own. The
// vault currently lives on a cloud-sync mount, where events are dropped and
// coalesced, and an index that is only as correct as fsnotify was that day is an
// index nobody can reason about. A periodic full pass makes correctness
// independent of the notifier, and on an unchanged corpus it is a cheap
// stat-and-compare rather than a re-read.
func (x *Index) Reconcile() (ReconcileReport, error) {
	started := time.Now()
	rep := ReconcileReport{}

	known, err := x.knownState()
	if err != nil {
		return rep, err
	}

	seen := make(map[string]bool, len(known))
	walkErr := filepath.WalkDir(x.vault, func(abs string, d fs.DirEntry, err error) error {
		if err != nil {
			// An unreadable directory is worth reporting and not worth aborting
			// a whole pass over — a cloud mount can hand back a transient error
			// for one subtree while the rest of the vault is fine.
			rep.Errors = append(rep.Errors, fmt.Sprintf("%s: %v", abs, err))
			if d != nil && d.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		name := d.Name()
		if d.IsDir() {
			if abs != x.vault && strings.HasPrefix(name, ".") {
				return fs.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(name, ".md") || strings.HasPrefix(name, ".") {
			return nil
		}
		rel, relErr := filepath.Rel(x.vault, abs)
		if relErr != nil {
			return nil
		}
		rel = filepath.ToSlash(rel)
		seen[rel] = true
		rep.Scanned++

		info, statErr := d.Info()
		if statErr != nil {
			rep.Errors = append(rep.Errors, fmt.Sprintf("%s: %v", rel, statErr))
			return nil
		}
		prev, existed := known[rel]
		if existed && prev.mtimeNS == info.ModTime().UnixNano() && prev.size == info.Size() {
			return nil
		}
		if err := x.IndexFile(rel); err != nil {
			rep.Errors = append(rep.Errors, fmt.Sprintf("%s: %v", rel, err))
			return nil
		}
		rep.Changed = append(rep.Changed, rel)
		if existed {
			rep.Updated++
		} else {
			rep.Added++
		}
		return nil
	})
	if walkErr != nil {
		return rep, walkErr
	}

	for rel := range known {
		if !seen[rel] {
			if err := x.Delete(rel); err != nil {
				rep.Errors = append(rep.Errors, fmt.Sprintf("%s: %v", rel, err))
				continue
			}
			rep.Removed++
			rep.Gone = append(rep.Gone, rel)
		}
	}

	rep.Elapsed = time.Since(started)
	return rep, nil
}

func (x *Index) knownState() (map[string]fileState, error) {
	x.mu.Lock()
	defer x.mu.Unlock()
	rows, err := x.db.Query(`SELECT path, mtime_ns, size FROM docmeta`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]fileState{}
	for rows.Next() {
		var rel string
		var st fileState
		if err := rows.Scan(&rel, &st.mtimeNS, &st.size); err != nil {
			return nil, err
		}
		out[rel] = st
	}
	return out, rows.Err()
}

// Stats is the index's own account of itself, for the status surface.
type Stats struct {
	Documents   int            `json:"documents"`
	Penalized   map[string]int `json:"penalized"`
	Unfiled     int            `json:"unfiled"`
	OldestUnfil string         `json:"oldest_unfiled,omitempty"`
	// CapturedFrom breaks the corpus down by which signal supplied each note's
	// capture date. Reported rather than hidden: on the current corpus almost
	// every note is dated by filesystem mtime, so an after:/before: bound is exact
	// for anything the daemon captured and a good approximation for everything
	// older. That is worth being able to see rather than infer.
	CapturedFrom map[string]int `json:"captured_from"`
	IndexPath    string         `json:"index_path"`
	IndexBytes   int64          `json:"index_bytes"`
}

// Stats reports document counts and the filing queue as a query rather than a
// folder — there is no inbox, so "how many are waiting" is a SELECT.
func (x *Index) Stats() (Stats, error) {
	x.mu.Lock()
	defer x.mu.Unlock()

	s := Stats{
		Penalized:    map[string]int{},
		CapturedFrom: map[string]int{},
		IndexPath:    x.path,
	}
	if err := x.db.QueryRow(`SELECT count(*) FROM docmeta`).Scan(&s.Documents); err != nil {
		return s, err
	}
	if srcRows, err := x.db.Query(
		`SELECT captured_src, count(*) FROM docmeta GROUP BY captured_src`); err == nil {
		for srcRows.Next() {
			var src string
			var n int
			if err := srcRows.Scan(&src, &n); err == nil {
				s.CapturedFrom[src] = n
			}
		}
		srcRows.Close()
	}
	rows, err := x.db.Query(`SELECT flags, count(*) FROM docmeta WHERE flags <> '' GROUP BY flags`)
	if err != nil {
		return s, err
	}
	defer rows.Close()
	for rows.Next() {
		var flags string
		var n int
		if err := rows.Scan(&flags, &n); err != nil {
			return s, err
		}
		for _, f := range strings.Split(flags, ",") {
			s.Penalized[f] += n
		}
	}
	if err := rows.Err(); err != nil {
		return s, err
	}

	// The filing queue's two numbers: how many are waiting, and the age of the
	// oldest. Age-dominant rather than size-dominant — fifty fresh unfiled items
	// is an ordinary Tuesday under a standing ingest; a three-day-old oldest
	// item means the pipeline stalled.
	_ = x.db.QueryRow(
		`SELECT count(*) FROM docmeta WHERE ','||flags||',' LIKE '%,status,%'`).Scan(&s.Unfiled)
	var oldest sql.NullString
	_ = x.db.QueryRow(
		`SELECT min(captured) FROM docmeta WHERE ','||flags||',' LIKE '%,status,%'`).Scan(&oldest)
	if oldest.Valid {
		s.OldestUnfil = oldest.String
	}
	if fi, err := os.Stat(x.path); err == nil {
		s.IndexBytes = fi.Size()
	}
	return s, nil
}

// Paths returns every indexed path, sorted. Used by the parity command.
func (x *Index) Paths() ([]string, error) {
	x.mu.Lock()
	defer x.mu.Unlock()
	rows, err := x.db.Query(`SELECT path FROM docmeta`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var p string
		if err := rows.Scan(&p); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	sort.Strings(out)
	return out, rows.Err()
}
