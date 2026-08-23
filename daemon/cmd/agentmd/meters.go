package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/meters"
)

// The four diversity meters, as one command.
//
// Read by dreaming and recorded into the rolling per-stage history the breaker
// already keeps, which is what turns four numbers into a trend. This command's
// whole job is to produce the numbers honestly and say when it cannot; deciding
// whether a number is alarming belongs to whoever holds the history, not here.

// meterReport is what one run measured, and what it could not.
type meterReport struct {
	// Sample is what the numbers are about. Reported first because every number
	// below is meaningless without it — four decimals over eleven notes is a
	// number about eleven notes.
	Sample   int    `json:"sample"`
	Embedded int    `json:"embedded"`
	Model    string `json:"model,omitempty"`
	Scope    string `json:"scope,omitempty"`
	// From and To bound the window. The sample is the most recent notes the
	// dense arm has reached, which on a corpus whose embedder is behind can be
	// weeks old — and a number with no period attached reads as "today".
	From string `json:"from,omitempty"`
	To   string `json:"to,omitempty"`

	TrigramConcentration float64 `json:"trigram_concentration"`
	TrigramTop           int     `json:"trigram_top"`
	LexicalDiversity     float64 `json:"lexical_diversity"`
	LexicalWindow        int     `json:"lexical_window"`

	// The dense pair is absent rather than zero when the arm is not there.
	PairwiseSimilarity *meters.Distribution `json:"pairwise_similarity,omitempty"`
	Dispersion         *meters.Distribution `json:"dispersion,omitempty"`

	// Unavailable names what could not be measured and why. A report that
	// silently omitted the dense pair would be read as a corpus with no
	// convergence rather than as a measurement that did not happen.
	Unavailable []string `json:"unavailable,omitempty"`
}

// defaultMeterSample is how many recent notes the meters look at.
//
// Recent rather than all, because the question is whether what is being written
// *now* is converging; and bounded because pairwise similarity is n(n-1)/2. Five
// hundred is 125,000 pairs, which is milliseconds, and is a large enough window
// to cover more than one nightly cycle's output.
const defaultMeterSample = 500

// defaultTrigramTop is how many of the most common trigrams count toward the
// concentration share. Unmeasured, and deliberately a flag: the right number
// depends on corpus size and nobody has a baseline yet.
const defaultTrigramTop = 50

func cmdMeters(args []string) error {
	fs := newFlagSet("meters")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the numbers as JSON")
	sample := fs.Int("sample", defaultMeterSample,
		"how many recent notes to measure")
	top := fs.Int("trigram-top", defaultTrigramTop,
		"how many of the most common trigrams count toward concentration")
	window := fs.Int("window", meters.DefaultTTRWindow,
		"word window for the lexical-diversity meter (under ~25 it stops seeing "+
			"repetition across notes)")
	ef := bindEmbedderFlags(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd meters "+
			"[--sample N] [--json]", extra[0])
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

	rep, err := runMeters(context.Background(), cfg, idx, *sample, *top, *window,
		embedModelFor(cfg, ef))
	if err != nil {
		return err
	}

	if *asJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(rep)
	}
	printMeters(rep)
	return nil
}

// embedModelFor resolves which model's vectors to read.
//
// The flag first, then the configured one. A meter reading a different model's
// vectors than the corpus was embedded with would find nothing and report an
// absence that is really a mismatch.
func embedModelFor(cfg *config.Config, ef *embedderFlags) string {
	if ef != nil && ef.model != "" {
		return ef.model
	}
	return cfg.EmbedModel
}

func runMeters(ctx context.Context, cfg *config.Config, idx *index.Index,
	sample, top, window int, model string) (meterReport, error) {
	// Embedded notes first, because that is the only window the dense meters can
	// run over. Falling back to everything when there are none keeps the two
	// lexical meters working on a corpus that has never been embedded.
	rows, err := idx.RecentForMeters(ctx, sample, model, cfg.EmbedScope, true)
	if err != nil {
		return meterReport{}, err
	}
	if len(rows) == 0 {
		if rows, err = idx.RecentForMeters(ctx, sample, model, cfg.EmbedScope, false); err != nil {
			return meterReport{}, err
		}
	}

	rep := meterReport{
		Sample: len(rows), Model: model,
		Scope:         strings.Join(cfg.EmbedScope, ", "),
		TrigramTop:    top,
		LexicalWindow: window,
	}

	bodies := make([]string, 0, len(rows))
	vecs := make([][]float32, 0, len(rows))
	unreadable := 0
	for _, r := range rows {
		raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(r.Rel)))
		if err != nil {
			// In the index and not on disk: a drifted index, which reconcile
			// fixes. Counted and reported rather than skipped silently, because
			// a meter quietly measuring four hundred of five hundred notes is a
			// meter describing a corpus nobody named.
			unreadable++
			continue
		}
		bodies = append(bodies, string(raw))
		if r.Vec != nil {
			vecs = append(vecs, r.Vec)
		}
	}
	rep.Embedded = len(vecs)
	if unreadable > 0 {
		rep.Unavailable = append(rep.Unavailable, fmt.Sprintf(
			"%d note(s) are in the index and not on disk; reconcile will fix that",
			unreadable))
	}

	if len(rows) > 0 {
		// The rows come back oldest-first, so the ends of the slice are the
		// window's bounds.
		rep.From, rep.To = rows[0].Captured, rows[len(rows)-1].Captured
	}

	rep.TrigramConcentration = meters.TrigramConcentration(bodies, top)
	rep.LexicalDiversity = meters.MovingAverageTTR(bodies, window)

	if d, err := meters.PairwiseSimilarity(vecs); err == nil {
		rep.PairwiseSimilarity = &d
	} else {
		rep.Unavailable = append(rep.Unavailable, "pairwise similarity: "+err.Error())
	}
	if d, err := meters.NearestNeighbourDispersion(vecs); err == nil {
		rep.Dispersion = &d
	} else {
		rep.Unavailable = append(rep.Unavailable, "dispersion: "+err.Error())
	}
	return rep, nil
}

func printMeters(rep meterReport) {
	fmt.Printf("%d note(s) sampled, %d embedded", rep.Sample, rep.Embedded)
	if rep.Model != "" {
		fmt.Printf(" with %s", rep.Model)
	}
	fmt.Println()
	if rep.Sample == 0 {
		fmt.Println("  nothing to measure yet — no notes in scope")
		return
	}
	if rep.From != "" {
		fmt.Printf("  window %s to %s\n", shortStamp(rep.From), shortStamp(rep.To))
	}

	fmt.Printf("  trigram concentration  %.4f  (top %d; rising means house phrasing)\n",
		rep.TrigramConcentration, rep.TrigramTop)
	fmt.Printf("  lexical diversity      %.4f  (window %d; falling means a narrowing vocabulary)\n",
		rep.LexicalDiversity, rep.LexicalWindow)
	if d := rep.PairwiseSimilarity; d != nil {
		fmt.Printf("  pairwise similarity    %.4f  (median of %d pairs; rising means converging)\n",
			d.Median, d.N)
	}
	if d := rep.Dispersion; d != nil {
		fmt.Printf("  nearest-neighbour      %.4f  (median distance; falling means converging)\n",
			d.Median)
	}
	for _, why := range rep.Unavailable {
		fmt.Printf("  not measured: %s\n", why)
	}
}

// shortStamp trims a capture timestamp to its date, which is the resolution a
// window is read at.
func shortStamp(s string) string {
	if len(s) >= 10 {
		return s[:10]
	}
	return s
}
