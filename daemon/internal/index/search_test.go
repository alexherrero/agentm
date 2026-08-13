package index

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// probeQuery is the phrase every fixture in this file is built to match. Two
// bare terms, so FTS5's implicit AND requires both — the same shape the
// benchmark queries have.
const probeQuery = "homelab server"

func newTestIndex(tb testing.TB) *Index {
	tb.Helper()
	dir := tb.TempDir()
	x, err := Open(filepath.Join(dir, "index.db"), dir)
	if err != nil {
		tb.Fatalf("opening index: %v", err)
	}
	tb.Cleanup(func() { x.Close() })
	return x
}

func addNote(tb testing.TB, x *Index, rel, title, body string) {
	tb.Helper()
	n := note.Note{
		Rel:            rel,
		Title:          title,
		Body:           body,
		Captured:       time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
		CapturedSource: "mtime",
	}
	if err := x.Upsert(n, 1, int64(len(body))); err != nil {
		tb.Fatalf("indexing %s: %v", rel, err)
	}
}

// filler returns roughly n bytes of prose that does not contain either probe
// term, so a note's size can be set independently of how well it matches.
func filler(n int) string {
	const unit = "lorem ipsum dolor sit amet consectetur adipiscing elit "
	return strings.Repeat(unit, n/len(unit)+1)
}

// buildBigVault writes `huge` notes of about hugeBytes each that match the probe
// once, deep in the body, plus `small` short notes carrying the probe terms in
// their titles. The short ones win the ranking and the huge ones sit behind them
// in the over-fetch window — which is the arrangement that makes the cost of
// snippeting the window visible against the cost of snippeting the top k.
func buildBigVault(tb testing.TB, x *Index, huge, hugeBytes, small int) {
	tb.Helper()
	half := filler(hugeBytes / 2)
	for i := 0; i < huge; i++ {
		body := half + " the homelab server is mentioned here once. " + half
		addNote(tb, x, fmt.Sprintf("huge/dump-%02d.md", i),
			fmt.Sprintf("archive dump %02d", i), body)
	}
	for i := 0; i < small; i++ {
		addNote(tb, x, fmt.Sprintf("small/note-%02d.md", i),
			fmt.Sprintf("homelab server notes %02d", i),
			"the homelab server runs in the closet")
	}
}

// referenceSnippets runs the shape this package used before snippets were split
// out: snippet() computed inside the ranking query, for every over-fetched
// candidate. It is written out here rather than called through the index so it
// stays an independent oracle — the snippets it produces are the snippets Search
// is required to keep producing, and the time it takes is the cost the split was
// supposed to remove.
func referenceSnippets(tb testing.TB, x *Index, match string, limit int) (map[string]string, time.Duration) {
	tb.Helper()
	sql := fmt.Sprintf(`
		SELECT m.path,
		       bm25(docs, %v, %v, %v, %v) AS s,
		       snippet(docs, %d, '[', ']', ' … ', 24)
		FROM docs JOIN docmeta m ON m.id = docs.rowid
		WHERE docs MATCH ?
		ORDER BY s
		LIMIT ?`,
		weightPath, weightTitle, weightMeta, weightBody, bodyColumn)

	started := time.Now()
	rows, err := x.db.Query(sql, match, limit)
	if err != nil {
		tb.Fatalf("reference query: %v", err)
	}
	defer rows.Close()

	out := map[string]string{}
	for rows.Next() {
		var path, snippet string
		var score float64
		if err := rows.Scan(&path, &score, &snippet); err != nil {
			tb.Fatalf("reference scan: %v", err)
		}
		out[path] = snippet
	}
	if err := rows.Err(); err != nil {
		tb.Fatalf("reference rows: %v", err)
	}
	return out, time.Since(started)
}

// collapse is the whitespace normalization Search applies to a raw snippet.
func collapse(s string) string { return strings.Join(strings.Fields(s), " ") }

// TestSearchSnippetContent pins the snippet a known body produces, against a
// literal rather than against another query. Everything else in this file checks
// that Search agrees with the old shape; this checks that the old shape was
// worth agreeing with.
func TestSearchSnippetContent(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "small/only.md", "closet inventory",
		"the homelab server runs in the closet")

	out, err := x.Search(Query{Text: probeQuery, K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 1 {
		t.Fatalf("expected 1 result, got %d", len(out.Results))
	}
	const want = "the [homelab] [server] runs in the closet"
	if got := out.Results[0].Snippet; got != want {
		t.Errorf("snippet\n got %q\nwant %q", got, want)
	}
}

// TestSearchSnippetsMatchTheOldShape is the no-behaviour-change check: the
// returned k carry the same snippets the single-query shape produced for those
// same notes.
func TestSearchSnippetsMatchTheOldShape(t *testing.T) {
	x := newTestIndex(t)
	for i := 0; i < 12; i++ {
		addNote(t, x, fmt.Sprintf("n-%02d.md", i), fmt.Sprintf("note %02d", i),
			fmt.Sprintf("entry %02d. %s the homelab server sits behind the rack, %s",
				i, filler(400), filler(400)))
	}

	const k = 5
	out, err := x.Search(Query{Text: probeQuery, K: k})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != k {
		t.Fatalf("expected %d results, got %d", k, len(out.Results))
	}
	if out.Matched != 12 {
		t.Fatalf("expected all 12 notes in the over-fetch window, got %d", out.Matched)
	}

	want, _ := referenceSnippets(t, x, probeQuery, note.Overfetch)
	for _, r := range out.Results {
		ref, ok := want[r.Path]
		if !ok {
			t.Fatalf("%s was returned but the reference query never saw it", r.Path)
		}
		if r.Snippet == "" {
			t.Errorf("%s came back with no snippet", r.Path)
			continue
		}
		if !strings.Contains(r.Snippet, "[homelab]") || !strings.Contains(r.Snippet, "[server]") {
			t.Errorf("%s snippet does not highlight both terms: %q", r.Path, r.Snippet)
		}
		if got := r.Snippet; got != collapse(ref) {
			t.Errorf("%s snippet drifted from the old shape\n got %q\nwant %q", r.Path, got, collapse(ref))
		}
	}
}

// TestSearchSnippetsOnlyTheSurvivingK is the regression tripwire. The defect it
// guards is invisible in the output — the old shape returned the same snippets,
// it just paid for 200 of them — so the assertion is on how many documents
// snippet() was handed, which is the only place the difference shows.
func TestSearchSnippetsOnlyTheSurvivingK(t *testing.T) {
	x := newTestIndex(t)
	const corpus = 60
	for i := 0; i < corpus; i++ {
		addNote(t, x, fmt.Sprintf("n-%02d.md", i), fmt.Sprintf("note %02d", i),
			"the homelab server hums along quietly")
	}

	before := x.snippeted()
	out, err := x.Search(Query{Text: probeQuery, K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if out.Matched != corpus {
		t.Fatalf("expected the over-fetch window to hold all %d notes, got %d", corpus, out.Matched)
	}
	if got := x.snippeted() - before; got != 5 {
		t.Fatalf("snippet() ran over %d documents for a k=5 search of a %d-note match; "+
			"it must run over the returned k, not the over-fetch window", got, corpus)
	}

	before = x.snippeted()
	if _, err := x.Search(Query{Text: probeQuery, K: 3}); err != nil {
		t.Fatalf("search: %v", err)
	}
	if got := x.snippeted() - before; got != 3 {
		t.Fatalf("k=3 search snippeted %d documents, want 3", got)
	}
}

// TestSearchDoesNotPriceTheOverfetchWindow is the timing half, calibrated
// against the old shape measured in the same run rather than against a fixed
// millisecond ceiling — a slow machine slows both arms and the ratio holds.
//
// The fixture is sized like the corpus that surfaced this: notes above 1 MB,
// which is where snippet()'s document scan stops being free.
func TestSearchDoesNotPriceTheOverfetchWindow(t *testing.T) {
	if testing.Short() {
		t.Skip("builds ~29 MB of index")
	}
	x := newTestIndex(t)
	const (
		huge      = 24
		hugeBytes = 1_200_000
		small     = 8
	)
	buildBigVault(t, x, huge, hugeBytes, small)

	// Warm the page cache for both arms before either is timed.
	referenceSnippets(t, x, probeQuery, note.Overfetch)

	started := time.Now()
	out, err := x.Search(Query{Text: probeQuery, K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	searchTook := time.Since(started)

	want, refTook := referenceSnippets(t, x, probeQuery, note.Overfetch)

	if out.Matched != huge+small {
		t.Fatalf("expected %d notes in the over-fetch window, got %d", huge+small, out.Matched)
	}
	// The fixture only measures what it is meant to if the big notes are the ones
	// being skipped. If they rank into the top k this test is timing something
	// else and its ratio means nothing.
	for _, r := range out.Results {
		if !strings.HasPrefix(r.Path, "small/") {
			t.Fatalf("fixture broken: %s ranked into the top k, so the huge notes are "+
				"not the ones being skipped", r.Path)
		}
		if got := r.Snippet; got != collapse(want[r.Path]) {
			t.Errorf("%s snippet drifted\n got %q\nwant %q", r.Path, got, collapse(want[r.Path]))
		}
	}

	const minRatio = 10
	ratio := float64(refTook) / float64(searchTook)
	t.Logf("search %v against old shape %v over %d notes (%d of them ~%.1f MB) — %.0fx",
		searchTook.Round(time.Microsecond), refTook.Round(time.Microsecond),
		out.Matched, huge, float64(hugeBytes)/1e6, ratio)
	if ratio < minRatio {
		t.Fatalf("search took %v against the old shape's %v (%.1fx). Snippets are being "+
			"computed for the over-fetch window again, not for the returned k",
			searchTook, refTook, ratio)
	}
}

// BenchmarkSearchLargeNotes is the human-readable version of the test above.
// The fusion mode. These pin the four claims task 1 makes: it finds what AND
// cannot, it is strictly opt-in, it ranks by best-single-evidence rather than by
// how many subsets agree, and an unrecognized mode is an error rather than a
// silent fallback.

// paths pulls the result paths out in rank order, for assertions that care about
// ordering rather than scores.
func paths(rs []Result) []string {
	out := make([]string, len(rs))
	for i, r := range rs {
		out[i] = r.Path
	}
	return out
}

// TestFusionFindsWhatAndCannot is the whole reason the mode exists: the terms are
// spread across the corpus, so no note contains all three and the implicit AND
// returns nothing, while the note holding two of them is right there.
func TestFusionFindsWhatAndCannot(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "target.md", "alpha beta notes", "alpha beta appear together here")
	addNote(t, x, "other.md", "unrelated", "gamma appears alone over here")

	const q = "alpha beta gamma"

	andOut, err := x.Search(Query{Text: q, K: 5})
	if err != nil {
		t.Fatalf("and search: %v", err)
	}
	if len(andOut.Results) != 0 {
		t.Fatalf("AND should find nothing (no note has all three terms), got %v",
			paths(andOut.Results))
	}

	fusedOut, err := x.Search(Query{Text: q, K: 5, Mode: ModeFusion})
	if err != nil {
		t.Fatalf("fusion search: %v", err)
	}
	if got := paths(fusedOut.Results); len(got) == 0 || got[0] != "target.md" {
		t.Fatalf("fusion should rank target.md first, got %v", got)
	}
}

// TestFusionIsOptIn guards the ground rule that production recall does not move
// until the cutover. An empty mode and an explicit "and" must be the same search.
func TestFusionIsOptIn(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "target.md", "alpha beta notes", "alpha beta appear together here")

	const q = "alpha beta gamma"
	for _, mode := range []string{"", ModeAnd} {
		out, err := x.Search(Query{Text: q, K: 5, Mode: mode})
		if err != nil {
			t.Fatalf("mode %q: %v", mode, err)
		}
		if len(out.Results) != 0 {
			t.Fatalf("mode %q must keep AND semantics and return nothing, got %v",
				mode, paths(out.Results))
		}
	}
}

// TestFusionRanksByBestEvidenceNotAgreement is the RRF decision, made testable.
// `spread.md` carries all three terms, so every one of the three subsets finds
// it; `precise.md` carries only two, so exactly one subset does — but that one
// match is far stronger. Max-score must prefer precise.md. A count- or
// rank-averaging fusion would prefer spread.md, which is the dilution the
// measurement rejected.
func TestFusionRanksByBestEvidenceNotAgreement(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "precise.md", "alpha beta", "alpha beta")
	addNote(t, x, "spread.md", "notes", "alpha "+filler(4000)+" beta "+filler(4000)+" gamma")

	out, err := x.Search(Query{Text: "alpha beta gamma", K: 5, Mode: ModeFusion})
	if err != nil {
		t.Fatalf("fusion search: %v", err)
	}
	got := paths(out.Results)
	if len(got) != 2 {
		t.Fatalf("both notes should be candidates, got %v", got)
	}
	if got[0] != "precise.md" {
		t.Fatalf("max-score fusion must rank the single strong match first; got %v", got)
	}
	if out.Results[0].Score <= out.Results[1].Score {
		t.Fatalf("precise.md should outscore spread.md, got %.4f vs %.4f",
			out.Results[0].Score, out.Results[1].Score)
	}
}

// TestFusionSnippetComesFromTheWinningSubset — the highlight should show the
// evidence that actually ranked the row, not some other pairing of the terms.
func TestFusionSnippetComesFromTheWinningSubset(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "target.md", "notes", "alpha beta appear together in this sentence")

	out, err := x.Search(Query{Text: "alpha beta gamma", K: 5, Mode: ModeFusion})
	if err != nil {
		t.Fatalf("fusion search: %v", err)
	}
	if len(out.Results) != 1 {
		t.Fatalf("expected one hit, got %v", paths(out.Results))
	}
	snip := out.Results[0].Snippet
	if !strings.Contains(snip, "[alpha]") || !strings.Contains(snip, "[beta]") {
		t.Fatalf("snippet should highlight the winning subset's terms, got %q", snip)
	}
}

// TestFusionSingleTermFallsBackToAnd — one term has no two-term subset, and
// returning nothing for it would be a worse answer than the term's own ranking.
func TestFusionSingleTermFallsBackToAnd(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "target.md", "alpha notes", "alpha appears here")

	out, err := x.Search(Query{Text: "alpha", K: 5, Mode: ModeFusion})
	if err != nil {
		t.Fatalf("fusion search: %v", err)
	}
	if got := paths(out.Results); len(got) != 1 || got[0] != "target.md" {
		t.Fatalf("single-term fusion should fall back to AND, got %v", got)
	}
}

// TestUnknownSearchModeIsAnError — a typo must not silently serve AND results
// while the caller believes it measured something else.
func TestUnknownSearchModeIsAnError(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "target.md", "alpha beta", "alpha beta")

	if _, err := x.Search(Query{Text: "alpha beta", K: 5, Mode: "hybrid"}); err == nil {
		t.Fatal("an unrecognized mode must be an error, not a silent fallback")
	}
}

func BenchmarkSearchLargeNotes(b *testing.B) {
	x := newTestIndex(b)
	buildBigVault(b, x, 24, 1_200_000, 8)

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := x.Search(Query{Text: probeQuery, K: 5}); err != nil {
			b.Fatalf("search: %v", err)
		}
	}
}

// BenchmarkSearchLargeNotesOldShape prices the pre-fix shape on the same fixture,
// so `go test -bench Search` shows both numbers side by side.
func BenchmarkSearchLargeNotesOldShape(b *testing.B) {
	x := newTestIndex(b)
	buildBigVault(b, x, 24, 1_200_000, 8)

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		referenceSnippets(b, x, probeQuery, note.Overfetch)
	}
}
