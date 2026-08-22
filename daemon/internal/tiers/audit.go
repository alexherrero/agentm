package tiers

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// The audit that earns a tier, and the file that remembers it.
//
// The shape follows the sampled audit this codebase already runs over applied
// mutations: a capped sample in deterministic order, a verdict per item, a rate,
// and a history sidecar under `_meta/`. What differs is the question. That audit
// asks whether an applied change was correct; this one asks whether two tiers
// agree on the same input, which is a comparison rather than a judgment.
//
// # Why the record is committed rather than cached
//
// The coverage ledger and the source registry live in the index database
// because losing them costs a re-scan. Losing this costs a re-audit, and a
// re-audit is 2N model calls per job at full price — there is no batch tier to
// soften it here. So it lives in the vault, in git, beside the notes it decides
// how to spend money on.
//
// Losing it is still safe, which is the point of the fallback direction: every
// job falls back to strong, and the only damage is the bill.

// TableName is the committed file's name under the meta directory.
const TableName = "model-tiers.json"

// TablePath is where the file lives for a given meta directory.
func TablePath(metaDir string) string { return filepath.Join(metaDir, TableName) }

// Load reads the tier table. A missing file is an empty table rather than an
// error: a vault that has never run an audit has none, and every job falls back
// to strong, which is exactly right.
func Load(metaDir string) (*Table, error) {
	blob, err := os.ReadFile(TablePath(metaDir))
	if os.IsNotExist(err) {
		return &Table{}, nil
	}
	if err != nil {
		return nil, err
	}
	var t Table
	if err := json.Unmarshal(blob, &t); err != nil {
		// Refused rather than treated as empty. An empty table routes everything
		// strong and would look like a deliberate decision; a corrupt one is a
		// file somebody needs to look at, and the difference should not be
		// silently the same.
		return nil, fmt.Errorf("tiers: %s will not parse. It is refused rather "+
			"than read as an empty table, because an empty table is a decision "+
			"and a broken file is a problem: %w", TablePath(metaDir), err)
	}
	return &t, nil
}

// Save writes the tier table whole and atomically.
func (t *Table) Save(metaDir string, now time.Time) error {
	t.WrittenBy = "agentmd"
	t.WrittenAt = now.UTC().Truncate(time.Second)
	t.Note = TableNote
	t.sort()

	blob, err := json.MarshalIndent(t, "", "  ")
	if err != nil {
		return err
	}
	blob = append(blob, '\n')

	if err := os.MkdirAll(metaDir, 0o755); err != nil {
		return err
	}
	path := TablePath(metaDir)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, blob, 0o644); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp)
		return err
	}
	return nil
}

// Sample is one real input both tiers are asked about.
type Sample struct {
	// Ref identifies the input, for the report. A rate with no way back to what
	// it was measured over is a number nobody can check.
	Ref string
	// Prompt is what both tiers are asked. The same text to both, because the
	// comparison is between the models and not between two framings of the
	// question.
	Prompt string
}

// Ask runs one tier over one prompt and returns its answer.
//
// Supplied rather than owned so this package does not depend on the model layer,
// and so a test can state what the two tiers said without a subprocess. The same
// seam the coverage ledger uses for its lookup.
type Ask func(ctx context.Context, model, prompt string) (string, error)

// Agrees decides whether two answers count as the same answer.
//
// Supplied because agreement means different things per job. A classification
// agrees when the labels match; a summary agrees when it says the same thing,
// which is a judgment rather than a comparison. Handing this in keeps the
// package from pretending one rule covers both.
type Agrees func(cheap, strong string) bool

// AuditReport is what one qualification run measured.
type AuditReport struct {
	Job         Job    `json:"job"`
	CheapModel  string `json:"cheap_model"`
	StrongModel string `json:"strong_model"`
	PassVersion string `json:"pass_version"`

	Sampled int `json:"sampled"`
	Agreed  int `json:"agreed"`
	// Failed counts samples where a tier errored. They are excluded from the
	// rate rather than counted as disagreement: a model that could not be
	// reached has not disagreed with anything, and counting it as a
	// disagreement would let an outage disqualify a tier that was fine.
	Failed int     `json:"failed"`
	Rate   float64 `json:"rate"`

	// Qualified is the verdict, and it is the only thing that should be read as
	// one. A rate above the bar over too small a sample is not a qualification.
	Qualified bool   `json:"qualified"`
	Why       string `json:"why"`

	// Disagreements names the samples the two tiers answered differently on, so
	// the number can be checked rather than believed.
	Disagreements []string      `json:"disagreements,omitempty"`
	Calls         int           `json:"calls"`
	Elapsed       time.Duration `json:"elapsed"`
}

// maxNamedDisagreements bounds the list a report carries, on the same reasoning
// as the enrichment batch's error cap: a run where everything disagreed should
// say so once rather than once per sample.
const maxNamedDisagreements = 20

// Audit runs one job's cheap tier against its strong tier over a sample.
//
// Two calls per sample, both at full price — there is no batch tier here, which
// is exactly why this is a deliberate, operator-initiated measurement rather
// than something a nightly cycle does on its own.
func Audit(ctx context.Context, job Job, cheapModel, strongModel, passVersion string,
	samples []Sample, ask Ask, agrees Agrees, now time.Time) (AuditReport, Qualification, error) {
	started := time.Now()
	rep := AuditReport{
		Job: job, CheapModel: cheapModel, StrongModel: strongModel,
		PassVersion: passVersion,
	}

	if !Known(job) {
		return rep, Qualification{}, fmt.Errorf(
			"tiers: %q is not a token-bearing job", job)
	}
	if yes, why := Pinned(job); yes {
		return rep, Qualification{}, fmt.Errorf(
			"tiers: %s is pinned to the strong tier without audit (%s); auditing "+
				"it would spend money measuring a route nothing takes", job, why)
	}
	if ask == nil || agrees == nil {
		return rep, Qualification{}, fmt.Errorf(
			"tiers: an audit needs both a way to ask each tier and a rule for what " +
				"counts as agreement")
	}
	if cheapModel == "" || strongModel == "" || cheapModel == strongModel {
		return rep, Qualification{}, fmt.Errorf(
			"tiers: an audit compares two different models, got %q and %q",
			cheapModel, strongModel)
	}

	for _, s := range samples {
		if ctx.Err() != nil {
			break
		}
		cheap, cerr := ask(ctx, cheapModel, s.Prompt)
		rep.Calls++
		if cerr != nil {
			rep.Failed++
			continue
		}
		strong, serr := ask(ctx, strongModel, s.Prompt)
		rep.Calls++
		if serr != nil {
			rep.Failed++
			continue
		}
		rep.Sampled++
		if agrees(cheap, strong) {
			rep.Agreed++
			continue
		}
		if len(rep.Disagreements) < maxNamedDisagreements {
			rep.Disagreements = append(rep.Disagreements, s.Ref)
		}
	}

	if rep.Sampled > 0 {
		rep.Rate = float64(rep.Agreed) / float64(rep.Sampled)
	}
	rep.Elapsed = time.Since(started)

	q := Qualification{
		Job: job, Tier: Cheap, CheapModel: cheapModel, StrongModel: strongModel,
		PassVersion: passVersion, Sampled: rep.Sampled, Agreed: rep.Agreed,
		Rate: rep.Rate, MinAgreement: MinAgreement, MinSamples: MinSamples,
		QualifiedAt: now.UTC().Truncate(time.Second),
	}
	rep.Qualified = q.Meets()

	switch {
	case rep.Sampled < MinSamples:
		rep.Why = fmt.Sprintf("%d usable samples, under the %d-sample floor — a "+
			"rate over too few inputs is not a measurement, whatever it says",
			rep.Sampled, MinSamples)
	case !rep.Qualified:
		rep.Why = fmt.Sprintf("%.1f%% agreement over %d samples, under the %.1f%% "+
			"bar; %s stays on the strong tier",
			rep.Rate*100, rep.Sampled, MinAgreement*100, job)
	default:
		rep.Why = fmt.Sprintf("%.1f%% agreement over %d samples, at or above the "+
			"%.1f%% bar", rep.Rate*100, rep.Sampled, MinAgreement*100)
	}
	if rep.Failed > 0 {
		rep.Why += fmt.Sprintf(" (%d sample(s) excluded: a tier could not be "+
			"reached, which is not a disagreement)", rep.Failed)
	}

	if !rep.Qualified {
		// No qualification is returned for a run that missed the bar. Storing one
		// would put a record in the table that Route has to know to ignore, and
		// the table should only contain rows that mean "this tier may serve".
		return rep, Qualification{}, nil
	}
	return rep, q, nil
}
