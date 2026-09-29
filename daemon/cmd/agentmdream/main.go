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
	"path/filepath"
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
  agentmdream ideas     print Ideas.md as the night would rebuild it; -write to make the one deliberate write
  agentmdream move-tasks  plan the closed-task moves; -apply to make them now, whatever the switch says
  agentmdream apply     check a one-time pass's manifest of rewrites; -apply to make them through the journal
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
	case "ideas":
		err = cmdIdeas(os.Args[2:])
	case "move-tasks":
		err = cmdMoveTasks(os.Args[2:])
	case "apply":
		err = cmdApply(os.Args[2:])
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
	// 0 means "the contract's `demotion_cap`". The flag used to default to this
	// package's own 200, which quietly outranked the number the operator set.
	cap := fs.Int("cap", 0, "at most this many moves along the axis per pass (0: the contract's `demotion_cap`)")
	reclassify := fs.Bool("reclassify", false, "run the sampled re-classification diff this pass even if the filing pass version is unchanged")
	taskCap := fs.Int("task-cap", 0, "at most this many closed task folders moved per pass (0: the contract's `demotion_cap`, counted in folders)")
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
	rep, err := dreaming.Run(cfg, dreaming.Options{Apply: *apply, Force: *force, Every: *every, Pace: *pace, Cap: *cap, Reclassify: *reclassify, TaskCap: *taskCap})
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

// cmdApply makes a one-time pass's manifest (task 178): the rewrites a Python
// pass planned — a trace folded, a chunk note or an idea copy superseded —
// journaled like the night's own acts. It checks and prints by default and
// writes nothing; `-apply` makes every act or, when any note changed since the
// plan, none of them.
func cmdApply(args []string) error {
	fs := newFlagSet("apply")
	opts := bindCommon(fs)
	manifest := fs.String("manifest", "", "the manifest file a pass wrote (required)")
	apply := fs.Bool("apply", false, "make the rewrites (default: check and print, touch nothing)")
	asJSON := fs.Bool("json", false, "emit the result as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 || *manifest == "" {
		return fmt.Errorf("usage: agentmdream apply -manifest FILE [-apply] [-json]")
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	if err := refuseAStrangeVaultOnLiveState(*opts, cfg); err != nil {
		return err
	}
	m, err := dreaming.LoadManifest(*manifest)
	if err != nil {
		return err
	}
	res, err := dreaming.ApplyManifest(cfg, m, dreaming.ApplyManifestOptions{Apply: *apply})
	if *asJSON {
		blob, _ := json.MarshalIndent(res, "", "  ")
		fmt.Println(string(blob))
	} else {
		fmt.Printf("%s (%s): %d act(s), %d ready\n", res.Job, res.Mode, res.Acts, res.Ready)
		for _, r := range res.Changed {
			fmt.Println("  changed since the plan:", r)
		}
		for _, r := range res.Missing {
			fmt.Println("  gone:", r)
		}
		for _, r := range res.Refused {
			fmt.Println("  refused:", r)
		}
		if res.Pending > 0 {
			fmt.Printf("  %d intent(s) a crashed pass left are pending; an applying run settles them first\n", res.Pending)
		}
		if res.Mode == "apply" && res.RunID != "" {
			fmt.Printf("  run %s: %d applied, %d skipped\n", res.RunID, res.Applied, res.Skipped)
		}
	}
	if errors.Is(err, dreaming.ErrRefused) {
		return &exitError{code: 3, err: fmt.Errorf("another pass holds the dreaming lock; nothing was written")}
	}
	if errors.Is(err, dreaming.ErrManifestNotReady) {
		return &exitError{code: 4, err: err}
	}
	return err
}

// cmdMoveTasks runs the closed-task mover on its own (task 177): the
// supervised first move of the backlog, and the rehearsal against a copy of the
// vault. It plans and prints by default. `-apply` moves, journaled and under
// the dreaming lock, whether or not `daemon.task_mover_enabled` is on — running
// it by hand is the decision the switch otherwise stands for.
func cmdMoveTasks(args []string) error {
	fs := newFlagSet("move-tasks")
	opts := bindCommon(fs)
	apply := fs.Bool("apply", false, "make the moves (default: plan and print, touch nothing)")
	cap := fs.Int("cap", 0, "at most this many folders (0: the contract's `demotion_cap`, counted in folders)")
	asJSON := fs.Bool("json", false, "emit the plan as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmdream move-tasks [-apply] [-cap N] [-json]", extra[0])
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	if err := refuseAStrangeVaultOnLiveState(*opts, cfg); err != nil {
		return err
	}
	plan, rep, err := dreaming.MoveTasks(cfg, dreaming.MoveTasksOptions{Apply: *apply, Cap: *cap})
	if *asJSON {
		blob, _ := json.MarshalIndent(struct {
			Plan    dreaming.TaskMovePlan `json:"tasks"`
			RunID   string                `json:"run_id,omitempty"`
			Applied int                   `json:"applied"`
			Skipped int                   `json:"skipped"`
			Resumed int                   `json:"resumed"`
		}{plan, rep.RunID, rep.Applied, rep.Skipped, rep.Resumed}, "", "  ")
		fmt.Println(string(blob))
	} else {
		verb := "would move"
		if plan.Mode == "apply" {
			verb = "moved"
		}
		fmt.Printf("closed tasks: %s %d folder(s) (%d link(s) rewritten in %d note(s) outside them); "+
			"held %d; waiting on the cap %d; after %.0f days\n", verb, len(plan.Folders), plan.Links,
			len(plan.Edited), len(plan.Held), plan.Capped, plan.AfterDays)
		for _, f := range plan.Folders {
			fmt.Printf("  %s %s -> %s (%s %s, %d file(s))\n", verb, f.From, f.To, f.Status, f.Closed, f.Files)
		}
		for _, h := range plan.Held {
			fmt.Printf("  held %s: %s %v\n", h.From, h.Reason, h.Notes)
		}
		for _, h := range plan.Stopped {
			fmt.Printf("  stopped %s: %s\n", h.From, h.Reason)
		}
		if plan.Pending > 0 {
			fmt.Printf("  %d intent(s) a crashed pass left are pending; the next applying run settles them first\n", plan.Pending)
		}
		for _, e := range plan.Errors {
			fmt.Println("  error:", e)
		}
		if plan.Mode == "apply" {
			fmt.Printf("  run %s: %d applied, %d skipped, %d folder(s) pruned, sidecars re-keyed %v\n",
				rep.RunID, rep.Applied, rep.Skipped, len(plan.Pruned), plan.Rekeyed)
		}
		if plan.Skipped != "" {
			fmt.Println("  skipped:", plan.Skipped)
		}
	}
	if errors.Is(err, dreaming.ErrRefused) {
		return &exitError{code: 3, quiet: *asJSON, err: fmt.Errorf("refused: %s", rep.Refused)}
	}
	return err
}

// refuseAStrangeVaultOnLiveState stops a rehearsal against a copy of the vault
// from writing the live engine state. `-vault` or `$MEMORY_ROOT` moves the
// vault and nothing else: the journal, the sidecars and the lifecycle record
// stay the live ones, so a copy's moves would be journaled where the live night
// replays them and its re-keys would land in the live sidecars. A vault other
// than the configured one is refused unless `$AGENTM_STATE_DIR` names state of
// its own.
func refuseAStrangeVaultOnLiveState(opts config.Options, cfg *config.Config) error {
	if strings.TrimSpace(os.Getenv("AGENTM_STATE_DIR")) != "" {
		return nil
	}
	path := opts.ConfigPath
	if path == "" {
		path = config.DefaultConfigPath()
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var kernel map[string]any
	if json.Unmarshal(raw, &kernel) != nil {
		return nil
	}
	configured, _ := kernel["plugins.obsidian-vault.vault_path"].(string)
	if strings.TrimSpace(configured) == "" {
		return nil
	}
	if strings.HasPrefix(configured, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			configured = filepath.Join(home, configured[2:])
		}
	}
	real := func(p string) string {
		if r, err := filepath.EvalSymlinks(p); err == nil {
			return r
		}
		return filepath.Clean(p)
	}
	if real(configured) == real(cfg.VaultPath) {
		return nil
	}
	return fmt.Errorf("the vault is %s, not the configured %s, but the engine state is the live "+
		"one; set AGENTM_STATE_DIR to a scratch directory so a rehearsal writes its own journal "+
		"and sidecars", cfg.VaultPath, configured)
}

// cmdIdeas is `Ideas.md`'s dry run and its one deliberate write
// (agentm-vault part 13).
//
// With no flags it prints the file exactly as the night would write it, under
// the file's own head, and writes nothing. `-intro <file>` is the adoption: the
// head is built from that introduction, so the operator reads the whole first
// rendering before anything replaces their hand-kept file. `-write` makes the
// write, journaled and under the dreaming lock, and only then; the nightly job
// keeps it current afterwards.
func cmdIdeas(args []string) error {
	fs := newFlagSet("ideas")
	opts := bindCommon(fs)
	introPath := fs.String("intro", "", "the approved introduction, a file whose text goes between the markers (the adoption)")
	write := fs.Bool("write", false, "make the write (default: print the rendering and touch nothing)")
	asJSON := fs.Bool("json", false, "emit the plan as JSON rather than the rendering")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmdream ideas [-intro FILE] [-write] [-json]", extra[0])
	}
	intro := ""
	if *introPath != "" {
		raw, err := os.ReadFile(*introPath)
		if err != nil {
			return err
		}
		if intro = strings.TrimSpace(string(raw)); intro == "" {
			return fmt.Errorf("%s is empty; the introduction is the one text the file keeps as it is", *introPath)
		}
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	plan, err := dreaming.Ideas(cfg, dreaming.IdeasOptions{Intro: intro, Write: *write})
	if *asJSON {
		blob, _ := json.MarshalIndent(plan, "", "  ")
		fmt.Println(string(blob))
	} else if plan.NotWritten != "" {
		fmt.Printf("ideas: %d card(s); nothing to render — %s\n", plan.Cards, plan.NotWritten)
	} else {
		fmt.Print(plan.Text)
		verb := "would change"
		if *write {
			verb = "written"
		}
		if !plan.Changed {
			verb = "unchanged"
		}
		fmt.Fprintf(os.Stderr, "ideas: %d card(s) in %d group(s) — Ideas.md %s\n", plan.Cards, plan.Groups, verb)
	}
	if err != nil {
		return err
	}
	if plan.NotWritten != "" {
		return &exitError{code: 4, quiet: true, err: errors.New(plan.NotWritten)}
	}
	return nil
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
	fmt.Printf("%s pass %s (%s): %s — %s %d, revived %d, archived %d, deleted %d, returned %d, touched by hand %d, previews %d, held by cap %d, considered %d",
		rep.Mode, rep.RunID, rep.Outcome, rep.Decision.Reason, verb, len(p.Demoted), len(p.Revived),
		len(p.Archived), len(p.Deleted), len(p.Returned), len(p.Touched), len(p.Previews), p.Capped, p.Considered)
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
	for _, m := range p.Archived {
		fmt.Printf("  archived %s — silent %.0f days, moved to %s\n",
			m.Rel, m.Days, dreaming.ArchiveDestination(m.Rel))
	}
	for _, m := range p.Deleted {
		fmt.Printf("  deleted %s — silent %.0f days\n", m.Rel, m.Days)
	}
	if rep.DeletionManifest != "" {
		fmt.Printf("  the deletions are recorded in %s, written before the files went\n",
			rep.DeletionManifest)
	}
	for _, m := range p.Returned {
		fmt.Printf("  returned %s — moved back into its class by hand\n", m.Rel)
	}
	for _, m := range p.Touched {
		fmt.Printf("  left alone %s — its `lifecycle` was edited by hand since this pass last moved it\n", m.Rel)
	}
	// What is coming, so a threshold can be argued with before it fires.
	if len(p.SinkingSoon) > 0 {
		fmt.Printf("  sinking within %.0f days: %d\n", dreaming.ForwardDays, len(p.SinkingSoon))
	}
	if len(p.ArchivingSoon) > 0 {
		fmt.Printf("  archiving within %.0f days: %d\n", dreaming.ForwardDays, len(p.ArchivingSoon))
	}
	would := "would "
	if rep.Mode == "apply" {
		would = ""
	}
	fmt.Printf("copies: %d famil%s (%d deferred) · refile: %d move(s), %d unflag(s), %d blocked · promote: %d new, %d existing\n",
		len(rep.Copies.Families), map[bool]string{true: "y", false: "ies"}[len(rep.Copies.Families) == 1], rep.Copies.Deferred,
		len(rep.Refile.Moves), len(rep.Refile.Unflags), len(rep.Refile.Blocked), len(rep.Promote.Promotions), len(rep.Promote.Existing))
	if rep.Calendar.Skipped != "" {
		fmt.Printf("calendar: skipped — %s\n", rep.Calendar.Skipped)
	} else {
		fmt.Printf("calendar: %d review(s) checked, %s%d written (%s)\n", rep.Calendar.Refreshed, would, len(rep.Calendar.Written),
			map[bool]string{true: "none", false: strings.Join(rep.Calendar.Written, ", ")}[len(rep.Calendar.Written) == 0])
	}
	changed := 0
	for _, pg := range rep.Mocs.Pages {
		if pg.Changed {
			changed++
		}
	}
	fmt.Printf("mocs: %d page(s), %s%d regenerated, %d type(s) below the floor, %s%d page(s) removed · dates: %s%d gloss(es) across %d aging note(s)\n",
		len(rep.Mocs.Pages), would, changed, len(rep.Mocs.BelowFloor), would, len(rep.Mocs.Removed), would, len(rep.Dates.Glossed), rep.Dates.Aging)
	switch {
	case rep.Ideas.NotWritten != "":
		fmt.Printf("ideas: %d card(s), not written — %s\n", rep.Ideas.Cards, rep.Ideas.NotWritten)
	case rep.Ideas.Changed:
		fmt.Printf("ideas: %d card(s) in %d group(s), Ideas.md %srebuilt\n", rep.Ideas.Cards, rep.Ideas.Groups, would)
	default:
		fmt.Printf("ideas: %d card(s) in %d group(s), Ideas.md unchanged\n", rep.Ideas.Cards, rep.Ideas.Groups)
	}
	fmt.Printf("vocabulary: %d note(s) — %d unrecognized, %d malformed, %d retired\n", rep.Vocabulary.Considered,
		len(rep.Vocabulary.Unrecognized), len(rep.Vocabulary.Malformed), len(rep.Vocabulary.Retired))
	for _, f := range rep.Vocabulary.Unrecognized {
		fmt.Printf("  unrecognized %s: %s %s\n", f.Field, f.Value, f.Rel)
	}
	pct := "no previous week"
	if rep.Trends.ChangePct != nil {
		pct = fmt.Sprintf("%+d%% week over week", *rep.Trends.ChangePct)
	}
	fmt.Printf("trends: week %d vs previous %d (%s), peak %d, cap %d", rep.Trends.Week, rep.Trends.PreviousWeek, pct, rep.Trends.Peak, rep.Trends.Cap)
	if len(rep.Trends.Flags) == 0 {
		fmt.Println(" — nothing flagged")
	} else {
		fmt.Println()
		for _, f := range rep.Trends.Flags {
			fmt.Printf("  ⚠ %s\n", f)
		}
	}
	if rep.Reclassify.Ran {
		fmt.Printf("reclassify: %s — sampled %d of %d, %d mismatch(es)\n", rep.Reclassify.Reason, rep.Reclassify.Sampled, rep.Reclassify.Available, len(rep.Reclassify.Mismatches))
		for _, m := range rep.Reclassify.Mismatches {
			fmt.Printf("  %s: type %s sits in %s, routes to %s (%s)\n", m.Rel, m.Type, m.Class, m.Routed, m.Note)
		}
	} else {
		fmt.Printf("reclassify: not run — %s\n", rep.Reclassify.Reason)
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
