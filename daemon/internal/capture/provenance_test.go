package capture

import (
	"strings"
	"testing"
)

// Task 7 of this part is mostly already built, and the honest thing is to verify
// that rather than rebuild it. `capture.Do` already defaults `status` to
// `unfiled`, already accepts `active`, already rejects anything else, and already
// stamps `captured` from the transaction's own clock. What was missing was any
// check that those properties hold — so this file is the check.
//
// The two statuses carry the design's provenance distinction. A capture the
// operator directed lands `active`, because a session he asked for produces
// memories he already approved by asking, and routing those through triage would
// page him about a backlog that is not one. Everything unattended lands
// `unfiled`, which is rank-penalized but fully indexed and searchable — there is
// no inbox, and rank-penalized is a very different condition from absent.

func TestAmbientCaptureLandsUnfiled(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{Title: "ambient", Text: "Something noticed in passing."})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}
	if res.Status != "unfiled" {
		t.Errorf("status %q; anything unattended lands unfiled", res.Status)
	}
}

func TestADirectedCaptureLandsActive(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Title:  "directed",
		Text:   "Something the operator asked to remember.",
		Status: "active",
	})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}
	if res.Status != "active" {
		t.Errorf("status %q; a directed capture lands active", res.Status)
	}
}

// The default is the safe one. An ambient path that forgot to set a status must
// land in the reviewable state, not the approved one — the failure of getting
// this backwards is silent and only shows up as a corpus of unreviewed material
// claiming to be reviewed.
func TestTheDefaultIsTheReviewableState(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{Title: "no status", Text: "Body."})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status == "active" {
		t.Error("an unspecified status defaulted to active; the safe default is unfiled")
	}
}

func TestAnUnknownStatusIsRefused(t *testing.T) {
	cp := newHarness(t)
	_, err := cp.Do(Request{Title: "x", Text: "Body.", Status: "inbox"})
	if err == nil {
		t.Fatal("a status outside the pair was accepted")
	}
	if !strings.Contains(err.Error(), "active") || !strings.Contains(err.Error(), "unfiled") {
		t.Errorf("the refusal does not name the two that work: %v", err)
	}
}

func TestStatusIsCaseInsensitive(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{Title: "x", Text: "Body.", Status: "ACTIVE"})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}
	if res.Status != "active" {
		t.Errorf("status %q", res.Status)
	}
}

// `captured` is immutable and fixes the shard. Nothing may rewrite it — a note
// that moved shards because a later pass re-stamped it would be a note whose
// address changed, which is the one thing the whole layout is built to prevent.
func TestCapturedIsWrittenOnceAndNotRewritten(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{Title: "stamped", Text: "Body."})
	if err != nil {
		t.Fatal(err)
	}

	first := frontmatterLine(t, cp.cfg.VaultPath, res.Path, "captured:")
	if first == "" {
		t.Fatal("no captured line was written")
	}

	// Re-index the same note several times, which is what a reconcile pass does.
	for i := 0; i < 3; i++ {
		if err := cp.idx.IndexFile(res.Path); err != nil {
			t.Fatalf("reindex %d: %v", i, err)
		}
	}

	again := frontmatterLine(t, cp.cfg.VaultPath, res.Path, "captured:")
	if again != first {
		t.Errorf("captured changed under reindexing:\n was %s\n now %s", first, again)
	}
}

// The captured stamp determines the shard, so the path a note is written to has
// to agree with the stamp it carries. A disagreement would mean a note filed
// under a date it does not claim.
func TestTheShardAgreesWithTheStamp(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{Title: "sharded", Text: "Body."})
	if err != nil {
		t.Fatal(err)
	}
	line := frontmatterLine(t, cp.cfg.VaultPath, res.Path, "captured:")
	stamp := strings.TrimSpace(strings.TrimPrefix(line, "captured:"))
	if len(stamp) < 7 {
		t.Fatalf("captured stamp is too short to carry a year and month: %q", stamp)
	}
	year, month := stamp[0:4], stamp[5:7]
	if !strings.Contains(res.Path, year+"/"+month) {
		t.Errorf("note written to %q but stamped %q; the shard and the stamp disagree",
			res.Path, stamp)
	}
}
