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

// sameAnswer is the rule a test states: the answers agree when they are the
// same text, and a disagreement says so.
func sameAnswer(_ context.Context, _ Sample, cheap, strong string) (Verdict, error) {
	if cheap == strong {
		return Verdict{Agree: true, Reason: "the same answer"}, nil
	}
	return Verdict{Agree: false, Reason: "the cheap tier said " + cheap}, nil
}

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
	// Two tier calls per sample, and the judgment counted beside them: three
	// per sample, all at full price. The number is reported because it is the
	// cost of the measurement and nobody should have to infer it.
	if *calls != 60 || rep.Calls != 90 {
		t.Errorf("tier calls = %d, reported calls = %d; want 60 and 90 — two "+
			"tier calls and one judgment per sample", *calls, rep.Calls)
	}
	// And the disagreements are named, each with the rule's reason, so the
	// rate can be checked rather than believed.
	if len(rep.Disagreements) != 2 {
		t.Fatalf("Disagreements = %v, want the two samples that differed",
			rep.Disagreements)
	}
	for _, d := range rep.Disagreements {
		if d.Ref == "" || !strings.Contains(d.Reason, "the cheap tier said") {
			t.Errorf("a disagreement carries no checkable reason: %+v", d)
		}
	}
}

// A judge that could not be reached has found no disagreement. The sample is
// excluded, like one whose tier could not be reached, rather than counted
// either way — and the report says which exclusion it was.
func TestAnUnreachableJudgeIsExcludedRatherThanCountedEitherWay(t *testing.T) {
	ask, _ := asker(0)
	// Four in a row, one under the fuse: this test is about exclusion, and
	// the fuse has its own.
	seen := 0
	judge := func(ctx context.Context, s Sample, cheap, strong string) (Verdict, error) {
		seen++
		if seen <= 4 {
			return Verdict{}, errors.New("the judge could not be reached")
		}
		return sameAnswer(ctx, s, cheap, strong)
	}

	rep, q, err := Audit(context.Background(), Summarize, cheapM, strongM, version,
		samplesN(35), ask, judge, auditAt())
	if err != nil {
		t.Fatal(err)
	}
	if rep.Unjudged != 4 || rep.Failed != 0 {
		t.Errorf("Unjudged = %d, Failed = %d; want 4 unjudged and no tier failures",
			rep.Unjudged, rep.Failed)
	}
	if rep.Sampled != 31 || rep.Rate != 1.0 {
		t.Errorf("Sampled = %d at %.2f; the unjudged samples leaked into the rate",
			rep.Sampled, rep.Rate)
	}
	if !rep.Qualified || q.Job != Summarize {
		t.Errorf("a judge outage disqualified a tier that agreed on every sample "+
			"it was judged on: %s", rep.Why)
	}
	if !strings.Contains(rep.Why, "the judge could not be reached") {
		t.Errorf("the report does not say the judge was the exclusion: %s", rep.Why)
	}
}

// A disagreement the rule gave no reason for still counts against the tier.
// Dropping it would raise the rate, which is the one direction this audit
// must never err in; it is named with a placeholder so a reader sees the gap.
func TestAReasonlessDisagreementStillCounts(t *testing.T) {
	ask, _ := asker(3)
	mute := func(_ context.Context, _ Sample, cheap, strong string) (Verdict, error) {
		return Verdict{Agree: cheap == strong}, nil
	}
	rep, _, err := Audit(context.Background(), Summarize, cheapM, strongM, version,
		samplesN(30), ask, mute, auditAt())
	if err != nil {
		t.Fatal(err)
	}
	if rep.Agreed != 27 || len(rep.Disagreements) != 3 {
		t.Fatalf("agreed %d, named %d; want 27 and 3", rep.Agreed, len(rep.Disagreements))
	}
	for _, d := range rep.Disagreements {
		if d.Reason == "" {
			t.Errorf("a reasonless disagreement was named with no placeholder: %+v", d)
		}
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
			if seen <= 4 { // one under the fuse; the fuse has its own test
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
	if rep.Failed != 4 {
		t.Errorf("Failed = %d, want 4", rep.Failed)
	}
	if rep.Sampled != 31 {
		t.Errorf("Sampled = %d, want the 31 that answered", rep.Sampled)
	}
	// Each excluded sample is named with what failed and why, so "could not
	// be reached" is never the whole story.
	if len(rep.Failures) != 4 {
		t.Fatalf("named %d failure(s), want 4", len(rep.Failures))
	}
	for _, f := range rep.Failures {
		if f.Tier != string(Cheap) || f.Reason != boom.Error() || f.Ref == "" {
			t.Errorf("a failure is not named with its tier and reason: %+v", f)
		}
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

// A lapsed login fails every call the same way. The fuse stops the run after
// five excluded samples in a row, names what failed, and says so in the
// verdict — rather than walking the whole sample producing the same error.
func TestALapsedLoginTripsTheFuse(t *testing.T) {
	lapsed := errors.New("enrich: the call reported an error: Failed to " +
		"authenticate: OAuth session expired and could not be refreshed")
	calls := 0
	ask := func(_ context.Context, model, prompt string) (string, error) {
		calls++
		return "", lapsed
	}
	rep, q, err := Audit(context.Background(), Summarize, cheapM, strongM, version,
		samplesN(35), ask, sameAnswer, auditAt())
	if err != nil {
		t.Fatal(err)
	}
	if calls != MaxFailuresInARow || rep.Calls != MaxFailuresInARow {
		t.Errorf("made %d call(s), reported %d; want the fuse's %d",
			calls, rep.Calls, MaxFailuresInARow)
	}
	if rep.Failed != MaxFailuresInARow || rep.Sampled != 0 {
		t.Errorf("Failed = %d, Sampled = %d", rep.Failed, rep.Sampled)
	}
	if !strings.Contains(rep.StoppedBy, "5 samples in a row") ||
		!strings.Contains(rep.StoppedBy, "OAuth session expired") {
		t.Errorf("StoppedBy does not say what tripped the fuse: %q", rep.StoppedBy)
	}
	if !strings.Contains(rep.Why, "stopped early") {
		t.Errorf("the verdict does not say the run stopped early: %s", rep.Why)
	}
	if len(rep.Failures) != MaxFailuresInARow || rep.Failures[0].Tier != string(Cheap) {
		t.Errorf("the failures are not named: %+v", rep.Failures)
	}
	if rep.Qualified || q != (Qualification{}) {
		t.Error("a run that could score nothing qualified something")
	}

	// A strong tier that fails after the cheap tier answered is named as the
	// strong tier, and the fuse resets on a sample that was scored.
	seen := 0
	flaky := func(_ context.Context, model, prompt string) (string, error) {
		if model == strongM {
			seen++
			if seen%2 == 0 {
				return "", errors.New("strong tier timed out")
			}
		}
		return "same", nil
	}
	rep, _, err = Audit(context.Background(), Summarize, cheapM, strongM, version,
		samplesN(30), flaky, sameAnswer, auditAt())
	if err != nil {
		t.Fatal(err)
	}
	if rep.StoppedBy != "" || rep.Sampled != 15 || rep.Failed != 15 {
		t.Errorf("alternating failures tripped the fuse or miscounted: sampled %d, "+
			"failed %d, stopped %q", rep.Sampled, rep.Failed, rep.StoppedBy)
	}
	if len(rep.Failures) != maxNamedFailures || rep.Failures[0].Tier != string(Strong) {
		t.Errorf("strong-tier failures are not named and capped: %+v", rep.Failures)
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

// No cheap model is refused rather than defaulted, and before anything is
// drawn or spent: which model is on trial is the operator's choice.
func TestCanAuditRefusesWithoutACheapModel(t *testing.T) {
	err := CanAudit(Summarize, "", strongM)
	if err == nil || !strings.Contains(err.Error(), "cheap model") {
		t.Errorf("an audit with no cheap model was not refused for that reason: %v", err)
	}
	if err := CanAudit(Summarize, cheapM, strongM); err != nil {
		t.Errorf("a well-formed audit was refused: %v", err)
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
