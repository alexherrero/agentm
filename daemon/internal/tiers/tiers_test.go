package tiers

import (
	"strings"
	"testing"
	"time"
)

const (
	cheapM  = "haiku"
	strongM = "sonnet"
	version = "dream/1"
)

func qualified(job Job) Qualification {
	return Qualification{
		Job: job, Tier: Cheap, CheapModel: cheapM, StrongModel: strongM,
		PassVersion: version, Sampled: 30, Agreed: 29, Rate: 29.0 / 30.0,
		MinAgreement: MinAgreement, MinSamples: MinSamples,
		QualifiedAt: time.Date(2026, 8, 21, 0, 0, 0, 0, time.UTC),
	}
}

// The verification the plan names: the three pinned jobs never run cheap.
//
// Not "are not currently configured to" — cannot. There is no qualification a
// caller can construct that routes one of these to the budget tier.
func TestThePinnedJobsNeverRunCheap(t *testing.T) {
	for _, job := range []Job{Crystallize, EntityIdentityMerge, SelfImprovementProposal} {
		tbl := &Table{}

		// Even handed a perfect audit, which Record refuses and Route ignores.
		if err := tbl.Record(Qualification{
			Job: job, Tier: Cheap, CheapModel: cheapM, StrongModel: strongM,
			PassVersion: version, Sampled: 1000, Agreed: 1000, Rate: 1.0,
			MinAgreement: MinAgreement, MinSamples: MinSamples,
		}); err == nil {
			t.Errorf("%s accepted a qualification; a pinned job must not have one", job)
		}

		// And forced past Record, straight into the table, it still routes strong.
		tbl.Qualifications = append(tbl.Qualifications, Qualification{
			Job: job, Tier: Cheap, CheapModel: cheapM, StrongModel: strongM,
			PassVersion: version, Sampled: 1000, Agreed: 1000, Rate: 1.0,
			MinAgreement: MinAgreement, MinSamples: MinSamples,
		})
		got := tbl.Route(job, cheapM, strongM, version)
		if got.Tier != Strong {
			t.Errorf("%s routed to %s even with a perfect qualification in the "+
				"table; pinning is not a preference", job, got.Tier)
		}
		if !strings.Contains(got.Why, "pinned") {
			t.Errorf("%s routed strong for the wrong reason: %s", job, got.Why)
		}
	}
}

// The other half: every job the audit qualified runs on the tier it earned.
func TestAQualifiedJobRunsCheap(t *testing.T) {
	tbl := &Table{}
	if err := tbl.Record(qualified(Summarize)); err != nil {
		t.Fatal(err)
	}
	got := tbl.Route(Summarize, cheapM, strongM, version)
	if got.Tier != Cheap {
		t.Errorf("a qualified job routed to %s: %s", got.Tier, got.Why)
	}
	if got.Model != cheapM {
		t.Errorf("Model = %q, want %q", got.Model, cheapM)
	}
	if !strings.Contains(got.Why, "96.7%") || !strings.Contains(got.Why, "30 samples") {
		t.Errorf("the reason does not carry the measurement: %s", got.Why)
	}
}

// Every unknown answers strong. Routing wrongly to strong costs money and shows
// up on the spend line; routing wrongly to cheap produces worse judgments that
// look exactly like good ones.
func TestEveryUnknownRoutesStrong(t *testing.T) {
	base := qualified(Summarize)

	for name, tc := range map[string]struct {
		table               *Table
		cheap, strong, vers string
		wantWhy             string
	}{
		"no qualification at all": {
			&Table{}, cheapM, strongM, version, "no audit has qualified",
		},
		"a different cheap model": {
			tableWith(base), "haiku-next", strongM, version, "nobody ran",
		},
		"a different strong model": {
			tableWith(base), cheapM, "opus", version, "nobody ran",
		},
		"a newer pass version": {
			tableWith(base), cheapM, strongM, "dream/2", "nobody ran",
		},
		"a rate under the bar": {
			tableWith(func() Qualification {
				q := base
				q.Agreed, q.Rate = 20, 20.0/30.0
				return q
			}()), cheapM, strongM, version, "against a bar",
		},
		"too few samples": {
			tableWith(func() Qualification {
				q := base
				q.Sampled, q.Agreed, q.Rate = 5, 5, 1.0
				return q
			}()), cheapM, strongM, version, "against a bar",
		},
	} {
		t.Run(name, func(t *testing.T) {
			got := tc.table.Route(Summarize, tc.cheap, tc.strong, tc.vers)
			if got.Tier != Strong {
				t.Errorf("routed to %s: %s", got.Tier, got.Why)
			}
			if got.Model != tc.strong {
				t.Errorf("Model = %q, want the strong model %q", got.Model, tc.strong)
			}
			if !strings.Contains(got.Why, tc.wantWhy) {
				t.Errorf("reason %q does not explain itself as %q", got.Why, tc.wantWhy)
			}
		})
	}
}

func tableWith(qs ...Qualification) *Table {
	return &Table{Qualifications: qs}
}

// A job nobody named is not a job with a cheap tier. The table governs a closed
// set, and a typo must not route to the budget model.
func TestAnUnknownJobRoutesStrong(t *testing.T) {
	got := (&Table{}).Route("summarise", cheapM, strongM, version)
	if got.Tier != Strong {
		t.Errorf("an unknown job routed to %s", got.Tier)
	}
	if !strings.Contains(got.Why, "not a job") {
		t.Errorf("reason: %s", got.Why)
	}
}

// Every routing decision says why, including the ones that went well. A spend
// line reading "cheap" with no reason is one nobody can check.
func TestEveryRoutingCarriesItsReason(t *testing.T) {
	tbl := tableWith(qualified(Summarize))
	for _, r := range tbl.RouteAll(cheapM, strongM, version) {
		if strings.TrimSpace(r.Why) == "" {
			t.Errorf("%s routed to %s with no reason", r.Job, r.Tier)
		}
		if r.Model == "" {
			t.Errorf("%s routed to %s with no model", r.Job, r.Tier)
		}
	}
}

// A measurement with an expiry date, stated as one.
func TestAQualificationIsCurrentOnlyForWhatItMeasured(t *testing.T) {
	q := qualified(Summarize)
	if !q.Current(cheapM, strongM, version) {
		t.Error("a qualification is not current for its own inputs")
	}
	for _, tc := range [][3]string{
		{"other", strongM, version},
		{cheapM, "other", version},
		{cheapM, strongM, "other"},
	} {
		if q.Current(tc[0], tc[1], tc[2]) {
			t.Errorf("a qualification claims to describe %v", tc)
		}
	}
}

// The bar is stamped on the record rather than read from the constant at
// routing time. A record taken under a looser bar must not silently start
// meeting a stricter one.
func TestAQualificationIsJudgedAgainstTheBarItWasTakenUnder(t *testing.T) {
	loose := qualified(Summarize)
	loose.MinAgreement, loose.MinSamples = 0.50, 5
	loose.Sampled, loose.Agreed, loose.Rate = 10, 6, 0.6

	if !loose.Meets() {
		t.Error("a record does not meet the bar it was taken under")
	}
	// And the current bar would refuse it, which is what makes the stamp
	// load-bearing rather than decorative.
	strict := loose
	strict.MinAgreement, strict.MinSamples = MinAgreement, MinSamples
	if strict.Meets() {
		t.Error("the same numbers meet the current bar; the stamp cannot be " +
			"distinguished from reading the constant")
	}
}

// The pre-registered bar, asserted so a later edit is a visible change rather
// than a quiet one.
func TestTheBarIsWhatWasPreRegistered(t *testing.T) {
	if MinAgreement != 0.90 {
		t.Errorf("MinAgreement = %v, want the pre-registered 0.90", MinAgreement)
	}
	if MinSamples != 25 {
		t.Errorf("MinSamples = %d, want the pre-registered 25", MinSamples)
	}
}

// A sample floor exists so one lucky answer is not a hundred per cent agreement.
func TestOneAgreeingCallDoesNotQualifyATier(t *testing.T) {
	q := Qualification{
		Job: Summarize, Sampled: 1, Agreed: 1, Rate: 1.0,
		MinAgreement: MinAgreement, MinSamples: MinSamples,
	}
	if q.Meets() {
		t.Error("a single agreeing call qualified a tier")
	}
}

// Recording replaces rather than accumulating: the table holds where each job
// stands, not its history.
func TestRecordReplacesAJobsQualification(t *testing.T) {
	tbl := &Table{}
	if err := tbl.Record(qualified(Summarize)); err != nil {
		t.Fatal(err)
	}
	newer := qualified(Summarize)
	newer.PassVersion = "dream/2"
	if err := tbl.Record(newer); err != nil {
		t.Fatal(err)
	}
	if len(tbl.Qualifications) != 1 {
		t.Fatalf("two records for one job: %+v", tbl.Qualifications)
	}
	if tbl.Qualifications[0].PassVersion != "dream/2" {
		t.Errorf("the older record survived: %+v", tbl.Qualifications[0])
	}
}

func TestRecordRefusesAnUnknownJob(t *testing.T) {
	if err := (&Table{}).Record(Qualification{Job: "invented"}); err == nil {
		t.Error("a qualification for an unknown job was accepted")
	}
}

func TestForgetSendsAJobBackToStrong(t *testing.T) {
	tbl := tableWith(qualified(Summarize))
	if !tbl.Forget(Summarize) {
		t.Fatal("Forget reported nothing to drop")
	}
	if got := tbl.Route(Summarize, cheapM, strongM, version); got.Tier != Strong {
		t.Errorf("a forgotten job still routes to %s", got.Tier)
	}
	if tbl.Forget(Summarize) {
		t.Error("Forget reported dropping something twice")
	}
}

// The three pinned jobs are the three the design names, and nothing else.
func TestExactlyThreeJobsArePinned(t *testing.T) {
	var got []Job
	for _, job := range Jobs {
		if yes, why := Pinned(job); yes {
			got = append(got, job)
			if strings.TrimSpace(why) == "" {
				t.Errorf("%s is pinned with no stated reason", job)
			}
		}
	}
	if len(got) != 3 {
		t.Errorf("%d jobs are pinned: %v — the design names three", len(got), got)
	}
	for _, want := range []Job{Crystallize, EntityIdentityMerge, SelfImprovementProposal} {
		if yes, _ := Pinned(want); !yes {
			t.Errorf("%s is not pinned", want)
		}
	}
}
