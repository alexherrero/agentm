package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/meters"
	"github.com/alexherrero/agentm/daemon/internal/note"
)

// `agentmd slop` — the deterministic signals, and no verdict.
//
// Distinct from `scripts/check-slop.py`, which is the prose gate for `wiki/` and
// is a different thing entirely: that one reads the operator's writing voice,
// this one reads whether a memory says anything.
//
// It prints numbers. The design puts a review band and a narrow auto-expire band
// on top of them, and both stay confirm-gated for a supervised pass — that lives
// with the staging machinery, so a scoring change cannot alter what gets deleted
// without somebody reviewing the band.
//
// # Why there is no threshold flag here yet
//
// Because the number is not known, and the corpus says why. Measured over the 261
// live filed memories after the mining cleanup: template residual runs from 0.700
// to 1.000, so nothing is an unfilled skeleton any more. Novelty finds about ten
// candidates below 0.70, and two of them at 0.50 are `gpt-5-3-instant` against
// `gpt-5-4-thinking` and `deepseek-math-v2` against `deepseek-prover-v2` — pairs
// the rubric already calls *not* slop, because "two notes on one subject from
// different sources are two memories".
//
// So the band sits somewhere under 0.50, and where exactly is a judgement the
// design says to settle with a hand-labelled sample. Guessing it here and calling
// it a default would be the third time in this arc that a threshold got reasoned
// about rather than measured.

// slopReport is one run over the filed corpus.
type slopReport struct {
	// Scanned and Scope first, for the reason every other report in this arc
	// gives: a number about 261 notes is a number about 261 notes.
	Scanned int    `json:"scanned"`
	Scope   string `json:"scope,omitempty"`
	// Excluded says what was left out and why, so a reader can tell a clean
	// corpus from a narrow one.
	Excluded map[string]int `json:"excluded,omitempty"`

	Notes []meters.Signals `json:"notes"`

	// Distribution is the same numbers as a shape, because a list of 261 rows is
	// not something anyone reads and the shape is what a band gets drawn on.
	TemplateResidual distribution `json:"template_residual"`
	Novelty          distribution `json:"novelty"`

	Unavailable []string `json:"unavailable,omitempty"`
}

type distribution struct {
	Min    float64 `json:"min"`
	P10    float64 `json:"p10"`
	Median float64 `json:"median"`
	P90    float64 `json:"p90"`
	Max    float64 `json:"max"`
}

func describeOf(v []float64) distribution {
	if len(v) == 0 {
		return distribution{}
	}
	s := append([]float64(nil), v...)
	sort.Float64s(s)
	at := func(f float64) float64 { return s[int(f*float64(len(s)-1))] }
	return distribution{Min: s[0], P10: at(.10), Median: at(.50), P90: at(.90),
		Max: s[len(s)-1]}
}

// generatedKinds are pages written by a machine from a template, on purpose.
//
// A directory index and a MOC are supposed to look like each other, so scoring
// them for novelty measures the generator rather than the corpus. Measured:
// `semantic/_index.md` against `procedural/_index.md` came out at 0.14 novelty,
// which is true and is not a finding about anybody's memories.
var generatedKinds = map[string]bool{"dir-index": true, "moc": true}

var slopKind = regexp.MustCompile(`(?m)^kind:[ \t]*(\S+)`)

// wholeCorpus is a bound high enough not to be one.
//
// The filed memory space is 261 notes today and the vault has never exceeded
// 16,000 in total. A number rather than an "all" sentinel because the seam takes
// a limit, and a limit that silently means something else is how `n = 0` came to
// mean "score nothing".
const wholeCorpus = 1_000_000

func cmdSlop(args []string) error {
	fs := newFlagSet("slop")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the signals as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd slop [--json]",
			extra[0])
	}

	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, cfg.DecayEnabled)
	if err != nil {
		return err
	}
	defer idx.Close()

	rep, err := runSlop(context.Background(), cfg, idx)
	if err != nil {
		return err
	}
	if *asJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(rep)
	}
	printSlop(rep)
	return nil
}

func runSlop(ctx context.Context, cfg *config.Config, idx *index.Index) (slopReport, error) {
	// The meters' population, for the meters' reason: this asks whether the
	// memories are saying anything, so the population is the memories. Sharing
	// `meterScope` rather than re-deriving it also means the two reports cannot
	// drift into describing different corpora under the same word.
	scope := meterScope(cfg)
	// The whole filed corpus, not a recent window.
	//
	// This differs from the meters on purpose. They ask whether what is being
	// written *now* is converging, so a recent window is the question. Novelty
	// asks whether a note repeats something already filed — and something filed
	// two years ago is still filed, so a window would make an old duplicate
	// invisible precisely because it is old.
	//
	// `RecentForMeters` reads `n < 1` as "none" rather than "all", which is a
	// reasonable reading of "up to n" and cost one confused run here.
	rows, err := idx.RecentForMeters(ctx, wholeCorpus, cfg.EmbedModel, scope, false)
	if err != nil {
		return slopReport{}, err
	}

	rep := slopReport{Scope: joinScope(scope), Excluded: map[string]int{}}
	notes := make([]meters.Scorable, 0, len(rows))
	for _, r := range rows {
		raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(r.Rel)))
		if err != nil {
			rep.Excluded["not on disk"]++
			continue
		}
		n := note.Parse(r.Rel, string(raw), time.Time{})
		// `kind` read here rather than off `note.Note`, which does not carry it.
		// One caller needs it, and growing a type every package depends on to
		// serve one exclusion is a worse trade than four lines of regex.
		if k := slopKind.FindStringSubmatch(string(raw)); k != nil &&
			generatedKinds[k[1]] {
			rep.Excluded["generated page"]++
			continue
		}
		notes = append(notes, meters.Scorable{Rel: r.Rel, Body: n.Body})
	}

	rep.Scanned = len(notes)
	if len(notes) == 0 {
		rep.Unavailable = append(rep.Unavailable,
			"no filed memories in scope — nothing to score")
		return rep, nil
	}

	rep.Notes = meters.Score(notes)
	res := make([]float64, 0, len(rep.Notes))
	nov := make([]float64, 0, len(rep.Notes))
	for _, s := range rep.Notes {
		res = append(res, s.TemplateResidual)
		nov = append(nov, s.Novelty)
	}
	rep.TemplateResidual = describeOf(res)
	rep.Novelty = describeOf(nov)
	return rep, nil
}

func printSlop(rep slopReport) {
	fmt.Printf("slop signals over %d filed memories in %s\n", rep.Scanned, rep.Scope)
	for k, n := range rep.Excluded {
		fmt.Printf("  excluded: %d %s\n", n, k)
	}
	for _, u := range rep.Unavailable {
		fmt.Printf("  unavailable: %s\n", u)
	}
	if rep.Scanned == 0 {
		return
	}
	fmt.Printf("\n  template residual (low is bad)  min %.3f  p10 %.3f  median %.3f\n",
		rep.TemplateResidual.Min, rep.TemplateResidual.P10, rep.TemplateResidual.Median)
	fmt.Printf("  novelty           (low is bad)  min %.3f  p10 %.3f  median %.3f\n",
		rep.Novelty.Min, rep.Novelty.P10, rep.Novelty.Median)

	by := append([]meters.Signals(nil), rep.Notes...)
	sort.Slice(by, func(a, b int) bool { return by[a].Novelty < by[b].Novelty })
	fmt.Println("\n  least novel:")
	for _, s := range by[:min(12, len(by))] {
		fmt.Printf("    nov %.2f  res %.2f  %5dw  %s\n           ~ %s\n",
			s.Novelty, s.TemplateResidual, s.Words, s.Rel, s.NearestRel)
	}
	fmt.Println("\n  No verdict here. The band is a labelled judgement, and the " +
		"design puts it\n  with the staging machinery rather than in this report.")
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
