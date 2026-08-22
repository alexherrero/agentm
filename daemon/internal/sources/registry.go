package sources

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"time"
)

// The registry table, and what "processed" means.
//
// Processed means processed *at a version*. A row carries the same pass-version
// stamp the coverage ledger uses, so a better model or a changed filing contract
// makes re-ingestion a deliberate, budgeted backfill rather than either
// impossible or accidental. Without the version in the key, improving the pass
// would leave every already-mined source permanently frozen at whatever the old
// one produced.

// Kind says which shape of watermark a unit carries.
type Kind string

const (
	// Immutable units are recorded by content hash. A sent email, a finished
	// transcript, a fetched article.
	Immutable Kind = "immutable"
	// Growing units are recorded by cursor. A live session log that appends.
	Growing Kind = "growing"
)

// Unit is one piece of source material offered to the registry.
type Unit struct {
	ID ID
	// Kind decides which watermark applies. An empty Kind is an error rather
	// than a default: guessing wrong in the Growing direction re-mines a whole
	// transcript on every append, and guessing wrong in the Immutable direction
	// stops a live log ever being read past its first sweep.
	Kind Kind
	// Hash is the content hash, for an immutable unit. Use HashContent.
	Hash string
	// Cursor is the last offset or message consumed, for a growing unit.
	Cursor string
	// Version is the pass version the unit was processed under.
	Version string
	// Yield is how many memories the pass produced from it. Zero is a real and
	// important answer — a source read and found to contain nothing worth
	// keeping is the one class a corpus rebuild can never recover, because there
	// is no memory carrying its `source` to find.
	Yield int
}

// Record is one row of the registry.
type Record struct {
	ID        ID        `json:"id"`
	Kind      Kind      `json:"kind"`
	Hash      string    `json:"hash,omitempty"`
	Cursor    string    `json:"cursor,omitempty"`
	Version   string    `json:"version,omitempty"`
	Yield     int       `json:"yield"`
	FirstSeen time.Time `json:"first_seen"`
	LastSeen  time.Time `json:"last_seen"`
}

// ZeroYield reports whether this source was read and produced nothing.
//
// The distinction that matters for durability: a source with memories can be
// found again by scanning the corpus for its id, and one without cannot. This is
// what the committed file exists to carry.
func (r Record) ZeroYield() bool { return r.Yield == 0 }

// Registry is the table.
type Registry struct {
	db  *sql.DB
	now func() time.Time
}

// Open prepares the registry's table on an already-open index database.
//
// The same handle the index and the ledger use, for the same reason: one
// connection, so a single resident process never contends with itself over a
// SQLite lock.
func Open(db *sql.DB) (*Registry, error) {
	if db == nil {
		return nil, errors.New("sources: no database handle")
	}
	r := &Registry{db: db}
	if err := r.migrate(); err != nil {
		return nil, err
	}
	return r, nil
}

// SetClock replaces the registry's clock, for tests that need two writes to be
// provably different moments.
func (r *Registry) SetClock(f func() time.Time) { r.now = f }

func (r *Registry) stamp() time.Time {
	if r.now != nil {
		return r.now()
	}
	return time.Now()
}

const stampFormat = "2006-01-02T15:04:05Z"

func (r *Registry) migrate() error {
	stmts := []string{
		// Keyed by the identity string. One row per source unit, whatever
		// happens to it — a growing log that finishes keeps the identity it had
		// while it was growing, because it is the same transcript.
		`CREATE TABLE IF NOT EXISTS sources (
			id         TEXT PRIMARY KEY,
			namespace  TEXT NOT NULL,
			kind       TEXT NOT NULL,
			hash       TEXT NOT NULL DEFAULT '',
			cursor     TEXT NOT NULL DEFAULT '',
			version    TEXT NOT NULL DEFAULT '',
			yield      INTEGER NOT NULL DEFAULT 0,
			first_seen TEXT NOT NULL DEFAULT '',
			last_seen  TEXT NOT NULL DEFAULT '')`,
		`CREATE INDEX IF NOT EXISTS sources_namespace ON sources(namespace)`,
		// The zero-yield query runs over the whole table when the committed file
		// is written, which is the one query that is not a single-row lookup.
		`CREATE INDEX IF NOT EXISTS sources_yield ON sources(yield)`,
	}
	for _, s := range stmts {
		if _, err := r.db.Exec(s); err != nil {
			return fmt.Errorf("sources schema: %w", err)
		}
	}
	return nil
}

// HashContent is the content hash an immutable unit is watermarked by.
func HashContent(content string) string {
	sum := sha256.Sum256([]byte(content))
	return hex.EncodeToString(sum[:])
}

// Register watermarks a unit as processed.
func (r *Registry) Register(ctx context.Context, u Unit) error {
	if u.ID.Namespace == "" || u.ID.Ref == "" {
		return fmt.Errorf("sources: a unit needs a namespaced identity, got %q",
			u.ID.String())
	}
	if !known(u.ID.Namespace) {
		return fmt.Errorf("sources: %q is not a source namespace", u.ID.Namespace)
	}
	switch u.Kind {
	case Immutable:
		if u.Hash == "" {
			return fmt.Errorf("sources: %s is immutable and has no content hash; "+
				"without one it can never be recognised again and would be re-mined "+
				"on every sweep", u.ID)
		}
	case Growing:
		if u.Cursor == "" {
			return fmt.Errorf("sources: %s is growing and has no cursor; without "+
				"one every sweep would start from the beginning of the log", u.ID)
		}
	default:
		return fmt.Errorf("sources: %s has no kind — an immutable unit is "+
			"watermarked by hash and a growing one by cursor, and guessing wrong "+
			"either re-mines a whole transcript on every append or stops a live "+
			"log ever being read past its first sweep", u.ID)
	}

	now := r.stamp().UTC().Format(stampFormat)
	_, err := r.db.ExecContext(ctx, `
		INSERT INTO sources(id, namespace, kind, hash, cursor, version, yield,
		                    first_seen, last_seen)
		VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			kind = excluded.kind,
			hash = excluded.hash,
			cursor = excluded.cursor,
			version = excluded.version,
			yield = excluded.yield,
			last_seen = excluded.last_seen`,
		u.ID.String(), string(u.ID.Namespace), string(u.Kind), u.Hash, u.Cursor,
		u.Version, u.Yield, now, now)
	if err != nil {
		return fmt.Errorf("sources: registering %s: %w", u.ID, err)
	}
	return nil
}

// Seen answers whether this source, at this content and this version, has
// already been processed.
//
// The lookup that saves the money: an identity already in the registry at the
// current hash is skipped without a model call. Both halves of the key matter —
// content, so an edited page is re-read; version, so a better pass can be run
// over material it has already seen.
//
// Growing sources are never "seen". A live log always has a possible new tail,
// and the question to ask of one is where its cursor is, not whether it is
// finished.
// An empty hash needs no special case. Register refuses an immutable unit
// without one, so no immutable row can carry an empty hash, and the query below
// filters on immutable — the comparison finds nothing either way.
func (r *Registry) Seen(ctx context.Context, id ID, hash, version string) (bool, error) {
	var n int
	err := r.db.QueryRowContext(ctx, `
		SELECT count(*) FROM sources
		WHERE id = ? AND kind = ? AND hash = ? AND version = ?`,
		id.String(), string(Immutable), hash, version).Scan(&n)
	if err != nil {
		return false, fmt.Errorf("sources: looking up %s: %w", id, err)
	}
	return n > 0, nil
}

// Cursor is where a growing source was last consumed to. An unknown source
// returns the empty cursor, which means "from the beginning".
func (r *Registry) Cursor(ctx context.Context, id ID) (string, error) {
	var cursor string
	err := r.db.QueryRowContext(ctx,
		`SELECT cursor FROM sources WHERE id = ? AND kind = ?`,
		id.String(), string(Growing)).Scan(&cursor)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("sources: reading %s's cursor: %w", id, err)
	}
	return cursor, nil
}

// Advance moves a growing source's cursor and adds to its yield.
//
// Added rather than replaced, because a growing source is consumed in pieces and
// each sweep's memories are as real as the last one's. Replacing would make the
// yield mean "what the most recent sweep found", and a transcript mined across
// forty sweeps would report the yield of its last tail.
func (r *Registry) Advance(ctx context.Context, id ID, cursor, version string, yield int) error {
	if cursor == "" {
		return fmt.Errorf("sources: %s cannot advance to an empty cursor", id)
	}
	now := r.stamp().UTC().Format(stampFormat)
	res, err := r.db.ExecContext(ctx, `
		INSERT INTO sources(id, namespace, kind, cursor, version, yield,
		                    first_seen, last_seen)
		VALUES(?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			kind = excluded.kind,
			cursor = excluded.cursor,
			version = excluded.version,
			yield = sources.yield + excluded.yield,
			last_seen = excluded.last_seen`,
		id.String(), string(id.Namespace), string(Growing), cursor, version, yield,
		now, now)
	if err != nil {
		return fmt.Errorf("sources: advancing %s: %w", id, err)
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return fmt.Errorf("sources: advancing %s changed nothing", id)
	}
	return nil
}

// Lookup returns one row, and whether there was one.
func (r *Registry) Lookup(ctx context.Context, id ID) (Record, bool, error) {
	row := r.db.QueryRowContext(ctx, `
		SELECT id, namespace, kind, hash, cursor, version, yield, first_seen, last_seen
		FROM sources WHERE id = ?`, id.String())
	rec, err := scanRecord(row)
	if errors.Is(err, sql.ErrNoRows) {
		return Record{}, false, nil
	}
	if err != nil {
		return Record{}, false, fmt.Errorf("sources: reading %s: %w", id, err)
	}
	return rec, true, nil
}

type scannable interface{ Scan(dest ...any) error }

func scanRecord(row scannable) (Record, error) {
	var rec Record
	var idStr, ns, kind, first, last string
	if err := row.Scan(&idStr, &ns, &kind, &rec.Hash, &rec.Cursor, &rec.Version,
		&rec.Yield, &first, &last); err != nil {
		return Record{}, err
	}
	rec.ID = ID{Namespace: Namespace(ns)}
	if _, ref, ok := cutOnce(idStr); ok {
		rec.ID.Ref = ref
	}
	rec.Kind = Kind(kind)
	if t, err := time.Parse(stampFormat, first); err == nil {
		rec.FirstSeen = t.UTC()
	}
	if t, err := time.Parse(stampFormat, last); err == nil {
		rec.LastSeen = t.UTC()
	}
	return rec, nil
}

// cutOnce splits an identity string on its first colon. Written out rather than
// re-parsed through ParseID, because a row already in the table has been
// validated once and re-validating it on read would make a namespace retirement
// silently drop history.
func cutOnce(s string) (string, string, bool) {
	for i := 0; i < len(s); i++ {
		if s[i] == ':' {
			return s[:i], s[i+1:], true
		}
	}
	return s, "", false
}

// All lists the registry, newest first, optionally filtered by namespace.
func (r *Registry) All(ctx context.Context, ns Namespace, limit int) ([]Record, error) {
	q := `SELECT id, namespace, kind, hash, cursor, version, yield, first_seen, last_seen
	      FROM sources`
	var args []any
	if ns != "" {
		q += ` WHERE namespace = ?`
		args = append(args, string(ns))
	}
	q += ` ORDER BY last_seen DESC, id`
	if limit > 0 {
		q += ` LIMIT ?`
		args = append(args, limit)
	}
	rows, err := r.db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, fmt.Errorf("sources: listing: %w", err)
	}
	defer rows.Close()

	var out []Record
	for rows.Next() {
		rec, err := scanRecord(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, rec)
	}
	return out, rows.Err()
}

// ZeroYield lists the sources that were read and produced nothing.
//
// One of the two classes a corpus rebuild cannot recover: there is no memory
// carrying their id, because they produced none. Without this record they look
// exactly like sources nobody has ever looked at, and every sweep would read
// them again — the same money the registry exists to save, spent on the
// material least likely to repay it.
func (r *Registry) ZeroYield(ctx context.Context) ([]Record, error) {
	rows, err := r.db.QueryContext(ctx, `
		SELECT id, namespace, kind, hash, cursor, version, yield, first_seen, last_seen
		FROM sources WHERE yield = 0 ORDER BY id`)
	if err != nil {
		return nil, fmt.Errorf("sources: listing zero-yield records: %w", err)
	}
	defer rows.Close()
	var out []Record
	for rows.Next() {
		rec, err := scanRecord(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, rec)
	}
	return out, rows.Err()
}

// Forget drops one source's row, so it is mined again.
func (r *Registry) Forget(ctx context.Context, id ID) error {
	_, err := r.db.ExecContext(ctx, `DELETE FROM sources WHERE id = ?`, id.String())
	return err
}

// Drop empties the table. The cache loss the durability bar is written against.
func (r *Registry) Drop(ctx context.Context) (int64, error) {
	res, err := r.db.ExecContext(ctx, `DELETE FROM sources`)
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}

// Stats is the registry's account of itself.
type Stats struct {
	Total     int          `json:"total"`
	ByKind    map[Kind]int `json:"by_kind"`
	ZeroYield int          `json:"zero_yield"`
	Memories  int          `json:"memories"`
}

// Count summarizes the registry for the status surface.
func (r *Registry) Count(ctx context.Context) (Stats, error) {
	s := Stats{ByKind: map[Kind]int{}}
	rows, err := r.db.QueryContext(ctx,
		`SELECT kind, count(*), sum(yield), sum(yield = 0) FROM sources GROUP BY kind`)
	if err != nil {
		return s, err
	}
	defer rows.Close()
	for rows.Next() {
		var kind string
		var n, yield, zero sql.NullInt64
		if err := rows.Scan(&kind, &n, &yield, &zero); err != nil {
			return s, err
		}
		s.ByKind[Kind(kind)] = int(n.Int64)
		s.Total += int(n.Int64)
		s.Memories += int(yield.Int64)
		s.ZeroYield += int(zero.Int64)
	}
	return s, rows.Err()
}
