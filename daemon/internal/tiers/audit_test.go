package tiers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
)

func samplesN(n int) []Sample {
	out := make([]Sample, n)
	for i := range out {
		out[i] = Sample{
			Ref:    fmt.Sprintf("memory/note-%02d.md", i),
			Prompt: fmt.Sprintf("classify note %d", i),
		}
	}
	return out
}

// asker answers per model, and disagrees on the first `disagree` samples.
func asker(disagree int) (Ask, *int) {
	calls := 0
	seen := 0
	return func(_ context.Context, model, prompt string) (string, error) {
		calls++
		if model == cheapM {
			seen++
			if seen <= disagree {
				return "different", nil
			}
		}
		return "same", nil
	}, &calls
}

func sameAnswer(cheap, strong string) bool { return cheap == strong }

func auditAt() time.Time { return time.Date(2026, 8, 21, 12, 0, 0, 0, time.UTC) }

// A tier that meets the bar is qualified for that job.
func TestATierThatMeetsTheBarQualifies(t *testing.T) {
	ask, calls := asker(2) // 2 of 30 disagree → 93.3%, over the 90% bar
	rep, q, err := Audit(context.Background(), Summarize, cheapM, strongM, version,
		samplesN(30), ask, sameAnswer, auditAt())
	if err != nil {
		t.Fatal(err)
	}
	if !rep.Qualified {
		t.Errorf("a run at %.1f%% over 30 samples did not qualify: %s",
			rep.Rate*100, rep.Why)
	}
	if q.Job != Summarize || q.Tier != Cheap {
		t.Errorf("qualification = %+v", q)
	}
	if q.MinAgreement != MinAgreement || q.MinSamples != MinSamples {
		t.Errorf("the record does not stamp the bar it was measured against: %+v", q)
	}
	// Two calls per sample, both at full price. The number is reported because
	// it is the cost of the measurement and nobody should have to infer it.
	if *calls != 60 || rep.Calls != 60 {
		t.Errorf("calls = %d/%d, want 60 — two per sample", *calls, rep.Calls)
	}
	// And the disagreements are named, so the rate can be checked.
	if len(rep.Disagreements) != 2 {
		t.Errorf("Disagreements = %v, want the two samples that differed",
			rep.Disagreements)
	}
}

// A tier that misses it is not, whatever it costs.
func TestATierUnderTheBarDoesNotQualify(t *testing.T) {
	ask, _ := asker(5) // 5 of 30 disagree → 83.3%
	rep, q, err := Audit(context.Background(), Summarize, cheapM, strongM, version,
		samplesN(30), ask, sameAnswer, auditAt())
	if err != nil {
		t.Fatal(err)
	}
	if rep.Qualified {
		t.Errorf("a run at %.1f%% qualified against a %.1f%% bar",
			rep.Rate*100, MinAgreement*100)
	}
	// No qualification is returned at all. A record in the table that Route has
	// to know to ignore is a record that should not be there.
	if q != (Qualification{}) {
		t.Errorf("a failed audit produced a qualification: %+v", q)
	}
	if !strings.Contains(rep.Why, "stays on the strong tier") {
		t.Errorf("the report does not say what happens now: %s", rep.Why)
	}
}

// The sample floor. A rate over too few inputs is not a measurement, whatever it
// says — without this a tier qualifies on one lucky answer.
func TestASmallSampleCannotQualifyATier(t *testing.T) {
	ask, _ := asker(0) // perfect agreement
	rep, q, err := Audit(context.Background(), Summarize, cheapM, strongM, version,
		samplesN(MinSamples-1), ask, sameAnswer, auditAt())
	if err != nil {
		t.Fatal(err)
	}
	if rep.Rate != 1.0 {
		t.Fatalf("the fixture did not agree perfectly: %.2f", rep.Rate)
	}
	if rep.Qualified {
		t.Error("a perfect run over too few samples qualified a tier")
	}
	if q != (Qualification{}) {
		t.Errorf("a run under the sample floor produced a qualification: %+v", q)
	}
	if !strings.Contains(rep.Why, "not a measurement") {
		t.Errorf("the report does not say why: %s", rep.Why)
	}
}

// A tier that could not be reached has not disagreed with anything. Counting an
// outage as disagreement would let a bad afternoon disqualify a tier that was
// fine, and the re-audit costs money.
func TestAnUnreachableTierIsExcludedRatherThanCountedAgainst(t *testing.T) {
	boom := errors.New("the model could not be reached")
	seen := 0
	ask := func(_ context.Context, model, prompt string) (string, error) {
		if model == cheapM {
			seen++
			if seen <= 5 {
				return "", boom
			}
		}
		return "same", nil
	}

	rep, _, err := Audit(context.Background(), Summarize, cheapM, strongM, version,
		samplesN(35), ask, sameAnswer, auditAt())
	if err != nil {
		t.Fatal(err)
	}
	if rep.Failed != 5 {
		t.Errorf("Failed = %d, want 5", rep.Failed)
	}
	if rep.Sampled != 30 {
		t.Errorf("Sampled = %d, want the 30 that answered", rep.Sampled)
	}
	if rep.Rate != 1.0 {
		t.Errorf("Rate = %.2f; an unreachable tier was counted as disagreement",
			rep.Rate)
	}
	if !rep.Qualified {
		t.Errorf("an outage disqualified a tier that agreed on every sample it "+
			"answered: %s", rep.Why)
	}
	if !strings.Contains(rep.Why, "not a disagreement") {
		t.Errorf("the report does not explain the exclusion: %s", rep.Why)
	}
}

// A pinned job is refused before any money is spent.
func TestAuditingAPinnedJobIsRefused(t *testing.T) {
	ask, calls := asker(0)
	for _, job := range []Job{Crystallize, EntityIdentityMerge, SelfImprovementProposal} {
		if _, _, err := Audit(context.Background(), job, cheapM, strongM, version,
			samplesN(30), ask, sameAnswer, auditAt()); err == nil {
			t.Errorf("auditing %s was accepted", job)
		}
	}
	if *calls != 0 {
		t.Errorf("%d model calls were made auditing pinned jobs", *calls)
	}
}

// An audit compares two different models. Comparing one against itself measures
// nothing and would qualify every tier at a hundred per cent.
func TestAuditRefusesToCompareAModelWithItself(t *testing.T) {
	ask, _ := asker(0)
	for _, tc := range [][2]string{{cheapM, cheapM}, {"", strongM}, {cheapM, ""}} {
		if _, _, err := Audit(context.Background(), Summarize, tc[0], tc[1], version,
			samplesN(30), ask, sameAnswer, auditAt()); err == nil {
			t.Errorf("an audit of %q against %q was accepted", tc[0], tc[1])
		}
	}
}

func TestAuditNeedsBothSeams(t *testing.T) {
	ask, _ := asker(0)
	if _, _, err := Audit(context.Background(), Summarize, cheapM, strongM, version,
		samplesN(30), nil, sameAnswer, auditAt()); err == nil {
		t.Error("an audit with no way to ask a tier was accepted")
	}
	if _, _, err := Audit(context.Background(), Summarize, cheapM, strongM, version,
		samplesN(30), ask, nil, auditAt()); err == nil {
		t.Error("an audit with no rule for agreement was accepted")
	}
}

// A run where everything disagreed says so once rather than once per sample.
func TestTheDisagreementListIsCapped(t *testing.T) {
	ask, _ := asker(100)
	rep, _, err := Audit(context.Background(), Summarize, cheapM, strongM, version,
		samplesN(maxNamedDisagreements+10), ask, sameAnswer, auditAt())
	if err != nil {
		t.Fatal(err)
	}
	if rep.Agreed != 0 {
		t.Fatalf("the fixture agreed on %d samples", rep.Agreed)
	}
	if len(rep.Disagreements) != maxNamedDisagreements {
		t.Errorf("the report names %d disagreements, want the cap of %d",
			len(rep.Disagreements), maxNamedDisagreements)
	}
}

// --- the committed file -----------------------------------------------------

func TestTheTableRoundTripsThroughTheCommittedFile(t *testing.T) {
	dir := t.TempDir()
	tbl := &Table{}
	if err := tbl.Record(qualified(Summarize)); err != nil {
		t.Fatal(err)
	}
	at := auditAt()
	if err := tbl.Save(dir, at); err != nil {
		t.Fatal(err)
	}

	back, err := Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	if back.WrittenBy != "agentmd" {
		t.Errorf("WrittenBy = %q", back.WrittenBy)
	}
	if !back.WrittenAt.Equal(at) {
		t.Errorf("WrittenAt = %s, want %s", back.WrittenAt, at)
	}
	if back.Note == "" {
		t.Error("the file says nothing about what it is to somebody who opens it")
	}
	got := back.Route(Summarize, cheapM, strongM, version)
	if got.Tier != Cheap {
		t.Errorf("a qualification did not survive the round trip: %s", got.Why)
	}
}

// A vault that has never run an audit has no file, and every job falls back to
// strong — which is exactly right, so it must not be an error.
func TestAMissingTableIsAnEmptyTable(t *testing.T) {
	tbl, err := Load(t.TempDir())
	if err != nil {
		t.Fatalf("Load on a fresh vault: %v", err)
	}
	for _, r := range tbl.RouteAll(cheapM, strongM, version) {
		if r.Tier != Strong {
			t.Errorf("%s routed to %s with no table at all", r.Job, r.Tier)
		}
	}
}

// A corrupt one is refused. An empty table is a decision and a broken file is a
// problem, and the two must not be silently the same.
func TestACorruptTableIsRefused(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(TablePath(dir), []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(dir); err == nil {
		t.Error("a corrupt tier table was read as an empty one")
	}
}

// The file is stable across writes. It lands in the vault's history, and one
// that reordered itself every night would put a diff in the log that said
// nothing.
func TestTheTableIsStableAcrossWrites(t *testing.T) {
	dir := t.TempDir()
	tbl := &Table{}
	// Recorded out of order, so a listing that did not sort would echo insertion.
	for _, job := range []Job{Summarize, ClassifyUnfiled, SlopBorderline} {
		if err := tbl.Record(qualified(job)); err != nil {
			t.Fatal(err)
		}
	}
	at := auditAt()

	var first []byte
	for i := 0; i < 5; i++ {
		if err := tbl.Save(dir, at); err != nil {
			t.Fatal(err)
		}
		blob, err := os.ReadFile(TablePath(dir))
		if err != nil {
			t.Fatal(err)
		}
		if i == 0 {
			first = blob
			continue
		}
		if string(blob) != string(first) {
			t.Fatalf("write %d differs:\n%s\n---\n%s", i, first, blob)
		}
	}

	var back Table
	if err := json.Unmarshal(first, &back); err != nil {
		t.Fatal(err)
	}
	var jobs []Job
	for _, q := range back.Qualifications {
		jobs = append(jobs, q.Job)
	}
	want := []Job{ClassifyUnfiled, SlopBorderline, Summarize}
	for i := range want {
		if i >= len(jobs) || jobs[i] != want[i] {
			t.Fatalf("the file lists %v, want %v", jobs, want)
		}
	}
}

// A hand-edited file comes back sorted.
//
// The table's own header note invites somebody to read it, and a file somebody
// reads is a file somebody eventually edits. Building through Record sorts on
// insert, so this is the only path that reaches the sort on the way out — and
// without it the next nightly write would preserve whatever order the edit left.
func TestAHandEditedTableIsSortedOnTheNextWrite(t *testing.T) {
	dir := t.TempDir()
	unsorted := `{
  "written_by": "a human, by hand",
  "qualifications": [
    {"job": "summarize", "tier": "cheap", "cheap_model": "haiku",
     "strong_model": "sonnet", "pass_version": "dream/1",
     "sampled": 40, "agreed": 38, "rate": 0.95,
     "min_agreement": 0.9, "min_samples": 25},
    {"job": "classify-unfiled", "tier": "cheap", "cheap_model": "haiku",
     "strong_model": "sonnet", "pass_version": "dream/1",
     "sampled": 40, "agreed": 38, "rate": 0.95,
     "min_agreement": 0.9, "min_samples": 25}
  ]
}`
	if err := os.WriteFile(TablePath(dir), []byte(unsorted), 0o644); err != nil {
		t.Fatal(err)
	}

	tbl, err := Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	if err := tbl.Save(dir, auditAt()); err != nil {
		t.Fatal(err)
	}

	back, err := Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	var jobs []Job
	for _, q := range back.Qualifications {
		jobs = append(jobs, q.Job)
	}
	want := []Job{ClassifyUnfiled, Summarize}
	for i := range want {
		if i >= len(jobs) || jobs[i] != want[i] {
			t.Fatalf("after a hand edit the file lists %v, want %v", jobs, want)
		}
	}
}

func TestTheTableIsWrittenAtomically(t *testing.T) {
	dir := t.TempDir()
	if err := (&Table{}).Save(dir, auditAt()); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".tmp") {
			t.Errorf("a temporary file was left behind: %s", e.Name())
		}
	}
}
