package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/meters"
	"github.com/alexherrero/agentm/daemon/internal/note"
)

// `agentmd clusters` — where the corpus is converging, rather than how much.
//
// The seam for the correction loop. Go finds the clusters and classifies them
// from provenance, because that is a corpus walk over the index and a fact read
// from frontmatter; Python decides what to do about each one, because the
// actions are staging a proposal, calling the enrichment pass, and writing a
// file for a person to read.
//
// Detection reports. It never merges, rewrites or deletes — this command is
// read-only against the vault, and the two arms that mutate live on the other
// side of the seam behind the revert log.

// DefaultClusterThreshold is where two notes stop being two memories.
//
// 0.95, measured before it was chosen. On the 494 filed live memories in the
// memory space the pairwise distribution is median 0.4324, p90 0.6008, max
// 0.9557 — so 0.95 sits at the extreme tail and selects two pairs, four notes,
// 0.8% of the window. Both are notes distilled from distinct upstream projects
// into near-identical prose, which is the case the correction loop exists for.
//
// Intuition would have said 0.90. On the population the meters were reading
// before their scope was corrected, 0.90 swept 451 of 500 notes into a cluster.
const DefaultClusterThreshold = 0.95

// defaultClusterSample is how many recent filed memories to look at, matching
// the meters' own window: the clusters are supposed to explain the meters'
// numbers, and two different windows would make them describe different corpora.
const defaultClusterSample = defaultMeterSample

type clusterReport struct {
	// Sample and Embedded first, for the reason the meter report gives: a
	// finding about four notes is a finding about four notes.
	Sample   int    `json:"sample"`
	Embedded int    `json:"embedded"`
	Model    string `json:"model,omitempty"`
	Scope    string `json:"scope,omitempty"`
	// Threshold is echoed because it decides everything below it, and a report
	// that does not say which line it drew cannot be compared with another.
	Threshold float64 `json:"threshold"`
	From      string  `json:"from,omitempty"`
	To        string  `json:"to,omitempty"`

	Clusters []meters.Cluster `json:"clusters"`
	// Counts breaks the clusters down by kind, so a caller sees at a glance how
	// much of what was found is actionable and how much is not.
	Counts map[meters.ClusterKind]int `json:"counts"`

	// Unavailable names what could not be measured. Present rather than implied,
	// because "no clusters" and "could not look" are opposite findings.
	Unavailable []string `json:"unavailable,omitempty"`
}

func cmdClusters(args []string) error {
	fs := newFlagSet("clusters")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the clusters as JSON")
	sample := fs.Int("sample", defaultClusterSample,
		"how many recent filed memories to consider")
	threshold := fs.Float64("threshold", DefaultClusterThreshold,
		"cosine similarity at which two notes stop being two memories")
	ef := bindEmbedderFlags(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd clusters "+
			"[--threshold F] [--sample N] [--json]", extra[0])
	}
	if *threshold <= 0 || *threshold > 1 {
		return fmt.Errorf("--threshold %v is not a cosine similarity; it must be "+
			"greater than 0 and at most 1", *threshold)
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

	rep, err := runClusters(context.Background(), cfg, idx, *sample, *threshold,
		embedModelFor(cfg, ef))
	if err != nil {
		return err
	}
	if *asJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(rep)
	}
	printClusters(rep)
	return nil
}

func runClusters(ctx context.Context, cfg *config.Config, idx *index.Index,
	sample int, threshold float64, model string) (clusterReport, error) {
	// The meters' population and the meters' scope. A cluster report drawn from a
	// different window than the meter it explains would answer a different
	// question with the same words.
	scope := meterScope(cfg)
	rows, err := idx.RecentForMeters(ctx, sample, model, scope, true)
	if err != nil {
		return clusterReport{}, err
	}

	rep := clusterReport{
		Sample: len(rows), Model: model, Threshold: threshold,
		Scope:  joinScope(scope),
		Counts: map[meters.ClusterKind]int{},
	}
	if len(rows) > 0 {
		rep.From, rep.To = rows[0].Captured, rows[len(rows)-1].Captured
	}

	notes := make([]meters.Note, 0, len(rows))
	unreadable := 0
	for _, r := range rows {
		raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(r.Rel)))
		if err != nil {
			unreadable++
			continue
		}
		// Parsed from disk rather than read off the index, because the index
		// stores neither `source` nor `derived_from` and because the file is the
		// truthful copy — a drifted index is a rebuild, and a cluster report drawn
		// from a stale row would name notes whose text has moved on.
		n := note.Parse(r.Rel, string(raw), time.Time{})
		prov := make([]string, 0, 1+len(n.DerivedFrom))
		if n.Source != "" {
			prov = append(prov, n.Source)
		}
		prov = append(prov, n.DerivedFrom...)
		notes = append(notes, meters.Note{Rel: r.Rel, Vec: r.Vec, Provenance: prov})
		if r.Vec != nil {
			rep.Embedded++
		}
	}
	if unreadable > 0 {
		rep.Unavailable = append(rep.Unavailable, fmt.Sprintf(
			"%d of %d notes are in the index and not on disk; reconcile fixes it, "+
				"and until then this report is about the rest", unreadable, len(rows)))
	}

	found, err := meters.Clusters(notes, threshold)
	if err != nil {
		// Refused, not empty. The dense arm is what this reads, and reporting
		// "no clusters" for "no vectors" would say the corpus is clean.
		rep.Unavailable = append(rep.Unavailable, err.Error())
		return rep, nil
	}
	rep.Clusters = found
	for _, c := range found {
		rep.Counts[c.Kind]++
	}
	return rep, nil
}

func printClusters(rep clusterReport) {
	fmt.Printf("clusters over %d filed memories (%d embedded) in %s at %.2f\n",
		rep.Sample, rep.Embedded, rep.Scope, rep.Threshold)
	if rep.From != "" {
		fmt.Printf("window %s to %s\n", rep.From, rep.To)
	}
	for _, u := range rep.Unavailable {
		fmt.Printf("  unavailable: %s\n", u)
	}
	if len(rep.Clusters) == 0 && len(rep.Unavailable) == 0 {
		fmt.Println("  none — no two notes are this close")
		return
	}
	for _, c := range rep.Clusters {
		chain := ""
		if c.Chained {
			chain = fmt.Sprintf(", chained (loosest pair %.4f)", c.MinSim)
		}
		fmt.Printf("\n  %s — %d notes, tightest %.4f%s\n", c.Kind, len(c.Members),
			c.MaxSim, chain)
		fmt.Printf("    %s\n", c.Why)
		for _, m := range c.Members {
			if p := c.Provenance[m]; len(p) > 0 {
				fmt.Printf("      %s  <- %v\n", m, p)
			} else {
				fmt.Printf("      %s  <- no provenance recorded\n", m)
			}
		}
	}
}

// joinScope renders the scope for the report header.
func joinScope(scope []string) string {
	out := ""
	for i, s := range scope {
		if i > 0 {
			out += ", "
		}
		out += s
	}
	return out
}
