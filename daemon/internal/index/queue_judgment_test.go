package index

import (
	"context"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// agentm-vault plan 04: the filing queue is the unfiled notes no enrichment has
// judged. A card judged below the floor stays `unfiled` and is listed for the
// operator, but it is not waiting to be filed — counting it would leave the
// queue-age alert red for as long as the operator has not got round to it,
// which is how an alert teaches its reader to ignore it.
func TestAJudgedCardLeavesTheFilingQueueButNotTheReviewQueue(t *testing.T) {
	x := newTestIndex(t)
	old := time.Now().Add(-10 * 24 * time.Hour).UTC()
	fresh := time.Now().Add(-1 * time.Hour).UTC()
	for _, n := range []note.Note{
		// Judged below the floor ten days ago: enrichment wrote its number.
		{Rel: "Agent/memory/semantic/judged.md", Title: "judged", Body: "b",
			Status: "unfiled", Confidence: 0.4, ConfidenceSet: true,
			Captured: old, CapturedSource: "frontmatter"},
		// Captured an hour ago, never judged.
		{Rel: "Agent/memory/semantic/waiting.md", Title: "waiting", Body: "b",
			Status: "unfiled", Captured: fresh, CapturedSource: "frontmatter"},
		// Filed.
		{Rel: "Agent/memory/semantic/filed.md", Title: "filed", Body: "b",
			Status: "active", Confidence: 0.9, ConfidenceSet: true,
			Captured: old, CapturedSource: "frontmatter"},
	} {
		if err := x.Upsert(n, 1, 1); err != nil {
			t.Fatal(err)
		}
	}

	st, err := x.Stats()
	if err != nil {
		t.Fatal(err)
	}
	if st.Unfiled != 1 {
		t.Errorf("the queue counts %d, want the one note awaiting a judgment", st.Unfiled)
	}
	if oldest, ok := st.OldestUnfiledTime(); !ok || oldest.Before(fresh.Add(-time.Minute)) {
		t.Errorf("the queue's oldest is %v; the ten-day-old judged card must not set the age", oldest)
	}
	q, err := x.UnfiledSince(old.Add(-time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if q.Count != 1 || q.Oldest.Before(fresh.Add(-time.Minute)) {
		t.Errorf("since the baseline: %d, oldest %v — want only the waiting note", q.Count, q.Oldest)
	}

	// The operator's review queue still lists the judged card: it is theirs now.
	items, err := x.ReviewQueue(context.Background(), 10)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, it := range items {
		if it.Path == "Agent/memory/semantic/judged.md" {
			found = true
		}
	}
	if !found {
		t.Errorf("the judged card left the review queue too: %+v", items)
	}
}
