package index

import (
	"context"
	"fmt"
	"strings"
)

// What the diversity meters read: the most recent memories, with their bodies
// and — where the dense arm has got to them — their vectors.
//
// Recent rather than random, because the meters answer "is what we are writing
// *now* converging". A uniform sample across five years of corpus would mix a
// month of drift into sixty months of history and report almost nothing had
// changed, which is exactly the dilution that lets slow convergence go unnoticed.

// MeterSample is one note as the meters see it.
//
// No body. The index's copy of it lives in an FTS5 virtual table whose path
// column is UNINDEXED, so fetching it by path scans that table once per row —
// measured at 4m1s for five hundred notes against 49ms without. The caller reads
// the bytes from disk, which is both faster and the more truthful source for a
// meter about what the corpus currently says.
type MeterSample struct {
	Rel string
	// Captured is when the note entered the corpus, so the caller can report
	// which window the numbers describe. Four decimals over an unnamed period
	// is a number nobody can act on.
	Captured string
	// Vec is the note's whole-note embedding, or nil if the dense arm has not
	// reached it. Nil rather than a zero vector: the dense meters refuse when
	// they have nothing, and a zero vector would be something.
	Vec []float32
}

// MeterStatus is the lifecycle state the meters measure.
//
// `active` is filed and live. It excludes `unfiled` — raw captures enrichment
// has not run on, which are not its output — `proposed`, which is a mined
// supplement awaiting the operator, and `superseded` / `expired`, which have
// left the live corpus. It also excludes the long tail of ad-hoc statuses the
// vault has accumulated (44 distinct values, 39 of them appearing once or
// twice), which a status allowlist handles and a blocklist would not.
const MeterStatus = "active"

// MeterExcludedDirs are directory names dropped at any depth, because a note
// under one of them is not a filed memory whatever its frontmatter claims.
//
// `_inbox` and `_archive` are here because `status: active` does not exclude
// them — a mining pass writes `active` into the inbox and nothing reconciles it
// on the way out. `scratch` is dreaming's own staging area, and leaving it in
// meant the correction loop's first live run found two of dreaming's proposal
// files 0.994 similar to each other and would have offered to correct them.
// `_shelf` is a browse convention rather than a lifecycle state, but a shelved
// note is deliberately out of the way and not part of what is being written now.
//
// `_opinions` is the one worth arguing about, because recall does search it and
// 24 of its notes carry `status: active`. It is excluded because those notes are
// not memories: they carry `kind: opinion-supplement` and no `type:`, so the
// contract's enum does not cover them, and they hold `mining_confidence` /
// `mining_occurrences` — they are `reflect.py`'s mined material awaiting
// promotion into an opinion file, which is a different pipeline's inbox wearing a
// different name.
//
// The measurement says the same thing. In the corrected window `_opinions` is 24
// notes of 500, and 26 of the 28 notes in pairs above 0.95 — twenty-two copies of
// one mined directive between 0.97 and 0.99, because a template filled twenty-two
// times reads as converged whatever enrichment does. Leaving it in would have
// reproduced the `_inbox` contamination at a twentieth of the size, which is the
// harder version of the bug to notice.
//
// # The residual
//
// This is a blocklist, and blocklists rot. recall.py carries the scar: its own
// comment records a migration that moved the scratch space one level down and
// left a two-segment path here that silently matched nothing, letting dream
// exhaust back into recall. The right rule is an allowlist of the contract's six
// class directories — `semantic`, `procedural`, `episodic`, `entities`,
// `crystallized`, `mocs` — which needs no maintenance because the contract
// already validates it. It is not usable yet: the corpus has not moved, and today
// those six hold three notes between them against 250 in `preferences` and 178 in
// `2026`.
//
// *Re-audit trigger: when the collapse migration has moved the memory space into
// the six class directories, this list is replaced by those six names and this
// comment goes with it.*
var MeterExcludedDirs = []string{"_inbox", "_archive", "scratch", "_shelf", "_opinions"}

// RecentForMeters returns up to n recent notes from the given spaces.
//
// `scope` is the same space list the vector arm uses, so the meters measure the
// part of the corpus the embedder actually covers rather than a wider set whose
// vectors would be missing for a reason that has nothing to do with drift.
//
// Only chunk 0, which is the whole note for everything that fits the embedder's
// window. A long note split into chunks would otherwise contribute several
// points to a distribution about notes, and the notes that split are the long
// ones — so the meters would quietly become a statement about long notes.
// # Why "with vectors" rather than simply "recent"
//
// Measured on the live corpus: of the 500 most recently captured notes, none
// carried a vector; of the newest 2,000, 1,184 did; the oldest 500 were complete.
// The embedder trails capture by one to two thousand notes, so recency alone
// selects almost exactly the notes the dense arm has not reached — which made the
// two embedding meters unable to run at all, every night, while reporting it as a
// missing embedder.
//
// `withVectors` therefore restricts the window to notes that have a current
// chunk-0 vector. The caller passes false when there is no dense arm to wait
// for, so the two lexical meters still run on a corpus that has never been
// embedded.
//
// # Why the population is narrowed, and what it cost not to be
//
// The meters ask whether the memories enrichment writes are converging, so the
// population has to be the memories. It was not. Measured on the live corpus,
// the 500 most recent embedded notes in the vector arm's scope were 393
// `_inbox`, 51 `_opinions`, 45 `desk/scratch`, and four filed memories — so the
// meters were 79% about raw captures enrichment has never touched, plus
// dreaming's own staged proposal files.
//
// The bias had a direction, which is what made it worse than dilution. `_inbox`
// accumulates near-identical mined clippings — `no-handoff-pack-the-cap-is-94`
// through `-98`, the same clipped directive filed five times — and that pushes
// similarity up. The same night, over the same corpus:
//
//	                          scope as it was    filed memories
//	pairwise median                     0.551             0.431
//	pairwise p90                        0.745             0.600
//	pairwise max                        0.994             0.956
//	nearest-neighbour median            0.977             0.764
//	pairs >= 0.985                         72                 0
//
// A convergence line set against the left-hand column would fire on the mining
// pipeline's duplication and report it as enrichment homogenizing the corpus.
//
// The scope came from `config.EmbedScope`, which is three spaces for a reason
// that belongs to a different question: the retrieval gold set's answers live in
// `desk` and `external`, 65 of 90 expected paths, so the vector arm has to reach
// them. Inheriting that scope inherited its reason.
//
// Two filters rather than one, because neither is sufficient. `status` alone
// still admits 765 `_inbox` notes that carry `active` from a mining pass that
// never reconciled them, and 263 in `_archive`. Directory names alone still
// admit `unfiled` captures and `proposed` supplements sitting in the memory
// space. Together they select what recall serves out of the memory space, which
// is the population "is the corpus converging" is a question about.
func (x *Index) RecentForMeters(ctx context.Context, n int, model string,
	scope []string, withVectors bool) ([]MeterSample, error) {
	if n < 1 {
		return nil, nil
	}
	where, args := scopeClause(scope)
	where += " AND m.status = ?"
	args = append(args, MeterStatus)
	for _, d := range MeterExcludedDirs {
		// Escaped, because `_` is a LIKE wildcard and `_inbox` unescaped would
		// also match `Xinbox`. The `%/` prefix is enough given every scope is
		// itself a path prefix, so an excluded directory always has a parent.
		where += ` AND m.path NOT LIKE ? ESCAPE '\'`
		args = append(args, "%/"+escapeLike(strings.Trim(d, "/"))+"/%")
	}
	// An inner join rather than a filter in WHERE, so the window is the most
	// recent *embedded* notes rather than the most recent notes of which few
	// happen to be embedded.
	join := "LEFT JOIN"
	if withVectors {
		join = "JOIN"
	}

	x.mu.Lock()
	defer x.mu.Unlock()

	// Ordered by capture date and then by path. The date alone is ambiguous —
	// capture shards by date and a burst writes many in the same second — and an
	// ambiguous order makes the sample, and so every number, depend on which row
	// SQLite happened to return first.
	q := `SELECT m.path, m.captured, e.vec
	        FROM docmeta m
	        ` + join + ` embeddings e
	               ON e.doc_id = m.id AND e.chunk_idx = 0 AND e.model = ?
	                  AND e.mtime_ns = m.mtime_ns
	       WHERE ` + where + `
	    ORDER BY m.captured DESC, m.path DESC
	       LIMIT ?`

	rows, err := x.db.QueryContext(ctx, q, append(append([]any{model}, args...), n)...)
	if err != nil {
		return nil, fmt.Errorf("sampling the corpus for the meters: %w", err)
	}
	defer rows.Close()

	var out []MeterSample
	for rows.Next() {
		var s MeterSample
		var blob []byte
		if err := rows.Scan(&s.Rel, &s.Captured, &blob); err != nil {
			return nil, err
		}
		if len(blob) > 0 {
			s.Vec = decodeVec(blob, nil)
		}
		out = append(out, s)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	// Reversed into ascending order, so the sample reads oldest-first the way a
	// person would expect a window of recent notes to. The set is the same; the
	// order is not, and the lexical meters slide a window along it.
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out, nil
}
