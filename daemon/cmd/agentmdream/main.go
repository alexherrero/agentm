// agentmdream is the dreaming binary — the second binary beside agentmd.
//
// It runs one pass and exits: under a dual gate (enough time since the last
// pass AND something happened since), behind a lock (a second start is
// refused), with every mutation journaled before it is made and resumed
// from that journal after a crash. Report-only by default; `-apply` makes
// the writes. Filing v2 part 6.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/dreaming"
)

var version = "0.1.0-dev"

const usage = `agentmdream — the agentm dreaming binary (one pass, then exit)

  agentmdream run       decide whether a pass is due, take the lock, resume, plan, report or apply
  agentmdream status    the last pass, the gate's answer now, the lock
  agentmdream journal   the mutation journal, newest last
  agentmdream version

Run any subcommand with -h for its flags.
`

type exitError struct {
	code  int
	quiet bool
	err   error
}

func (e *exitError) Error() string { return e.err.Error() }

func main() {
	if len(os.Args) < 2 {
		fmt.Fprint(os.Stderr, usage)
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "run":
		err = cmdRun(os.Args[2:])
	case "status":
		err = cmdStatus(os.Args[2:])
	case "journal":
		err = cmdJournal(os.Args[2:])
	case "version", "-v", "--version":
		fmt.Println("agentmdream", version)
	case "-h", "--help", "help":
		fmt.Print(usage)
	default:
		fmt.Fprintf(os.Stderr, "agentmdream: unknown subcommand %q\n\n%s", os.Args[1], usage)
		os.Exit(2)
	}
	if err != nil {
		var ee *exitError
		if errors.As(err, &ee) {
			if !ee.quiet {
				fmt.Fprintln(os.Stderr, "agentmdream:", ee.err)
			}
			os.Exit(ee.code)
		}
		fmt.Fprintln(os.Stderr, "agentmdream:", err)
		os.Exit(1)
	}
}

func newFlagSet(name string) *flag.FlagSet {
	fs := flag.NewFlagSet("agentmdream "+name, flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	return fs
}

func bindCommon(fs *flag.FlagSet) *config.Options {
	opts := &config.Options{}
	fs.StringVar(&opts.ConfigPath, "config", "",
		"kernel config to resolve the vault path from (default ~/.claude/.agentm-config.json)")
	fs.StringVar(&opts.VaultPath, "vault", "", "override the resolved vault path")
	fs.StringVar(&opts.IndexPath, "index", "", "override the index file location")
	return opts
}

func cmdRun(args []string) error {
	fs := newFlagSet("run")
	opts := bindCommon(fs)
	apply := fs.Bool("apply", false, "make the writes (default: report-only — decide, print, touch nothing)")
	force := fs.Bool("force", false, "skip the dual gate and run a pass now")
	every := fs.Duration("every", 7*24*time.Hour, "minimum interval between passes")
	pace := fs.Duration("pace", 0, "sleep between mutations (tests)")
	cap := fs.Int("cap", dreaming.DefaultDemotionCap, "at most this many automatic demotions per pass")
	asJSON := fs.Bool("json", false, "emit the report as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmdream run [-apply] [-force] [-every D] [-json]", extra[0])
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	rep, err := dreaming.Run(cfg, dreaming.Options{Apply: *apply, Force: *force, Every: *every, Pace: *pace, Cap: *cap})
	if *asJSON {
		blob, _ := json.MarshalIndent(rep, "", "  ")
		fmt.Println(string(blob))
	} else {
		printReport(rep)
	}
	if errors.Is(err, dreaming.ErrRefused) {
		return &exitError{code: 3, quiet: *asJSON, err: fmt.Errorf("refused: %s", rep.Refused)}
	}
	return err
}

func printReport(rep dreaming.Report) {
	switch rep.Outcome {
	case "refused":
		fmt.Printf("refused — %s\n", rep.Refused)
		return
	case dreaming.OutcomeNotDue:
		if rep.Resumed > 0 {
			fmt.Printf("resumed %d intent(s) from an unfinished pass\n", rep.Resumed)
		}
		fmt.Printf("not due — %s\n", rep.Decision.Reason)
		return
	}
	if rep.Resumed > 0 {
		fmt.Printf("resumed %d intent(s) from an unfinished pass\n", rep.Resumed)
	}
	p := rep.Plan
	verb := "would sink"
	if rep.Mode == "apply" {
		verb = "sank"
	}
	fmt.Printf("%s pass %s (%s): %s — %s %d, revived %d, archive candidates %d, previews %d, held by cap %d, considered %d",
		rep.Mode, rep.RunID, rep.Outcome, rep.Decision.Reason, verb, len(p.Demoted), len(p.Revived), len(p.Candidates), len(p.Previews), p.Capped, p.Considered)
	if rep.Mode == "apply" {
		fmt.Printf("; applied %d, skipped %d", rep.Applied, rep.Skipped)
	}
	fmt.Println()
	for _, m := range p.Demoted {
		fmt.Printf("  %s %s — silent %.0f days\n", verb, m.Rel, m.Days)
	}
	for _, m := range p.Revived {
		fmt.Printf("  revived %s — recalled %.0f days ago\n", m.Rel, m.Days)
	}
	for _, m := range p.Candidates {
		fmt.Printf("  archive candidate %s — %.0f days (the confirm surface's, never this pass's)\n", m.Rel, m.Days)
	}
}

func cmdStatus(args []string) error {
	fs := newFlagSet("status")
	opts := bindCommon(fs)
	every := fs.Duration("every", 7*24*time.Hour, "minimum interval between passes, for the gate preview")
	asJSON := fs.Bool("json", false, "emit JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	st, err := dreaming.LoadState(cfg.EngineStateDir)
	if err != nil {
		return err
	}
	lockDir := dreaming.SingletonLockDir(cfg.EngineStateDir)
	held := false
	if info, err := os.Stat(lockDir); err == nil {
		held = time.Since(info.ModTime()) <= 30*time.Second
	}
	out := map[string]any{
		"state":     st,
		"lock":      map[string]any{"dir": lockDir, "held": held},
		"journal":   dreaming.JournalPath(cfg.EngineStateDir),
		"gate_note": "the gate's activity count needs the index; run `agentmdream run` (report-only) to see the full decision",
		"every":     every.String(),
	}
	if *asJSON {
		blob, _ := json.MarshalIndent(out, "", "  ")
		fmt.Println(string(blob))
		return nil
	}
	if st.LastDone.IsZero() {
		fmt.Println("no pass has completed yet")
	} else {
		fmt.Printf("last pass %s finished %s ago (%s); %d run(s) so far\n", st.LastRunID,
			time.Since(st.LastDone).Round(time.Minute), st.LastOutcome, st.Runs)
	}
	fmt.Printf("lock %s: %s\n", lockDir, map[bool]string{true: "held", false: "free"}[held])
	fmt.Printf("journal %s\n", dreaming.JournalPath(cfg.EngineStateDir))
	return nil
}

func cmdJournal(args []string) error {
	fs := newFlagSet("journal")
	opts := bindCommon(fs)
	run := fs.String("run", "", "only this run id")
	tail := fs.Int("tail", 0, "only the last N entries")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	j := dreaming.Journal{Path: dreaming.JournalPath(cfg.EngineStateDir)}
	entries, err := j.Read()
	if err != nil {
		return err
	}
	if *run != "" {
		var kept []dreaming.Entry
		for _, e := range entries {
			if e.RunID == *run {
				kept = append(kept, e)
			}
		}
		entries = kept
	}
	if *tail > 0 && len(entries) > *tail {
		entries = entries[len(entries)-*tail:]
	}
	for _, e := range entries {
		e.After = "" // the content is on disk; the line is the decision
		blob, _ := json.Marshal(e)
		fmt.Println(strings.TrimSpace(string(blob)))
	}
	return nil
}
