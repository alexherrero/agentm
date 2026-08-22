package sources

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"
)

// Losing the registry, and getting it back.
//
// Durability follows files-are-truth. The table lives in the index database for
// fast lookup, and the index is a cache the daemon is allowed to discard and
// rebuild whenever its schema changes. So the registry has to survive that, and
// the way it survives is by not being the only copy of anything.
//
// # What the corpus can prove
//
// Every memory names the unit it came from, the content that unit had when it
// was mined, and the pass version that mined it. Scanning what the index already
// caches therefore recovers identity, hash, version and yield — everything a
// `Seen` lookup needs — without re-reading a single email.
//
// That is the claim the design makes, and it is only true because the
// provenance is per-note. A memory carrying its source id alone would let a
// rebuild recover *which* sources had been read and not *whether they could be
// skipped*, and a registry that knows the names of the things it has processed
// but has to process them again is not a registry.
//
// # What the corpus cannot prove, and why it is exactly two things
//
// A source read and found to contain nothing worth keeping produced no memory,
// so no note carries its provenance and no scan can find it. It looks exactly
// like a source nobody has ever opened — and it is the material least likely to
// repay a second reading, which makes it the most expensive kind to forget.
//
// A growing source's cursor is not a property of any memory either. It is a
// position in a log, and the memories distilled from that log say nothing about
// how far along it the reader got.
//
// Those two live in a small committed file under `Agent/_meta/`. Losing the
// table costs a re-scan; losing that file costs memories.

// SidecarName is the committed file's name under the meta directory.
const SidecarName = "source-registry.json"

// Sidecar is what a corpus scan cannot recover.
//
// Deliberately not a copy of the table. A mirror of every row would drift from
// the table between writes and there would be no way to tell which was right;
// this holds only the rows nothing else can produce, so the two can never
// disagree about the same fact.
type Sidecar struct {
	// WrittenBy and WrittenAt are the attribution the design asks for. This file
	// is committed to the vault's history alongside the operator's own notes,
	// and a machine-written file that does not say so is one somebody will
	// eventually hand-edit.
	WrittenBy string    `json:"written_by"`
	WrittenAt time.Time `json:"written_at"`
	// Note is for the human who opens this file wondering what it is.
	Note string `json:"note"`

	// ZeroYield are sources read and found to contain nothing.
	ZeroYield []Record `json:"zero_yield"`
	// Cursors are growing sources and how far along them the reader got.
	Cursors []Record `json:"cursors"`
}

const sidecarNote = "Written by agentmd. This is the half of the source " +
	"registry a corpus scan cannot rebuild: sources that were read and produced " +
	"no memory (nothing carries their id, so nothing can find them again), and " +
	"the cursors of growing sources (a position in a log is not a property of " +
	"any memory). Everything else in the registry is recovered from the " +
	"provenance each memory carries. Losing the index costs a re-scan; losing " +
	"this file costs re-reading material already read."

// SidecarPath is where the file lives for a given meta directory.
func SidecarPath(metaDir string) string { return filepath.Join(metaDir, SidecarName) }

// SaveSidecar writes the unrebuildable half of the registry.
//
// Written whole and atomically rather than appended: the file is small by
// construction, and a half-written record of what has already been read is worse
// than none — it would silently exempt a source nobody had actually looked at.
func (r *Registry) SaveSidecar(ctx context.Context, metaDir string, now time.Time) (Sidecar, error) {
	side := Sidecar{
		WrittenBy: "agentmd",
		WrittenAt: now.UTC().Truncate(time.Second),
		Note:      sidecarNote,
	}

	zero, err := r.ZeroYield(ctx)
	if err != nil {
		return side, err
	}
	side.ZeroYield = zero

	growing, err := r.byKind(ctx, Growing)
	if err != nil {
		return side, err
	}
	side.Cursors = growing

	// Both lists arrive sorted — ZeroYield and byKind each order by id, which is
	// what keeps this file stable. It is committed to the vault's history, and
	// one that reordered itself on every nightly run would put a diff in the log
	// every night that said nothing. A second sort here was removed: no input
	// could reach it, so nothing could tell whether it was doing anything.

	blob, err := json.MarshalIndent(side, "", "  ")
	if err != nil {
		return side, err
	}
	blob = append(blob, '\n')

	if err := os.MkdirAll(metaDir, 0o755); err != nil {
		return side, err
	}
	path := SidecarPath(metaDir)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, blob, 0o644); err != nil {
		return side, err
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp)
		return side, err
	}
	return side, nil
}

// LoadSidecar reads the committed file. A missing file is an empty sidecar
// rather than an error: a vault that has never ingested anything has none, and
// refusing to rebuild without one would make the first rebuild impossible.
func LoadSidecar(metaDir string) (Sidecar, error) {
	blob, err := os.ReadFile(SidecarPath(metaDir))
	if os.IsNotExist(err) {
		return Sidecar{}, nil
	}
	if err != nil {
		return Sidecar{}, err
	}
	var side Sidecar
	if err := json.Unmarshal(blob, &side); err != nil {
		return Sidecar{}, fmt.Errorf("sources: %s will not parse; it is the only "+
			"copy of what a corpus scan cannot recover, so a rebuild stops rather "+
			"than quietly proceeding without it: %w", SidecarPath(metaDir), err)
	}
	return side, nil
}

// byKind lists one shape of source.
func (r *Registry) byKind(ctx context.Context, kind Kind) ([]Record, error) {
	rows, err := r.db.QueryContext(ctx, `
		SELECT id, namespace, kind, hash, cursor, version, yield, first_seen, last_seen
		FROM sources WHERE kind = ? ORDER BY id`, string(kind))
	if err != nil {
		return nil, fmt.Errorf("sources: listing %s sources: %w", kind, err)
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

// Provenance is what one memory records about where it came from.
type Provenance struct {
	// Source is the namespaced identity, as written.
	Source string
	// Hash is the content the unit had when it was mined.
	Hash string
	// Version is the pass that mined it.
	Version string
	// Memories is how many notes carry this provenance.
	Memories int
}

// CorpusScan yields the provenance the corpus records, one entry per source.
type CorpusScan func(ctx context.Context) ([]Provenance, error)

// RebuildReport is what one rebuild did.
type RebuildReport struct {
	// Dropped is how many rows the wipe removed.
	Dropped int64 `json:"dropped"`
	// FromCorpus is how many the scan put back.
	FromCorpus int `json:"from_corpus"`
	// FromSidecar is how many the committed file put back — the two classes a
	// scan cannot reach.
	FromSidecar int `json:"from_sidecar"`
	// Unrecoverable names sources the scan found without enough provenance to
	// make skippable. Named rather than counted: each one is material that will
	// be read again, and a number nobody can act on reads as a rounding error.
	Unrecoverable []string      `json:"unrecoverable,omitempty"`
	Elapsed       time.Duration `json:"elapsed"`
}

// Rebuild replaces the registry with what the corpus and the committed file can
// prove between them.
//
// Wipe-then-rebuild rather than merge, for the reason the ledger's rebuild gives:
// a merge keeps rows nothing supports any more — a source whose memories were
// all deleted, an identity that no longer appears anywhere — and those are
// exactly the rows that would claim a source had been read when nothing can show
// it. After a rebuild the table says only what the files say.
func (r *Registry) Rebuild(ctx context.Context, scan CorpusScan, side Sidecar) (RebuildReport, error) {
	started := time.Now()
	var rep RebuildReport
	if scan == nil {
		return rep, fmt.Errorf("sources: a rebuild needs a corpus scan; without " +
			"one it would wipe the table and call the empty result a recovery")
	}

	found, err := scan(ctx)
	if err != nil {
		return rep, fmt.Errorf("sources: scanning the corpus: %w", err)
	}

	dropped, err := r.Drop(ctx)
	if err != nil {
		return rep, err
	}
	rep.Dropped = dropped

	for _, p := range found {
		id, err := ParseID(p.Source)
		if err != nil {
			// A `source:` that is not an identity is prose somebody wrote, not a
			// unit anything mined. Skipped silently rather than reported: the
			// corpus has 138 of these and a rebuild that listed them all every
			// time would bury the entries that matter.
			continue
		}
		if p.Hash == "" {
			// Found, but not skippable. The memory names its source and not the
			// content that source had, so nothing can tell whether the unit has
			// changed since — and re-reading is the only safe answer.
			rep.Unrecoverable = append(rep.Unrecoverable, id.String())
			continue
		}
		if err := r.Register(ctx, Unit{
			ID: id, Kind: Immutable, Hash: p.Hash, Version: p.Version,
			Yield: p.Memories,
		}); err != nil {
			return rep, fmt.Errorf("sources: recovering %s: %w", id, err)
		}
		rep.FromCorpus++
	}

	// The sidecar last, so its rows win. A growing source may also have produced
	// memories, and the scan would have written it back as immutable with a hash
	// — which is the wrong shape for a log that is still being appended to, and
	// would stop its tail ever being read.
	for _, rec := range append(append([]Record{}, side.ZeroYield...), side.Cursors...) {
		u := Unit{
			ID: rec.ID, Kind: rec.Kind, Hash: rec.Hash, Cursor: rec.Cursor,
			Version: rec.Version, Yield: rec.Yield,
		}
		if err := r.Register(ctx, u); err != nil {
			return rep, fmt.Errorf("sources: restoring %s from the committed "+
				"file: %w", rec.ID, err)
		}
		rep.FromSidecar++
	}

	sort.Strings(rep.Unrecoverable)
	rep.Elapsed = time.Since(started)
	return rep, nil
}
