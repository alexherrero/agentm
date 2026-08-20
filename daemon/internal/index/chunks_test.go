package index

import (
	"path/filepath"
	"strings"
	"testing"
)

func openScratch(t *testing.T) *Index {
	t.Helper()
	dir := t.TempDir()
	idx, err := Open(filepath.Join(dir, "index.db"), filepath.Join(dir, "vault"))
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { idx.Close() })
	return idx
}

// The regression case, and the reason the two chunkings compose rather than one
// replacing the other: a note with no headings must come out exactly as
// `ChunkText` has always produced it. 94% of this corpus is that note.
func TestANoteWithNoHeadingsMatchesChunkTextExactly(t *testing.T) {
	title := "A preference"
	body := "Use Edit rather than Write for a file that already exists.\n"

	want := ChunkText(title, body, 2048)
	got := BuildChunks(title, body, 2048)

	if len(got) != len(want) {
		t.Fatalf("got %d chunks, ChunkText produces %d", len(got), len(want))
	}
	for i := range want {
		if got[i].Content != want[i] {
			t.Errorf("chunk %d differs from ChunkText:\n got %q\nwant %q",
				i, got[i].Content, want[i])
		}
		if got[i].HeaderPath != "" {
			t.Errorf("chunk %d carries header path %q on a note with no headings",
				i, got[i].HeaderPath)
		}
	}
}

// A long note with no headings still window-splits, which is the measured fix
// this must not regress: 562 of 9,473 notes exceed the embedder's window and
// used to lose everything past their head.
func TestALongUnstructuredNoteStillWindowSplits(t *testing.T) {
	// 100 rather than 64: windowBudget subtracts a 64-token headroom, so a
	// ctxTokens of exactly 64 yields a zero-byte budget and ChunkText correctly
	// returns the note whole. A fixture that picked the headroom value would test
	// that fallback while claiming to test the split.
	body := strings.Repeat("This sentence exists to exceed the window budget. ", 400)
	got := BuildChunks("T", body, 100)
	if len(got) < 2 {
		t.Fatalf("a long note produced %d chunks; the window split is gone", len(got))
	}
	for i, c := range got {
		if c.ChunkIdx != i {
			t.Errorf("chunk %d carries idx %d", i, c.ChunkIdx)
		}
	}
}

func TestSectionsCarryTheirHeaderPath(t *testing.T) {
	body := "# Architecture\n\nOverview.\n\n## Ingestion\n\nHow capture works.\n"
	got := BuildChunks("Design", body, 2048)
	if len(got) != 2 {
		t.Fatalf("got %d chunks, want 2", len(got))
	}
	if got[0].HeaderPath != "Architecture" {
		t.Errorf("chunk 0 header path %q", got[0].HeaderPath)
	}
	if got[1].HeaderPath != "Architecture > Ingestion" {
		t.Errorf("chunk 1 header path %q", got[1].HeaderPath)
	}
}

// The two-level split. A section longer than the window produces several rows,
// and every one of them keeps the section's identity — otherwise the second half
// of a long section would be an anonymous fragment.
func TestAnOversizedSectionSplitsAndKeepsOneHeaderPath(t *testing.T) {
	long := strings.Repeat("Filing is a frontmatter edit so nothing moves. ", 200)
	body := "# Short\n\nBrief.\n\n## Long\n\n" + long + "\n"
	got := BuildChunks("T", body, 100)

	var longRows []Chunk
	for _, c := range got {
		if c.HeaderPath == "Short > Long" {
			longRows = append(longRows, c)
		}
	}
	if len(longRows) < 2 {
		t.Fatalf("the oversized section produced %d rows; it did not window-split", len(longRows))
	}
	for _, c := range longRows {
		if c.HeaderPath != "Short > Long" {
			t.Errorf("a piece of the long section lost its header path: %q", c.HeaderPath)
		}
	}
}

// chunk_idx must be a single contiguous space across the whole note, not
// restarted per section — the primary key depends on it.
func TestChunkIndexesAreContiguousAcrossSections(t *testing.T) {
	body := "# A\n\n1.\n\n# B\n\n2.\n\n# C\n\n3.\n"
	got := BuildChunks("T", body, 2048)
	for i, c := range got {
		if c.ChunkIdx != i {
			t.Fatalf("chunk at position %d carries idx %d; the key space is not contiguous", i, c.ChunkIdx)
		}
	}
}

func TestReplaceAndReadBack(t *testing.T) {
	idx := openScratch(t)
	chunks := BuildChunks("T", "# A\n\nAlpha.\n\n# B\n\nBeta.\n", 2048)
	if err := idx.ReplaceChunks(42, chunks); err != nil {
		t.Fatalf("ReplaceChunks: %v", err)
	}
	got, err := idx.ChunksFor(42)
	if err != nil {
		t.Fatalf("ChunksFor: %v", err)
	}
	if len(got) != len(chunks) {
		t.Fatalf("read back %d chunks, wrote %d", len(got), len(chunks))
	}
	for i := range chunks {
		if got[i] != chunks[i] {
			t.Errorf("chunk %d round-tripped as %+v, wrote %+v", i, got[i], chunks[i])
		}
	}
}

// A note that lost a section must lose its rows. Reconciling row-by-row against
// an edited document is how a stale chunk survives an edit that removed the text
// it holds.
func TestReplaceDropsRowsForRemovedSections(t *testing.T) {
	idx := openScratch(t)

	before := BuildChunks("T", "# A\n\n1.\n\n# B\n\n2.\n\n# C\n\n3.\n", 2048)
	if err := idx.ReplaceChunks(7, before); err != nil {
		t.Fatal(err)
	}
	after := BuildChunks("T", "# A\n\n1.\n", 2048)
	if err := idx.ReplaceChunks(7, after); err != nil {
		t.Fatal(err)
	}

	got, err := idx.ChunksFor(7)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("after shrinking the note to one section, %d rows remain: %+v", len(got), got)
	}
	for _, c := range got {
		if strings.Contains(c.Content, "# B") || strings.Contains(c.Content, "# C") {
			t.Errorf("a removed section survived: %q", c.Content)
		}
	}
}

func TestReplaceIsIdempotent(t *testing.T) {
	idx := openScratch(t)
	chunks := BuildChunks("T", "# A\n\nAlpha.\n", 2048)
	for i := 0; i < 3; i++ {
		if err := idx.ReplaceChunks(9, chunks); err != nil {
			t.Fatalf("run %d: %v", i, err)
		}
	}
	got, err := idx.ChunksFor(9)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != len(chunks) {
		t.Errorf("after three writes there are %d rows, want %d", len(got), len(chunks))
	}
}

func TestEmptyBodyWritesNoRows(t *testing.T) {
	idx := openScratch(t)
	if err := idx.ReplaceChunks(11, BuildChunks("T", "", 2048)); err != nil {
		t.Fatal(err)
	}
	got, err := idx.ChunksFor(11)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Errorf("an empty body wrote %d rows", len(got))
	}
}

// A budget of zero is what a caller that only wants sections passes.
func TestZeroBudgetDisablesTheSecondLevel(t *testing.T) {
	long := strings.Repeat("word ", 5000)
	got := BuildChunks("T", "# Long\n\n"+long+"\n", 0)
	if len(got) != 1 {
		t.Fatalf("a zero budget produced %d chunks; the second level did not turn off", len(got))
	}
}
