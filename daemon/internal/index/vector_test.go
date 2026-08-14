package index

import (
	"fmt"
	"math"
	"strings"
	"testing"
	"unicode/utf8"
)

// The vector arm is tested against hand-written vectors rather than against a
// model. Everything below is a claim about the daemon's own arithmetic and
// bookkeeping — which document a cosine ranks first, which notes are pending,
// what happens when a scope is empty — and a real embedder would make every one
// of those assertions depend on what a 300MB file thinks about English this week.

// unit builds a unit-length vector from raw components, the way the client does
// before storing one.
func unit(xs ...float32) []float32 {
	out := make([]float32, len(xs))
	copy(out, xs)
	var sum float64
	for _, x := range out {
		sum += float64(x) * float64(x)
	}
	if sum == 0 {
		return out
	}
	inv := float32(1 / math.Sqrt(sum))
	for i := range out {
		out[i] *= inv
	}
	return out
}

func docID(tb testing.TB, x *Index, rel string) int64 {
	tb.Helper()
	var id int64
	if err := x.db.QueryRow(`SELECT id FROM docmeta WHERE path = ?`, rel).Scan(&id); err != nil {
		tb.Fatalf("no docmeta row for %s: %v", rel, err)
	}
	return id
}

func TestVectorSearchRanksByCosine(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/near.md", "near", "body")
	addNote(t, x, "memory/mid.md", "mid", "body")
	addNote(t, x, "memory/far.md", "far", "body")

	// Three vectors at known angles from the query (1,0,0): identical, 45°, and
	// opposed. The expected order is therefore not a property of any model.
	rows := []VectorRow{
		{DocID: docID(t, x, "memory/near.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: docID(t, x, "memory/mid.md"), MtimeNS: 1, Vec: unit(1, 1, 0)},
		{DocID: docID(t, x, "memory/far.md"), MtimeNS: 1, Vec: unit(-1, 0, 0)},
	}
	if err := x.PutVectors("test-model", rows); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}

	got, err := x.VectorSearch(unit(1, 0, 0), "test-model", 3, "", "")
	if err != nil {
		t.Fatalf("VectorSearch: %v", err)
	}
	want := []string{"memory/near.md", "memory/mid.md", "memory/far.md"}
	if len(got) != len(want) {
		t.Fatalf("got %d results, want %d", len(got), len(want))
	}
	for i, w := range want {
		if got[i].Path != w {
			t.Errorf("rank %d: got %s, want %s", i+1, got[i].Path, w)
		}
	}
	// Cosine of 0°, 45°, 180° — pinned to literals rather than recomputed with
	// the same dot product the implementation uses.
	for i, w := range []float64{1.0, 0.7071, -1.0} {
		if math.Abs(got[i].Score-w) > 0.001 {
			t.Errorf("rank %d score %.4f, want ~%.4f", i+1, got[i].Score, w)
		}
	}
}

// A query embedded by one model must never be scored against another model's
// rows. The two vector spaces are unrelated, so the resulting ranking is not a
// weak answer, it is a meaningless one that looks exactly like a real result.
func TestVectorSearchIgnoresOtherModels(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/a.md", "a", "body")
	if err := x.PutVectors("model-a", []VectorRow{
		{DocID: docID(t, x, "memory/a.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}

	got, err := x.VectorSearch(unit(1, 0, 0), "model-b", 5, "", "")
	if err != nil {
		t.Fatalf("VectorSearch: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("model-b saw %d of model-a's vectors; they are not comparable", len(got))
	}
}

// A stored vector of a different width is skipped rather than scored. Reaching
// past the end of the shorter one would panic; scoring a prefix would rank.
func TestVectorSearchSkipsWrongWidth(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/wide.md", "wide", "body")
	addNote(t, x, "memory/right.md", "right", "body")
	if err := x.PutVectors("m", []VectorRow{
		{DocID: docID(t, x, "memory/wide.md"), MtimeNS: 1, Vec: unit(1, 0, 0, 0)},
		{DocID: docID(t, x, "memory/right.md"), MtimeNS: 1, Vec: unit(0, 1, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	got, err := x.VectorSearch(unit(0, 1, 0), "m", 5, "", "")
	if err != nil {
		t.Fatalf("VectorSearch: %v", err)
	}
	if len(got) != 1 || got[0].Path != "memory/right.md" {
		t.Fatalf("got %v, want only memory/right.md", got)
	}
}

// A note's several chunks must collapse to one result, scored by whichever
// chunk matches best — not the first chunk scanned, not an average, and not
// once per chunk.
func TestVectorSearchScoresNoteByBestChunk(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/multi.md", "multi", "body")
	id := docID(t, x, "memory/multi.md")

	// Chunk 0 is 90° off the query; chunk 1 is a perfect match.
	if err := x.PutVectors("m", []VectorRow{
		{DocID: id, ChunkIdx: 0, MtimeNS: 1, Vec: unit(0, 1, 0)},
		{DocID: id, ChunkIdx: 1, MtimeNS: 1, Vec: unit(1, 0, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}

	got, err := x.VectorSearch(unit(1, 0, 0), "m", 5, "", "")
	if err != nil {
		t.Fatalf("VectorSearch: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d results, want exactly 1 (one note, however many chunks): %v", len(got), got)
	}
	if got[0].Path != "memory/multi.md" {
		t.Fatalf("got %s, want memory/multi.md", got[0].Path)
	}
	if diff := got[0].Score - 1.0; diff > 0.001 || diff < -0.001 {
		t.Errorf("score = %.4f, want ~1.0 — the perfect-match chunk must win, not the 90°-off one",
			got[0].Score)
	}
}

func TestScopeSelectsPendingNotes(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/in.md", "in", "body")
	addNote(t, x, "desk/also-in.md", "also in", "body")
	addNote(t, x, "_meta/out.md", "out", "body")

	pending, err := x.PendingEmbeds("m", []string{"memory", "desk"}, 0)
	if err != nil {
		t.Fatalf("PendingEmbeds: %v", err)
	}
	var paths []string
	for _, p := range pending {
		paths = append(paths, p.Path)
	}
	want := "desk/also-in.md,memory/in.md" // ordered by path, so a resume is deterministic
	if strings.Join(paths, ",") != want {
		t.Fatalf("pending = %v, want %s", paths, want)
	}
}

// An empty scope must embed nothing. The dangerous reading is the opposite one:
// a scope that fell back to "everything" on a misread config would embed the
// 200,000-token meta notes the scope exists to exclude, and the only symptom
// would be a slow backfill and worse retrieval.
func TestEmptyScopeMatchesNothing(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/in.md", "in", "body")
	pending, err := x.PendingEmbeds("m", nil, 0)
	if err != nil {
		t.Fatalf("PendingEmbeds: %v", err)
	}
	if len(pending) != 0 {
		t.Fatalf("an empty scope selected %d notes; it must select none", len(pending))
	}
	n, err := x.InScopeCount(nil)
	if err != nil {
		t.Fatalf("InScopeCount: %v", err)
	}
	if n != 0 {
		t.Fatalf("InScopeCount(nil) = %d, want 0", n)
	}
}

// A prefix must match a directory boundary, not a string prefix. `desk` must not
// swallow `desktop`, and an underscore in a directory name is a literal — LIKE
// reads it as a single-character wildcard, so `desk_2026` would otherwise match
// `desk-2026` too.
func TestScopeMatchesDirectoryBoundaries(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "desk/yes.md", "yes", "body")
	addNote(t, x, "desktop/no.md", "no", "body")
	addNote(t, x, "desk_2026/yes.md", "yes", "body")
	addNote(t, x, "desk-2026/no.md", "no", "body")

	pending, err := x.PendingEmbeds("m", []string{"desk", "desk_2026"}, 0)
	if err != nil {
		t.Fatalf("PendingEmbeds: %v", err)
	}
	var paths []string
	for _, p := range pending {
		paths = append(paths, p.Path)
	}
	want := "desk/yes.md,desk_2026/yes.md"
	if strings.Join(paths, ",") != want {
		t.Fatalf("pending = %v, want %s", paths, want)
	}
}

// A note with several chunks must be one pending entry, not one per stale
// chunk — PendingEmbeds hands a note-shaped list to the backfill loop, and a
// duplicate entry there would re-chunk and re-embed the same note twice in one
// pass.
func TestPendingEmbedsTreatsMultiChunkNoteAsOneUnit(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/multi.md", "multi", "body")
	id := docID(t, x, "memory/multi.md")

	if err := x.PutVectors("m", []VectorRow{
		{DocID: id, ChunkIdx: 0, MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: id, ChunkIdx: 1, MtimeNS: 1, Vec: unit(0, 1, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	pending, err := x.PendingEmbeds("m", []string{"memory"}, 0)
	if err != nil {
		t.Fatalf("PendingEmbeds: %v", err)
	}
	if len(pending) != 0 {
		t.Fatalf("a note whose chunks are all current is pending: %v", pending)
	}

	// Edit the note: both chunks are now stale, and it must come back as
	// exactly one pending entry.
	addNoteAt(t, x, "memory/multi.md", "multi", "edited body", 2)
	pending, err = x.PendingEmbeds("m", []string{"memory"}, 0)
	if err != nil {
		t.Fatalf("PendingEmbeds: %v", err)
	}
	if len(pending) != 1 || pending[0].Path != "memory/multi.md" {
		t.Fatalf("got %v, want exactly one pending entry for memory/multi.md", pending)
	}
}

// A re-indexed note must come back as pending: its vector describes the previous
// revision. This is the whole staleness contract, and getting it wrong means an
// edited note keeps answering with what it used to say.
func TestReindexedNoteBecomesPending(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/n.md", "n", "first body")
	id := docID(t, x, "memory/n.md")
	if err := x.PutVectors("m", []VectorRow{{DocID: id, MtimeNS: 1, Vec: unit(1, 0, 0)}}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	pending, err := x.PendingEmbeds("m", []string{"memory"}, 0)
	if err != nil {
		t.Fatalf("PendingEmbeds: %v", err)
	}
	if len(pending) != 0 {
		t.Fatalf("a freshly embedded note is pending: %v", pending)
	}

	// Re-index with a different mtime, as a real edit would.
	addNoteAt(t, x, "memory/n.md", "n", "second body", 2)
	pending, err = x.PendingEmbeds("m", []string{"memory"}, 0)
	if err != nil {
		t.Fatalf("PendingEmbeds: %v", err)
	}
	if len(pending) != 1 || pending[0].Path != "memory/n.md" {
		t.Fatalf("edited note not pending: %v", pending)
	}
	if !strings.Contains(pending[0].Body, "second") {
		t.Fatalf("pending body is the old revision: %q", pending[0].Body)
	}
}

// A note re-embedded with fewer chunks than it had before must not leave the
// extra ones behind. PutVectors replaces a note's whole chunk set rather than
// upserting individual chunk_idx values into it precisely to prevent this.
func TestPutVectorsReplacesShrunkChunkSet(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/shrinks.md", "shrinks", "body")
	id := docID(t, x, "memory/shrinks.md")

	if err := x.PutVectors("m", []VectorRow{
		{DocID: id, ChunkIdx: 0, MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: id, ChunkIdx: 1, MtimeNS: 1, Vec: unit(0, 1, 0)},
		{DocID: id, ChunkIdx: 2, MtimeNS: 1, Vec: unit(0, 0, 1)},
	}); err != nil {
		t.Fatalf("PutVectors (3 chunks): %v", err)
	}
	var n int
	if err := x.db.QueryRow(`SELECT count(*) FROM embeddings WHERE doc_id = ?`, id).Scan(&n); err != nil {
		t.Fatalf("counting: %v", err)
	}
	if n != 3 {
		t.Fatalf("after the first write: %d chunk rows, want 3", n)
	}

	// Re-embed as if the note (or the chunking policy) now produces one chunk.
	if err := x.PutVectors("m", []VectorRow{
		{DocID: id, ChunkIdx: 0, MtimeNS: 2, Vec: unit(1, 1, 1)},
	}); err != nil {
		t.Fatalf("PutVectors (1 chunk): %v", err)
	}
	if err := x.db.QueryRow(`SELECT count(*) FROM embeddings WHERE doc_id = ?`, id).Scan(&n); err != nil {
		t.Fatalf("counting: %v", err)
	}
	if n != 1 {
		t.Fatalf("after re-embedding with fewer chunks: %d chunk row(s) survived, want 1 "+
			"(chunk_idx 1 and 2 are orphans)", n)
	}
}

// Deleting a note takes its vector with it. docmeta.id is the join key and a
// re-added note gets a fresh one, so a surviving row would be an orphan that no
// count can see and no query can reach.
func TestDeleteRemovesVector(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/gone.md", "gone", "body")
	id := docID(t, x, "memory/gone.md")
	if err := x.PutVectors("m", []VectorRow{{DocID: id, MtimeNS: 1, Vec: unit(1, 0, 0)}}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	if err := x.Delete("memory/gone.md"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	var n int
	if err := x.db.QueryRow(`SELECT count(*) FROM embeddings`).Scan(&n); err != nil {
		t.Fatalf("counting: %v", err)
	}
	if n != 0 {
		t.Fatalf("%d orphaned vector(s) survived the note's deletion", n)
	}
}

func TestVectorStatsCountsCoverage(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/a.md", "a", "body")
	addNote(t, x, "memory/b.md", "b", "body")
	addNote(t, x, "_meta/c.md", "c", "body")
	if err := x.PutVectors("m", []VectorRow{
		{DocID: docID(t, x, "memory/a.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	st, err := x.VectorStats("m", []string{"memory"})
	if err != nil {
		t.Fatalf("VectorStats: %v", err)
	}
	if st.InScope != 2 {
		t.Errorf("InScope = %d, want 2 (the _meta note is out of scope)", st.InScope)
	}
	if st.Vectors != 1 {
		t.Errorf("Vectors = %d, want 1", st.Vectors)
	}
	if st.Notes != 1 {
		t.Errorf("Notes = %d, want 1", st.Notes)
	}
	if st.Dim != 3 {
		t.Errorf("Dim = %d, want 3", st.Dim)
	}
	if st.Complete() {
		t.Error("Complete() is true with one of two notes embedded")
	}
}

// Vectors counts chunk rows; Notes counts notes. A note split into several
// chunks must move only the first, or the status line would report more notes
// "embedded" than the scope actually contains.
func TestVectorStatsNotesCountsNotesNotChunks(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/chunked.md", "chunked", "body")
	id := docID(t, x, "memory/chunked.md")
	if err := x.PutVectors("m", []VectorRow{
		{DocID: id, ChunkIdx: 0, MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: id, ChunkIdx: 1, MtimeNS: 1, Vec: unit(0, 1, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	st, err := x.VectorStats("m", []string{"memory"})
	if err != nil {
		t.Fatalf("VectorStats: %v", err)
	}
	if st.Vectors != 2 {
		t.Errorf("Vectors = %d, want 2 (one row per chunk)", st.Vectors)
	}
	if st.Notes != 1 {
		t.Errorf("Notes = %d, want 1 (one note, however many chunks)", st.Notes)
	}
	if !st.Complete() {
		t.Error("Complete() is false with the only in-scope note fully embedded, " +
			"chunked or not")
	}
}

// Round-tripping through the blob encoding must be exact. Little-endian is
// pinned because index files are copied between machines — the corpus snapshots
// are exactly that — and a vector decoded the other way round is not an error,
// it is a number that ranks wrongly.
func TestVectorBlobRoundTrip(t *testing.T) {
	in := []float32{1, -1, 0.5, 0, 3.4028235e38, 1e-38}
	got := decodeVec(encodeVec(in), nil)
	if len(got) != len(in) {
		t.Fatalf("decoded %d values, want %d", len(got), len(in))
	}
	for i := range in {
		if got[i] != in[i] {
			t.Errorf("index %d: got %v, want %v", i, got[i], in[i])
		}
	}
	// The first four bytes of 1.0f little-endian, written out rather than
	// computed, so a change of byte order fails here rather than in a ranking.
	if b := encodeVec([]float32{1}); b[0] != 0x00 || b[1] != 0x00 || b[2] != 0x80 || b[3] != 0x3f {
		t.Errorf("1.0 encoded as % x, want 00 00 80 3f (little-endian)", b)
	}
}

// A note that already fits the window must come back byte-for-byte as the same
// single string EmbedText has always produced — the common case (94% of the
// corpus) has to be unchanged by chunking existing at all.
func TestChunkTextReturnsWholeNoteWhenItFits(t *testing.T) {
	got := ChunkText("title", "short body", 2048)
	want := EmbedText("title", "short body")
	if len(got) != 1 || got[0] != want {
		t.Fatalf("ChunkText = %v, want exactly one chunk %q", got, want)
	}
}

// A long body must split into overlapping, title-carrying pieces at the exact
// offsets the budget and overlap formulas produce. Pinned to literal slice
// expressions of the fixture rather than re-derived from the implementation:
// budget=(74-64)*3=30, prefix="T\n\n"=3 bytes, bodyBudget=27, overlap=27/10=2.
func TestChunkTextSplitsLongBodyWithOverlapAndTitleOnEveryChunk(t *testing.T) {
	var body strings.Builder
	for i := 0; i < 20; i++ {
		fmt.Fprintf(&body, "A%02d", i) // 20 unique 3-byte markers, 60 bytes total
	}
	b := body.String()

	got := ChunkText("T", b, 74)
	want := []string{
		"T\n\n" + b[0:27],
		"T\n\n" + b[25:52],
		"T\n\n" + b[50:60],
	}
	if len(got) != len(want) {
		t.Fatalf("got %d chunks, want %d: %q", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("chunk %d = %q, want %q", i, got[i], want[i])
		}
	}
}

// No chunk may split a multi-byte rune, however the byte budget lands relative
// to one. This is the same hazard TruncateForModel used to guard against a
// single time; chunking hits it at every internal boundary.
func TestChunkTextNeverSplitsARune(t *testing.T) {
	body := strings.Repeat("→", 20) // 3-byte rune, 60 bytes, 20 runes
	// budget=(76-64)*3=36; prefix="AB\n\n"=4; bodyBudget=32, which is not a
	// multiple of 3 — the first cut target lands mid-rune by construction.
	got := ChunkText("AB", body, 76)
	if len(got) < 2 {
		t.Fatalf("fixture did not force a split: got %d chunk(s)", len(got))
	}
	for i, c := range got {
		if !utf8.ValidString(c) {
			t.Errorf("chunk %d is not valid UTF-8: %q", i, c)
		}
		if strings.ContainsRune(c, utf8.RuneError) {
			t.Errorf("chunk %d contains the UTF-8 replacement rune: %q", i, c)
		}
	}
}

func TestEmbedRetryCutHalvesAndTerminates(t *testing.T) {
	s := strings.Repeat("a", 100)
	for i := 0; i < 10 && s != ""; i++ {
		next := EmbedRetryCut(s)
		if len(next) >= len(s) {
			t.Fatalf("retry cut did not shrink: %d -> %d", len(s), len(next))
		}
		s = next
	}
	if s != "" {
		t.Fatalf("retry cut did not terminate; %d bytes left", len(s))
	}
}

func TestEmbedTextJoinsTitleAndBody(t *testing.T) {
	for _, tc := range []struct{ title, body, want string }{
		{"T", "B", "T\n\nB"},
		{"", "B", "B"},
		{"T", "", "T"},
		{"  T  ", "  B  ", "T\n\nB"},
	} {
		if got := EmbedText(tc.title, tc.body); got != tc.want {
			t.Errorf("EmbedText(%q, %q) = %q, want %q", tc.title, tc.body, got, tc.want)
		}
	}
}
