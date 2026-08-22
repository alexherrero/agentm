// Package tiers decides which model tier each token-bearing job runs on, and
// refuses to let that be an assumption.
//
// Dreaming is where the token weight lives, so dreaming is where tiering has to
// be disciplined. Three rules, from the design:
//
// Most stages cost nothing. Fingerprint dedup, footer injection, MOC
// regeneration, stub creation, the meters, the ledger and the reconcile scan are
// deterministic and never appear here. Only the token-bearing jobs have a tier
// at all.
//
// The audit assigns the tier, and the assignment is earned. A cheap tier serves
// a job alone only after it has agreed with the strong tier on a sample of real
// inputs, at a bar written down before the run. A tier that misses the bar is
// not qualified, whatever it costs.
//
// Three jobs are pinned strong without audit, because their consequences outlive
// a revert window. There is no configuration that unpins them.
//
// # Which direction this fails in
//
// Every unknown answers Strong. No qualification, a qualification for a
// different model, a qualification for an older pass version, a table that will
// not load — all of them route to the expensive tier. That is deliberate and it
// is the whole safety property: routing wrongly to Strong costs money and is
// visible on the spend line, while routing wrongly to Cheap produces worse
// judgments that look exactly like good ones.
package tiers

import (
	"fmt"
	"sort"
	"time"
)

// Tier is which model class a job runs on.
type Tier string

const (
	// Cheap is the budget tier, reachable only by a job whose audit qualified it.
	Cheap Tier = "cheap"
	// Strong is the default, and the answer to every unknown.
	Strong Tier = "strong"
)

// Job names one token-bearing piece of dreaming's work.
type Job string

// The token-bearing jobs. The design names exactly these: everything else in
// dreaming runs on regex and arithmetic, and the cheapest model call is the one
// never made.
const (
	// ClassifyUnfiled decides what an unfiled note is.
	ClassifyUnfiled Job = "classify-unfiled"
	// Summarize writes a memory's summary.
	Summarize Job = "summarize"
	// FuzzyMerge judges whether two memories are the same memory.
	FuzzyMerge Job = "fuzzy-merge"
	// EntityRollup builds an entity file from the facts that mention it.
	EntityRollup Job = "entity-rollup"
	// SlopBorderline is the slop detector's ambiguous middle band.
	SlopBorderline Job = "slop-borderline"

	// Crystallize distills a lesson from repetition. Pinned: a bad lesson
	// enters the decay-exempt layer, where nothing ages it out.
	Crystallize Job = "crystallize"
	// EntityIdentityMerge decides that two entities are one. Pinned: a wrong
	// merge pollutes a hub that recall hits first.
	EntityIdentityMerge Job = "entity-identity-merge"
	// SelfImprovementProposal proposes a change to dreaming's own machinery.
	// Pinned: it changes the machinery itself.
	SelfImprovementProposal Job = "self-improvement-proposal"
)

// Jobs is every job with a tier, in a stable order.
var Jobs = []Job{
	ClassifyUnfiled, Summarize, FuzzyMerge, EntityRollup, SlopBorderline,
	Crystallize, EntityIdentityMerge, SelfImprovementProposal,
}

// pinned are the three jobs that run strong without audit.
//
// A map rather than a config field, and unexported, because "pinned" is not a
// setting. The design's reason for each is that its consequences outlive a
// revert window — a mistake here is not caught by noticing it the next morning
// — and a switch that could turn that off would eventually be turned off.
var pinned = map[Job]string{
	Crystallize:             "a bad lesson enters the decay-exempt layer, where nothing ages it out",
	EntityIdentityMerge:     "a wrong merge pollutes a hub that recall hits first",
	SelfImprovementProposal: "it changes the machinery itself",
}

// Pinned reports whether a job is pinned to the strong tier, and why.
func Pinned(job Job) (bool, string) {
	why, ok := pinned[job]
	return ok, why
}

// Known reports whether this is a job the tier table governs.
func Known(job Job) bool {
	for _, j := range Jobs {
		if j == job {
			return true
		}
	}
	return false
}

// --- the bar, pre-registered ------------------------------------------------
//
// Written here, before any audit has run, because a bar chosen after seeing the
// numbers is not a bar. Both values are stamped into every qualification, so a
// record produced under a different bar is legible as one rather than silently
// comparable.

const (
	// MinAgreement is the share of a sample on which the cheap tier must match
	// the strong one to serve a job alone.
	//
	// Ninety per cent, against the twenty-per-cent-disagreement alarm line the
	// existing sampled audit already uses for work that has been applied. That
	// line is where somebody gets told to look; this is where a cheap model is
	// allowed to work unsupervised, and it should be the stricter of the two by
	// a clear margin rather than by a hair.
	MinAgreement = 0.90

	// MinSamples is how many real inputs an audit must cover before its rate
	// means anything.
	//
	// Twenty-five, the batch cap every other sampled stage in this codebase
	// uses. The number matters less than the floor existing: without one, a
	// single agreeing call is a hundred per cent agreement, and a tier could
	// qualify on one lucky answer.
	MinSamples = 25
)

// Qualification is one audit's result: a measurement with an expiry date.
type Qualification struct {
	Job Job `json:"job"`
	// Tier is what qualified. Only ever Cheap — Strong needs no audit to serve.
	Tier Tier `json:"tier"`

	// CheapModel and StrongModel are the two models compared. Both are part of
	// the key: the measurement says this cheap model matches this strong one,
	// and swapping either makes it a claim about a comparison nobody ran.
	CheapModel  string `json:"cheap_model"`
	StrongModel string `json:"strong_model"`
	// PassVersion is the prompt-and-code version the sample was judged under.
	PassVersion string `json:"pass_version"`

	Sampled int     `json:"sampled"`
	Agreed  int     `json:"agreed"`
	Rate    float64 `json:"rate"`

	// MinAgreement and MinSamples are the bar this run was measured against,
	// stamped rather than assumed. A record produced under a looser bar must not
	// read as though it met the current one.
	MinAgreement float64   `json:"min_agreement"`
	MinSamples   int       `json:"min_samples"`
	QualifiedAt  time.Time `json:"qualified_at"`
}

// Meets reports whether this measurement clears the bar it was taken against.
func (q Qualification) Meets() bool {
	return q.Sampled >= q.MinSamples && q.Rate >= q.MinAgreement
}

// Current reports whether this qualification still describes the world.
//
// A measurement with an expiry date: change either model or the pass version and
// the record stops being about what would run now. It is not wrong, it is about
// something else.
func (q Qualification) Current(cheapModel, strongModel, passVersion string) bool {
	return q.CheapModel == cheapModel &&
		q.StrongModel == strongModel &&
		q.PassVersion == passVersion
}

// Routing is a decision about one job, with the reason attached.
type Routing struct {
	Job  Job  `json:"job"`
	Tier Tier `json:"tier"`
	// Why says how the tier was arrived at, in words a human reading the spend
	// line needs. A routing decision with no reason is one nobody can audit,
	// and this whole mechanism exists so tiering is not an assumption.
	Why string `json:"why"`
	// Model is the model name the caller should use.
	Model string `json:"model"`
}

// Table is the set of qualifications, keyed by job.
type Table struct {
	// WrittenBy and WrittenAt are the attribution the committed file carries.
	WrittenBy string    `json:"written_by"`
	WrittenAt time.Time `json:"written_at"`
	Note      string    `json:"note"`

	Qualifications []Qualification `json:"qualifications"`
}

// TableNote is for whoever opens the committed file wondering what it is.
const TableNote = "Written by agentmd. Each entry is a measurement, not a " +
	"setting: it says a cheap model agreed with a strong one on a sample of real " +
	"inputs, at the bar stamped beside it. Change either model or the pass " +
	"version and the entry stops describing what would run now, and the job " +
	"falls back to the strong tier until a fresh audit qualifies it again. " +
	"Three jobs are pinned strong and never appear here. Deleting this file " +
	"costs money rather than correctness — everything falls back to strong."

// Route decides which tier a job runs on now.
//
// The reason is always populated, including on the happy path. A spend line that
// says "cheap" without saying why is one nobody can check, and the failure this
// design exists to prevent is a tier assignment nobody measured being mistaken
// for one somebody did.
func (t *Table) Route(job Job, cheapModel, strongModel, passVersion string) Routing {
	strong := Routing{Job: job, Tier: Strong, Model: strongModel}

	if !Known(job) {
		strong.Why = fmt.Sprintf("%q is not a job the tier table governs, so "+
			"nothing has measured a cheap tier for it", job)
		return strong
	}
	if yes, why := Pinned(job); yes {
		strong.Why = "pinned to the strong tier without audit: " + why
		return strong
	}

	q, ok := t.Lookup(job)
	if !ok {
		strong.Why = "no audit has qualified a cheap tier for this job"
		return strong
	}
	if !q.Current(cheapModel, strongModel, passVersion) {
		strong.Why = fmt.Sprintf("the qualification is for %s against %s at %s, "+
			"and this run is %s against %s at %s — a measurement of a comparison "+
			"nobody ran", q.CheapModel, q.StrongModel, q.PassVersion,
			cheapModel, strongModel, passVersion)
		return strong
	}
	if !q.Meets() {
		strong.Why = fmt.Sprintf("the audit measured %.1f%% agreement over %d "+
			"samples, against a bar of %.1f%% over %d",
			q.Rate*100, q.Sampled, q.MinAgreement*100, q.MinSamples)
		return strong
	}

	return Routing{
		Job: job, Tier: Cheap, Model: cheapModel,
		Why: fmt.Sprintf("qualified: %.1f%% agreement over %d samples on %s, "+
			"against a bar of %.1f%% over %d",
			q.Rate*100, q.Sampled, q.QualifiedAt.Format("2006-01-02"),
			q.MinAgreement*100, q.MinSamples),
	}
}

// Lookup returns one job's qualification.
func (t *Table) Lookup(job Job) (Qualification, bool) {
	for _, q := range t.Qualifications {
		if q.Job == job {
			return q, true
		}
	}
	return Qualification{}, false
}

// Record adds or replaces one job's qualification.
//
// A pinned job is refused rather than stored and ignored. A record that sat in
// the table saying a pinned job had qualified would be a true statement about a
// measurement and a misleading one about what runs, and somebody reading the
// file would have to know the pinning rule to tell the difference.
func (t *Table) Record(q Qualification) error {
	if !Known(q.Job) {
		return fmt.Errorf("tiers: %q is not a token-bearing job; the tier table "+
			"governs %d of them and everything else in dreaming is deterministic",
			q.Job, len(Jobs))
	}
	if yes, why := Pinned(q.Job); yes {
		return fmt.Errorf("tiers: %s is pinned to the strong tier without audit "+
			"(%s), so a qualification for it would describe a route nothing takes",
			q.Job, why)
	}
	for i, existing := range t.Qualifications {
		if existing.Job == q.Job {
			t.Qualifications[i] = q
			t.sort()
			return nil
		}
	}
	t.Qualifications = append(t.Qualifications, q)
	t.sort()
	return nil
}

// Forget drops one job's qualification, sending it back to the strong tier.
func (t *Table) Forget(job Job) bool {
	for i, q := range t.Qualifications {
		if q.Job == job {
			t.Qualifications = append(t.Qualifications[:i], t.Qualifications[i+1:]...)
			return true
		}
	}
	return false
}

// sort keeps the committed file stable across writes. It lands in the vault's
// history, and one that reordered itself every night would put a diff in the log
// every night that said nothing.
func (t *Table) sort() {
	sort.Slice(t.Qualifications, func(i, j int) bool {
		return t.Qualifications[i].Job < t.Qualifications[j].Job
	})
}

// RouteAll decides every job, for the report a human reads before believing the
// spend line.
func (t *Table) RouteAll(cheapModel, strongModel, passVersion string) []Routing {
	out := make([]Routing, 0, len(Jobs))
	for _, job := range Jobs {
		out = append(out, t.Route(job, cheapModel, strongModel, passVersion))
	}
	return out
}
