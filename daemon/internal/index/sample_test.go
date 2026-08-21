package index

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

func seedQueue(t *testing.T, idx *Index, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		rel := "Agent/memory/semantic/n" + string(rune('a'+i/26)) + string(rune('a'+i%26)) + ".md"
		raw := "---\ntitle: A note\nstatus: unfiled\n---\n\nThe staging gate runs.\n"
		if err := idx.Upsert(note.Parse(rel, raw, time.Now()),
			time.Now().UnixNano(), int64(len(raw))); err != nil {
			t.Fatal(err)
		}
	}
}

// A sample nobody can reproduce cannot be compared against a later one, and
// comparing two batches is the only way the pre-registered thresholds ever get
// replaced by measured ones.
func TestTheSameSeedDrawsTheSameNotes(t *testing.T) {
	idx := openScratch(t)
	seedQueue(t, idx, 60)

	a, err := idx.UnfiledSample(context.Background(), 10, 42)
	if err != nil {
		t.Fatal(err)
	}
	b, err := idx.UnfiledSample(context.Background(), 10, 42)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(a, ",") != strings.Join(b, ",") {
		t.Errorf("the same seed drew different notes:\n  %v\n  %v", a, b)
	}
	c, err := idx.UnfiledSample(context.Background(), 10, 43)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(a, ",") == strings.Join(c, ",") {
		t.Error("two different seeds drew the identical sample, so the seed does " +
			"nothing")
	}
}

// The whole point of sampling: it must not be the front of the queue. That front
// is overwhelmingly `_inbox/` mining stubs, and a batch taken from there
// measures the pass against the least representative notes the corpus has.
func TestASampleIsNotTheFrontOfTheQueue(t *testing.T) {
	idx := openScratch(t)
	seedQueue(t, idx, 60)

	page, err := idx.UnfiledPage(context.Background(), "", 10)
	if err != nil {
		t.Fatal(err)
	}
	sample, err := idx.UnfiledSample(context.Background(), 10, 7)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(page, ",") == strings.Join(sample, ",") {
		t.Error("the sample is exactly the first page, so it is not a sample")
	}
	// It is still drawn from the queue, though — every path has to be real.
	inQueue := map[string]bool{}
	all, err := idx.UnfiledPage(context.Background(), "", 1000)
	if err != nil {
		t.Fatal(err)
	}
	for _, p := range all {
		inQueue[p] = true
	}
	for _, p := range sample {
		if !inQueue[p] {
			t.Errorf("%s is not in the unfiled queue", p)
		}
	}
}

// Only unfiled notes, or the sample would rewrite things somebody already
// judged.
func TestASampleOnlyDrawsUnfiledNotes(t *testing.T) {
	idx := openScratch(t)
	seedQueue(t, idx, 20)
	active := "---\ntitle: Settled\nstatus: active\n---\n\nAlready judged.\n"
	if err := idx.Upsert(note.Parse("Agent/memory/semantic/settled.md", active, time.Now()),
		time.Now().UnixNano(), int64(len(active))); err != nil {
		t.Fatal(err)
	}

	got, err := idx.UnfiledSample(context.Background(), 50, 1)
	if err != nil {
		t.Fatal(err)
	}
	for _, p := range got {
		if strings.HasSuffix(p, "settled.md") {
			t.Error("an active note was drawn into the sample")
		}
	}
	if len(got) != 20 {
		t.Errorf("drew %d of the 20 unfiled notes", len(got))
	}
}

// Asking for more than exists returns everything rather than erroring or
// repeating a note to pad the count.
func TestAskingForMoreThanExistsReturnsAllOfThem(t *testing.T) {
	idx := openScratch(t)
	seedQueue(t, idx, 5)

	got, err := idx.UnfiledSample(context.Background(), 30, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 5 {
		t.Fatalf("got %d notes from a queue of 5", len(got))
	}
	seen := map[string]bool{}
	for _, p := range got {
		if seen[p] {
			t.Errorf("%s appears twice", p)
		}
		seen[p] = true
	}
}

// Sorted, because the batch runner serves the sample through the same
// cursor-ordered path the live queue uses. An unsorted sample would have the
// cursor skip most of it.
func TestASampleComesBackSorted(t *testing.T) {
	idx := openScratch(t)
	seedQueue(t, idx, 60)

	got, err := idx.UnfiledSample(context.Background(), 15, 99)
	if err != nil {
		t.Fatal(err)
	}
	for i := 1; i < len(got); i++ {
		if got[i-1] >= got[i] {
			t.Fatalf("the sample is not sorted at %d: %q then %q — the cursor "+
				"would skip everything after the first out-of-order path",
				i, got[i-1], got[i])
		}
	}
}

func TestAZeroSampleDrawsNothing(t *testing.T) {
	idx := openScratch(t)
	seedQueue(t, idx, 10)
	got, err := idx.UnfiledSample(context.Background(), 0, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Errorf("a zero sample drew %d notes", len(got))
	}
}
