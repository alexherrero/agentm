package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// cmdDecay prints the one decay curve: the bands this binary read from the
// filing contract, and what a note scores at a given age.
//
// It exists for two readers. The operator, who can now see the curve their own
// `decay_*` lines describe without reading Go — the design's "the thresholds are
// yours, the mechanics are the agent's" is hard to hold when one half is
// invisible. And the parity test, which is the real reason it prints rather than
// explains: the Python arm runs the same curve from the same lines, and the only
// honest way to prove two implementations agree is to run both. The gate that
// preceded this compared the Go *data* through a Python *reimplementation* of
// the Go rule, and said so in its own comment.
//
// Like `agentmd rules`, it needs no running daemon, no index and no vault.
func cmdDecay(args []string) error {
	fs := newFlagSet("decay")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the bands and the scores as JSON")
	file := fs.String("file", "", "read the contract from this file instead of resolving one")
	days := fs.String("days", "", "comma-separated ages in days to score")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd decay [--days 0,180,365] [--json] [--file PATH]", extra[0])
	}

	var (
		loaded *rules.Rules
		err    error
	)
	if *file != "" {
		loaded, err = rules.LoadFile(*file)
	} else {
		vaultPath := ""
		if cfg, cfgErr := config.Load(*opts); cfgErr == nil {
			vaultPath = cfg.VaultPath
		}
		loaded, err = rules.Load(vaultPath)
	}
	if err != nil {
		return fmt.Errorf("the rules block does not parse, so the curve cannot be read: %w", err)
	}

	// The same five lines, read the same way the daemon reads them at boot.
	full, _ := loaded.Threshold("decay_full_days")
	half, _ := loaded.Threshold("decay_half_days")
	eighth, _ := loaded.Threshold("decay_eighth_days")
	floorDays, _ := loaded.Threshold("decay_floor_days")
	floorWeight, _ := loaded.Threshold("decay_floor_weight")
	note.SetDecayBands(full, half, eighth, floorDays, floorWeight)
	bands := note.DecayBands()

	var scored []scoredDay
	if strings.TrimSpace(*days) != "" {
		for _, raw := range strings.Split(*days, ",") {
			raw = strings.TrimSpace(raw)
			if raw == "" {
				continue
			}
			d, convErr := strconv.ParseFloat(raw, 64)
			if convErr != nil {
				return fmt.Errorf("--days: %q is not a number of days", raw)
			}
			scored = append(scored, scoredDay{Days: d, Score: note.DecayScore(d)})
		}
	}

	if *asJSON {
		out := decayReport{Source: loaded.Source, Bands: bands, Days: scored}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(out)
	}

	fmt.Printf("the curve, from %s\n", loaded.Source)
	for _, b := range bands {
		fmt.Printf("  at or below %6.0f days  x%.4f\n", b.Days, b.Score)
	}
	fmt.Printf("  past the last band     x%.4f\n", bands[len(bands)-1].Score)
	for _, s := range scored {
		fmt.Printf("%8.0f days  x%.4f\n", s.Days, s.Score)
	}
	return nil
}

type scoredDay struct {
	Days  float64 `json:"days"`
	Score float64 `json:"score"`
}

type decayReport struct {
	Source string      `json:"source"`
	Bands  []note.Band `json:"bands"`
	Days   []scoredDay `json:"days,omitempty"`
}
