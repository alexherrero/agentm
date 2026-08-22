package ledger

import (
	"context"
	"fmt"
	"sort"
	"time"
)

// "What is pending?" as a query.
//
// The caller supplies the eligible population, because what counts as eligible
// is a stage's business and not the ledger's — enrichment's population is the
// unfiled queue filtered by the filing contract, a rollup's is the set of entity
// URIs with enough mentions, and neither is knowable from a table of what has
// already been done. The ledger's job is to say which of those the stage is not
// current on, and why.

// Target is one member of a stage's eligible population, with the key its
// current content hashes to.
type Target struct {
	// Rel identifies the target — a vault-relative path for a note, an entity
	// URI for a rollup.
	Rel string
	// Key is what the target's current content and the stage's current version
	// hash to. The caller computes it, because only the stage knows what its own
	// input is.
	Key string
}

// Reason says why a target is pending.
//
// The design names three — never attempted, fingerprint changed, version stale —
// and this adds two more for the states those three do not cover: a target the
// stage tried and failed on, and one a gate declined. They are separated rather
// than folded into "never" because the digest has to distinguish "we have not
// got to it" from "we got to it and it did not work", and because a queue that
// quietly omitted its failures would be the same silent partiality the cursor
// rule exists to prevent.
type Reason string

const (
	// ReasonNever means the stage has no row for this target at all.
	ReasonNever Reason = "never"
	// ReasonChanged means the stage finished this target, at this version, over
	// content that is no longer what the target holds.
	ReasonChanged Reason = "changed"
	// ReasonStale means the row sits at an older stage version — a prompt, a
	// code change, or a filing-contract edit has moved on without it.
	//
	// The contract counts, and it counts as *version* rather than as content.
	// The design defines a stage's version as its prompt version and the
	// `rules_hash` it judged under, together, and a memory is re-enrichment
	// eligible exactly when that hash differs. Reporting a contract edit as
	// `changed` would say forty notes were edited when one rules file was.
	ReasonStale Reason = "stale"
	// ReasonRetry means the last attempt failed.
	ReasonRetry Reason = "retry"
	// ReasonSkipped means a gate declined the target last time. It is pending
	// because the caller put it in the eligible population, which is the
	// caller's assertion that it should be reachable now.
	ReasonSkipped Reason = "skipped"
)

// Item is one pending target and why.
type Item struct {
	Target string `json:"target"`
	Reason Reason `json:"reason"`
	// Since is when the existing row was written. Zero for ReasonNever, where
	// there is no row and therefore no honest answer.
	Since time.Time `json:"since,omitempty"`
	// Version is the version the existing row sits at, which is what makes a
	// stale item legible: it says what it is behind.
	Version string `json:"version,omitempty"`
	// Detail carries the recorded reason for a failure or a skip.
	Detail string `json:"detail,omitempty"`
}

// Report is one stage's standing, and the numbers the coverage meter reads.
type Report struct {
	Stage   Stage  `json:"stage"`
	Version string `json:"version"`
	// RulesHash is the filing contract the report was taken against, so a
	// coverage number can be read back against the contract that produced it.
	RulesHash string `json:"rules_hash,omitempty"`
	// Eligible is how many targets the caller offered.
	Eligible int `json:"eligible"`
	// Current is how many of those the stage is finished with.
	Current int `json:"current"`
	// Pending is the rest, ordered the way an owner should drain them.
	Pending []Item `json:"pending"`
	// Counts breaks the pending set down by reason, so a run can report "forty
	// went stale because the rules changed" rather than "forty pending".
	Counts map[Reason]int `json:"counts"`
}

// Coverage is the share of the eligible population the stage is current on.
//
// One when there is nothing to do. An empty population with no work outstanding
// is complete coverage, and reporting it as zero would put a red number on the
// dashboard for a stage that has nothing to be behind on.
func (r Report) Coverage() float64 {
	if r.Eligible == 0 {
		return 1
	}
	return float64(r.Current) / float64(r.Eligible)
}

// OldestPending is the age of the oldest item that has a timestamp, which is the
// number the queue thresholds are set on — fifty fresh items on a Tuesday is a
// Tuesday, and an item three days old means the drain has stalled.
//
// Items with no timestamp are excluded rather than treated as infinitely old.
// A never-attempted target has no age; giving it one would make the age
// threshold fire on a brand-new population that nothing has had a chance to
// touch yet, which is the exact false alarm that makes a threshold get ignored.
func (r Report) OldestPending(now time.Time) time.Duration {
	var oldest time.Time
	for _, it := range r.Pending {
		if it.Since.IsZero() {
			continue
		}
		if oldest.IsZero() || it.Since.Before(oldest) {
			oldest = it.Since
		}
	}
	if oldest.IsZero() {
		return 0
	}
	return now.Sub(oldest)
}

// Version is what a stage is running as: its own version and the filing contract
// it judges under, together.
//
// One type rather than two arguments because they are one thing. The design
// says so — "the stage's version — its prompt version and the `rules_hash` it
// judged under" — and splitting them at the call site is how the comparison came
// to use only half of it.
type Version struct {
	Stage string
	Rules string
}

// Matches reports whether a row was written under this version.
func (v Version) Matches(rowStage, rowRules string) bool {
	if v.Stage != "" && v.Stage != rowStage {
		return false
	}
	// An empty rules hash means "do not judge on the contract", which is what a
	// stage with no contract dependency needs. It must not silently mark every
	// row stale.
	if v.Rules != "" && v.Rules != rowRules {
		return false
	}
	return true
}

// Pending reports which of the offered targets the stage is not current on.
//
// The whole stage is read into memory once rather than queried per target. A
// stage's row count is bounded by the corpus — fifteen thousand rows at the
// outside — and one scan beats fifteen thousand round trips by enough that the
// alternative is not worth writing.
func (l *Ledger) Pending(ctx context.Context, stage Stage, version Version,
	targets []Target) (Report, error) {
	rows, err := l.db.QueryContext(ctx, `
		SELECT stage, target, version, rules_hash, input_key, output_key,
		       outcome, reason, at
		FROM ledger WHERE stage = ?`, stage)
	if err != nil {
		return Report{}, fmt.Errorf("ledger: reading %s: %w", stage, err)
	}
	defer rows.Close()

	known := make(map[string]Entry)
	for rows.Next() {
		e, err := scanEntry(rows)
		if err != nil {
			return Report{}, err
		}
		known[e.Target] = e
	}
	if err := rows.Err(); err != nil {
		return Report{}, err
	}

	rep := Report{
		Stage: stage, Version: version.Stage, RulesHash: version.Rules,
		Eligible: len(targets), Counts: map[Reason]int{},
	}
	for _, t := range targets {
		e, ok := known[t.Rel]
		if !ok {
			rep.append(Item{Target: t.Rel, Reason: ReasonNever})
			continue
		}
		// Version first, and the contract is part of it. A stale row's key
		// comparison is meaningless — the key folds both in, so a row written
		// under an older prompt or an older contract can never match a current
		// key, and reporting either as "changed" blames the note for an edit
		// somebody made to the machinery.
		if !version.Matches(e.Version, e.RulesHash) {
			rep.append(Item{Target: t.Rel, Reason: ReasonStale,
				Since: e.At, Version: e.Version, Detail: staleBecause(version, e)})
			continue
		}
		switch e.Outcome {
		case Failed:
			rep.append(Item{Target: t.Rel, Reason: ReasonRetry,
				Since: e.At, Version: e.Version, Detail: e.Reason})
			continue
		case Skipped:
			rep.append(Item{Target: t.Rel, Reason: ReasonSkipped,
				Since: e.At, Version: e.Version, Detail: e.Reason})
			continue
		}
		if t.Key != "" && (t.Key == e.InputKey || t.Key == e.OutputKey) {
			rep.Current++
			continue
		}
		rep.append(Item{Target: t.Rel, Reason: ReasonChanged,
			Since: e.At, Version: e.Version})
	}

	sortPending(rep.Pending)
	return rep, nil
}

// staleBecause names which half of the version moved, because "stale" alone
// leaves the reader guessing between a prompt revision and a contract edit —
// and those call for very different reactions.
func staleBecause(want Version, e Entry) string {
	switch {
	case want.Stage != "" && want.Stage != e.Version && want.Rules != "" && want.Rules != e.RulesHash:
		return "both the pass and the filing contract have moved on"
	case want.Stage != "" && want.Stage != e.Version:
		return "the pass has moved on from " + e.Version
	case want.Rules != "" && want.Rules != e.RulesHash:
		return "the filing contract has moved on from " + shortHash(e.RulesHash)
	}
	return ""
}

func shortHash(h string) string {
	if len(h) <= 12 {
		return h
	}
	return h[:12]
}

func (r *Report) append(it Item) {
	r.Pending = append(r.Pending, it)
	r.Counts[it.Reason]++
}

// sortPending puts the queue in drain order: oldest first, then by target.
//
// Never-attempted items lead, and they do so without a rule of their own. An
// item with no row has no stamp, a zero time sorts before every real one, and
// that is exactly the ordering wanted — a target nothing has ever touched has
// been waiting since the corpus existed. An explicit never-first clause stood
// here for a while and was removed: no input could reach it, because no
// never-attempted item can carry a timestamp, so nothing could tell whether it
// was doing anything.
//
// The final tiebreak on target name is not cosmetic. A drain reads this order
// behind a cursor, and an order that is not total would let two runs disagree
// about where the cursor pointed.
func sortPending(items []Item) {
	sort.SliceStable(items, func(i, j int) bool {
		a, b := items[i], items[j]
		if !a.Since.Equal(b.Since) {
			return a.Since.Before(b.Since)
		}
		return a.Target < b.Target
	})
}
