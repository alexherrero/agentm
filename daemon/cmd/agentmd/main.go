// Command agentmd is the resident memory daemon.
//
// One process. It watches the vault, maintains one FTS5 index, serves
// memory_search and memory_capture over MCP, and is the only thing that runs git.
// Nothing requiring judgment runs inside it.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/capture"
	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/embed"
	"github.com/alexherrero/agentm/daemon/internal/gate"
	"github.com/alexherrero/agentm/daemon/internal/health"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/mcpsrv"
	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/probe"
	"github.com/alexherrero/agentm/daemon/internal/rules"
	"github.com/alexherrero/agentm/daemon/internal/vcs"
	"github.com/alexherrero/agentm/daemon/internal/watch"
)

// version is the daemon's own version, stamped at build time when the release
// script sets it and honest about being a development build when it does not.
var version = "0.1.0-dev"

const usage = `agentmd — the agentm memory daemon

  agentmd serve      run the daemon: watch, index, serve MCP, commit
  agentmd search     one-shot search against the index
  agentmd capture    one-shot capture
  agentmd reindex    rebuild the index from the files
  agentmd embed      compute the vector arm's embeddings for in-scope notes
  agentmd status     ask a running daemon how it is doing
  agentmd probe      run the round-trip self-probe now
  agentmd gate       ask whether a corpus-wide write job may start
  agentmd classify   report rank-penalty class counts over the live vault
  agentmd retire     retire the orphaned pre-daemon memory server
  agentmd rules      print the filing contract, or seed a vault with one

Run any subcommand with -h for its flags.
`

func main() {
	if len(os.Args) < 2 {
		fmt.Fprint(os.Stderr, usage)
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "serve":
		err = cmdServe(os.Args[2:])
	case "search":
		err = cmdSearch(os.Args[2:])
	case "capture":
		err = cmdCapture(os.Args[2:])
	case "reindex":
		err = cmdReindex(os.Args[2:])
	case "embed":
		err = cmdEmbed(os.Args[2:])
	case "status":
		err = cmdStatus(os.Args[2:])
	case "probe":
		err = cmdProbe(os.Args[2:])
	case "gate":
		err = cmdGate(os.Args[2:])
	case "classify":
		err = cmdClassify(os.Args[2:])
	case "retire":
		err = cmdRetire(os.Args[2:])
	case "rules":
		err = cmdRules(os.Args[2:])
	case "version", "-v", "--version":
		fmt.Println("agentmd", version)
	case "help", "-h", "--help":
		fmt.Print(usage)
	default:
		fmt.Fprintf(os.Stderr, "unknown subcommand %q\n\n%s", os.Args[1], usage)
		os.Exit(2)
	}
	if err != nil {
		var exit *exitError
		if errors.As(err, &exit) {
			if exit.quiet {
				os.Exit(exit.code)
			}
			fmt.Fprintln(os.Stderr, "agentmd:", err)
			os.Exit(exit.code)
		}
		fmt.Fprintln(os.Stderr, "agentmd:", err)
		os.Exit(1)
	}
}

// exitError carries a specific exit status. It exists for the gate, where a
// caller has to be able to tell "the gate says no" from "the gate broke" — both
// are non-zero, because a gate that cannot decide must fail closed, but a job
// script deserves to know which one happened.
type exitError struct {
	code  int
	quiet bool
	err   error
}

func (e *exitError) Error() string { return e.err.Error() }
func (e *exitError) Unwrap() error { return e.err }

// ---------------------------------------------------------------------------
// serve
// ---------------------------------------------------------------------------

func cmdServe(args []string) error {
	fs := newFlagSet("serve")
	opts := bindCommon(fs)
	verbose := fs.Bool("verbose", false, "log every indexed file")
	healthEvery := fs.Duration("health-every", 0,
		"how often to evaluate the queue thresholds and run the self-probe if it is due")
	fs.DurationVar(&opts.ProbeEvery, "probe-every", 0,
		"how often the round-trip self-probe runs")
	ef := bindEmbedderFlags(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	markPortSet(fs, opts)

	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	if *healthEvery > 0 {
		cfg.HealthEvery = *healthEvery
	}
	// Uptime is measured from here rather than from the moment the listener
	// binds, because the initial index pass is time the daemon was up and not
	// answering, and a status surface that hid it would hide a slow start.
	bootedAt := time.Now()

	level := slog.LevelInfo
	if *verbose {
		level = slog.LevelDebug
	}
	log := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: level}))

	log.Info("resolved vault", "path", cfg.VaultPath, "source", cfg.VaultSource)

	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath)
	if err != nil {
		return err
	}
	defer idx.Close()

	repo := vcs.Open(cfg.VaultPath)
	// An absence has to outlive a reconcile interval before it counts as a
	// deletion, because the pass that would notice the file back runs on that
	// interval. On a cloud-sync mount the alternative is recording a deletion for
	// a file that was only being replaced at its own path.
	repo.SetDeletionGrace(max(cfg.ReconcileEvery, vcs.DefaultDeletionGrace))
	if repo.Available() {
		log.Info("git", "status", repo.Status())
	} else {
		// Loud, every start. Without git there is no undo for a bad write, and a
		// capability that is quietly missing is the exact failure mode principle 4
		// exists to prevent.
		log.Warn("git DEGRADED — no commits, no undo", "reason", repo.Status())
	}

	// Signals are wired before the embedder so the child process is born under the
	// context that Ctrl-C cancels. A model spawned outside it would outlive the
	// daemon that spawned it, which is the one way a supervised child becomes
	// exactly the independently-managed resident process the design declined.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// A bare hook-issued `agentmd search -mode hybrid` (see cmdSearch) has no
	// way to find this process's embedder once it spawns one — the child binds
	// a kernel-assigned port that only this process knows. Fixing the port
	// this daemon spawns on to a well-known default is what lets that one-shot
	// caller attach instead of loading its own model per query.
	ef.port = embedderSpawnPort(*ef, cfg)

	// The embedder is started but never waited on. A cold 600MB model takes tens
	// of seconds, and a daemon that refused to answer lexical searches until its
	// vector arm warmed up would be strictly worse than the lexical-only daemon
	// this replaces. Hybrid turns itself on when the child reports warm.
	embedder, err := resolveEmbedder(cfg, *ef, log)
	if err != nil {
		return err
	}
	embedder.Start(ctx)
	defer embedder.Close()
	if st, detail := embedder.State(); st == embed.StateOff {
		log.Info("embedder off — hybrid unavailable, lexical-only", "reason", detail)
	} else {
		log.Info("embedder starting", "model", embedder.Model().Name,
			"scope", strings.Join(cfg.EmbedScope, ","))
	}

	cp := capture.New(cfg, idx)
	w := watch.New(cfg, idx, repo, log)

	// Bind before the initial reconcile so a port collision fails in a second
	// rather than after a full vault walk.
	addr := fmt.Sprintf("127.0.0.1:%d", cfg.Port)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		if isAddrInUse(err) {
			return fmt.Errorf(
				"port %d is already in use — if that is the orphaned pre-daemon memory "+
					"server, run `agentmd retire` first; otherwise pass --port: %w",
				cfg.Port, err)
		}
		return err
	}
	defer ln.Close()

	// Index everything before answering anything. A search served against a
	// half-built index is how a memory system convinces its user a fact was never
	// saved. On an existing index this is a stat-and-compare, not a re-read.
	started := time.Now()
	rep, err := w.ReconcileInitial()
	if err != nil {
		return fmt.Errorf("initial index pass: %w", err)
	}
	log.Info("index ready",
		"documents", rep.Scanned, "added", rep.Added, "updated", rep.Updated,
		"removed", rep.Removed, "errors", len(rep.Errors),
		"elapsed", time.Since(started).Round(time.Millisecond))

	baseURL := "http://" + ln.Addr().String()
	prober := probe.New(cfg, idx, baseURL, func(rel string) {
		w.MarkOrigin(rel, vcs.OriginProbe)
	})

	// The daemon's account of itself, evaluated fresh on every read. It is not
	// cached: a status surface reporting a number from ten minutes ago is how a
	// stalled queue looks fine.
	baseline := health.Baseline(cfg.StateDir, cfg.QueueBaseline, time.Now())
	log.Info("queue baseline", "at", baseline.Format(time.RFC3339),
		"meaning", "unfiled items captured before this are the inherited backlog: "+
			"reported, not paged about")

	report := func() health.Report {
		in := health.Input{
			Now:                 time.Now(),
			Uptime:              time.Since(bootedAt),
			GitAvailable:        repo.Available(),
			GitReason:           repo.Status(),
			LastReconcile:       w.LastReconcileAt(),
			LastReconcileErrors: w.LastReconcileErrors(),
			Baseline:            baseline,
			Thresholds:          thresholds(cfg),
		}
		if st, err := idx.Stats(); err == nil {
			in.Unfiled, in.Documents = st.Unfiled, st.Documents
			if oldest, ok := st.OldestUnfiledTime(); ok {
				in.OldestUnfiled = oldest
			}
		}
		if q, err := idx.UnfiledSince(baseline); err == nil {
			in.UnfiledSince, in.OldestUnfiledSince = q.Count, q.Oldest
		}
		if st, ok := prober.Load(); ok {
			in.Probe, in.ProbeAt = probe.AsHealth(st, true)
		}
		in.Embedder = embedderHealth(embedder, idx, cfg)
		return health.Evaluate(in)
	}

	statusFn := func() map[string]any {
		out := map[string]any{
			"vault":        cfg.VaultPath,
			"vault_source": cfg.VaultSource,
			"config":       cfg.ConfigPath,
			"spaces":       cfg.Spaces,
			"shard":        cfg.Shard,
			"uptime":       time.Since(bootedAt).Round(time.Second).String(),
			"health":       report(),
			"watcher":      w.Status(),
		}
		if st, err := idx.Stats(); err == nil {
			out["index_detail"] = st
		} else {
			out["index_detail"] = map[string]any{"error": err.Error()}
		}
		return out
	}

	srv0 := mcpsrv.New(cfg, idx, cp, log, statusFn, w.MarkSelfWritten, version)
	srv0.SetProbe(func() (any, error) { return prober.Run(time.Now()) })
	// The vector arm, behind the same seam. A caller asking for hybrid before the
	// child is warm gets a nil vector and therefore the lexical arm, which is the
	// degradation the mode is specified to have rather than an error.
	srv0.SetEmbedder(func(ctx context.Context, query, question string) ([]float32, string) {
		text := queryEmbedText(query, question, embedder.Model().CtxTokens)
		return embedQuery(ctx, embedder, text), embedder.Model().Name
	})
	srv := &http.Server{
		Handler:           srv0.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		if err := w.Run(ctx); err != nil {
			log.Error("watcher stopped", "err", err)
		}
	}()

	// The address line is the readiness signal: it means indexed, watching, and
	// serving. Tests and supervisors both wait on it.
	fmt.Printf("agentmd %s listening http://%s\n", version, ln.Addr().String())
	_ = os.Stdout.Sync()

	errc := make(chan error, 1)
	go func() {
		if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errc <- err
		}
	}()

	go watchHealth(ctx, cfg, log, prober, report)

	select {
	case err := <-errc:
		return err
	case <-ctx.Done():
		log.Info("shutting down")
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return srv.Shutdown(shutdownCtx)
	}
}

func isAddrInUse(err error) bool {
	return errors.Is(err, syscall.EADDRINUSE) ||
		strings.Contains(err.Error(), "address already in use")
}

// ---------------------------------------------------------------------------
// one-shot commands
// ---------------------------------------------------------------------------

func cmdSearch(args []string) error {
	fs := newFlagSet("search")
	opts := bindCommon(fs)
	k := fs.Int("k", 5, "how many results")
	after := fs.String("after", "", "only notes captured on or after this date")
	before := fs.String("before", "", "only notes captured before this date")
	mode := fs.String("mode", index.ModeAnd,
		"how to combine terms: `and` (every term in one note), `fusion` (best two-term subset), "+
			"`hybrid` (fusion + dense vectors, fused by reciprocal rank), or "+
			"`rerank` (hybrid's fused top-20, cross-encoder reranked and floored)")
	question := fs.String("question", "",
		"natural-language question; when set, `hybrid`/`rerank`'s dense arm embeds this "+
			"instead of the terms query below, truncated to the embedder's window — the "+
			"lexical arms always search the terms below regardless")
	lex3 := fs.Bool("lex3", false,
		"widen `fusion`/`hybrid`'s lexical arm from 2-term to 2- and 3-term subsets "+
			"(task 4, column `+lex3`); false reproduces `lexical-fusion`/`+question` exactly")
	ef := bindEmbedderFlags(fs)
	rf := bindRerankerFlags(fs)
	asJSON := fs.Bool("json", false, "emit JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	query := strings.Join(fs.Args(), " ")
	if strings.TrimSpace(query) == "" {
		return errors.New("usage: agentmd search [flags] <query terms>")
	}

	cfg, idx, err := openReadOnly(opts)
	if err != nil {
		return err
	}
	defer idx.Close()

	// "rerank" is a CLI-level arm, not an index.Query.Mode — see modeRerank's
	// doc comment. It runs the index's own hybrid mode at the rerank depth
	// rather than the caller's requested k, since the cross-encoder needs the
	// fused top-20 to have anything worth reranking; the caller's k is applied
	// after the floor, in rerankFused.
	rerankRequested := *mode == modeRerank
	innerMode, innerK := *mode, *k
	if rerankRequested {
		innerMode, innerK = index.ModeHybrid, rerankDepth
	}

	q := index.Query{Text: query, K: innerK, After: *after, Before: *before, Mode: innerMode, Lex3: *lex3}
	var ctx context.Context
	var cancel context.CancelFunc
	if innerMode == index.ModeHybrid {
		ctx, cancel = context.WithCancel(context.Background())
		defer cancel()
		// A one-shot hybrid search has to have a model in hand before it can ask
		// anything, so unlike `serve` it waits. Attaching to a running server via
		// -embedder-url is what keeps a measurement run from paying one model load
		// per query — and a bare invocation with no explicit pointer (this is
		// what the prompt-submit hook issues) defaults to the resident `agentmd
		// serve` daemon's own embedder (embedderAttachDefault/embedderSpawnPort).
		if u := embedderAttachDefault(*ef, cfg); u != "" {
			ef.url = u
		}
		sup, err := startEmbedder(ctx, cfg, *ef, quietLogger(), 3*time.Minute)
		if err != nil {
			return err
		}
		defer sup.Close()
		q.Vector = embedQuery(ctx, sup, queryEmbedText(query, *question, sup.Model().CtxTokens))
		q.EmbedModel = sup.Model().Name
	}

	out, err := idx.Search(q)
	if err != nil {
		return err
	}

	if rerankRequested {
		// ctx is never nil here: rerankRequested forces innerMode ==
		// ModeHybrid above, which is what set it.
		rsup, err := startReranker(ctx, cfg, *rf, quietLogger(), 3*time.Minute)
		if err != nil {
			return err
		}
		defer rsup.Close()
		out, err = rerankFused(ctx, idx, rsup, query, out, *k)
		if err != nil {
			return err
		}
	}
	if *asJSON {
		return json.NewEncoder(os.Stdout).Encode(out)
	}
	if out.Note != "" {
		fmt.Println("note:", out.Note)
	}
	for i, r := range out.Results {
		fmt.Printf("%d. %s\n", i+1, r.Path)
		fmt.Printf("   score %.4f", r.Score)
		if r.Penalty != "" {
			fmt.Printf("  raw %.4f  penalty %s", r.RawScore, r.Penalty)
		}
		fmt.Printf("  captured %s (%s)\n", r.Captured, r.CapturedSource)
		if r.Snippet != "" {
			fmt.Printf("   %s\n", r.Snippet)
		}
	}
	if len(out.Results) == 0 {
		fmt.Println("(no results)")
	}
	return nil
}

func cmdCapture(args []string) error {
	fs := newFlagSet("capture")
	opts := bindCommon(fs)
	title := fs.String("title", "", "short title")
	// The help text names the types the contract currently defines. Resolved
	// leniently: a flag description is not worth failing a capture over, and the
	// capture itself validates against the same contract a moment later.
	knownTypes := "see `agentmd rules`"
	if r, err := rules.Load(""); err == nil {
		knownTypes = strings.Join(r.TypesSorted(), ", ")
	}
	noteType := fs.String("type", "", "one of: "+knownTypes)
	status := fs.String("status", "", "active | unfiled")
	tags := fs.String("tags", "", "comma-separated tags")
	aliases := fs.String("aliases", "", "comma-separated alternate phrasings")
	source := fs.String("source", "", "URL or message-id")
	space := fs.String("space", "", "which space to write into")
	if err := fs.Parse(args); err != nil {
		return err
	}

	text := strings.Join(fs.Args(), " ")
	if strings.TrimSpace(text) == "" {
		blob, err := io.ReadAll(os.Stdin)
		if err != nil {
			return err
		}
		text = string(blob)
	}

	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath)
	if err != nil {
		return err
	}
	defer idx.Close()

	res, err := capture.New(cfg, idx).Do(capture.Request{
		Text: text, Title: *title, Type: *noteType, Status: *status,
		Tags: splitList(*tags), Aliases: splitList(*aliases),
		Source: *source, Space: *space,
	})
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(res)
}

func cmdReindex(args []string) error {
	fs := newFlagSet("reindex")
	opts := bindCommon(fs)
	scratch := fs.Bool("from-scratch", false,
		"delete the index file first, proving it is rebuildable from the files alone")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	if *scratch {
		for _, suffix := range []string{"", "-wal", "-shm"} {
			if err := os.Remove(cfg.IndexPath + suffix); err != nil && !errors.Is(err, os.ErrNotExist) {
				return err
			}
		}
		fmt.Println("deleted", cfg.IndexPath)
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath)
	if err != nil {
		return err
	}
	defer idx.Close()

	started := time.Now()
	rep, err := idx.Reconcile()
	if err != nil {
		return err
	}
	fmt.Printf("scanned %d, added %d, updated %d, removed %d in %s\n",
		rep.Scanned, rep.Added, rep.Updated, rep.Removed,
		time.Since(started).Round(time.Millisecond))
	for i, e := range rep.Errors {
		if i >= 10 {
			fmt.Printf("… and %d more errors\n", len(rep.Errors)-10)
			break
		}
		fmt.Println("error:", e)
	}
	st, err := idx.Stats()
	if err != nil {
		return err
	}
	blob, _ := json.MarshalIndent(st, "", "  ")
	fmt.Println(string(blob))
	return nil
}

// cmdEmbed computes the vector arm's embeddings for every in-scope note that
// does not have a current one.
//
// It is a separate command rather than part of reindex because the two have
// different costs by two orders of magnitude. Rebuilding the lexical index over
// ten thousand notes is seconds and happens on any schema change; embedding the
// same corpus is minutes of model time, and folding it into reindex would make
// every routine rebuild pay for it.
func cmdEmbed(args []string) error {
	fs := newFlagSet("embed")
	opts := bindCommon(fs)
	ef := bindEmbedderFlags(fs)
	batch := fs.Int("batch", 16, "how many notes to embed per request")
	limit := fs.Int("limit", 0, "stop after this many notes (0 = all)")
	scope := fs.String("embed-scope", "",
		"comma-separated vault-relative directories to embed (default: the configured scope). "+
			"Two models compared on different scopes are not compared at all, so a bake-off "+
			"names this explicitly rather than inheriting it")
	reset := fs.Bool("reset", false,
		"discard every stored vector first — required when changing models, since "+
			"vectors from two models are not comparable")
	asJSON := fs.Bool("json", false, "emit the report as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath)
	if err != nil {
		return err
	}
	defer idx.Close()

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	log := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))
	sup, err := startEmbedder(ctx, cfg, *ef, log, 3*time.Minute)
	if err != nil {
		return err
	}
	defer sup.Close()
	if st, detail := sup.State(); st != embed.StateWarm {
		return fmt.Errorf("no embedder to run the backfill with (state %s: %s)", st, detail)
	}

	if *reset {
		if err := idx.DropVectors(); err != nil {
			return err
		}
		fmt.Fprintln(os.Stderr, "discarded every stored vector")
	}

	// A model swap without --reset would leave the table holding two geometries
	// at once. That is not a search that is a bit worse; the arms would rank
	// against different spaces and the result would be arbitrary, so it is
	// refused rather than warned about.
	models, err := idx.VectorModels()
	if err != nil {
		return err
	}
	for _, m := range models {
		if m != sup.Model().Name {
			return fmt.Errorf(
				"the index already holds vectors from %q and this run would add %q; "+
					"vectors from two models are not comparable — re-run with -reset",
				m, sup.Model().Name)
		}
	}

	embedScope := cfg.EmbedScope
	if *scope != "" {
		embedScope = splitList(*scope)
	}

	rep, err := runBackfill(ctx, idx, sup, embedScope, *batch, *limit, log)
	if err != nil {
		return err
	}
	if *asJSON {
		return json.NewEncoder(os.Stdout).Encode(rep)
	}
	fmt.Printf("embedded %d notes with %s (%d dims) in %s\n",
		rep.Embedded, rep.Model, rep.Dim, rep.ElapsedS)
	fmt.Printf("scope %s\n", strings.Join(rep.Scope, ", "))
	if rep.Chunked > 0 {
		fmt.Printf("%d note(s) were longer than the model's %d-token window and were split "+
			"into %d chunks total instead of being truncated\n",
			rep.Chunked, sup.Model().CtxTokens, rep.Chunks)
	}
	if rep.Shortened > 0 {
		fmt.Printf("%d chunk(s) were rejected at their first size and re-sent shorter "+
			"before the server accepted them\n", rep.Shortened)
	}
	if rep.Failed > 0 {
		fmt.Printf("%d note(s) could not be embedded at all and were skipped — they stay "+
			"pending, so a later run retries them:\n", rep.Failed)
		for _, p := range rep.FailedPaths {
			fmt.Printf("  %s\n", p)
		}
		if rep.Failed > len(rep.FailedPaths) {
			fmt.Printf("  … and %d more\n", rep.Failed-len(rep.FailedPaths))
		}
	}
	if rep.Stalled {
		fmt.Println("stopped early: an entire batch failed, so the run would have looped " +
			"over the same notes. The embedder may be out of memory — try a smaller -batch.")
	}
	if rep.Remaining > 0 {
		fmt.Printf("%d still pending\n", rep.Remaining)
	}
	return nil
}

// cmdStatus asks a running daemon how it is doing, and prints the answer as
// something a person can read at a glance.
//
// Four numbers and a verdict, which is the whole loud-queue mechanism from the
// operator's side: how many items are waiting to be filed, how old the oldest
// one is, whether the index still tracks the vault, whether there is an undo,
// and whether the round trip was working the last time anything checked.
func cmdStatus(args []string) error {
	fs := newFlagSet("status")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the raw status document")
	if err := fs.Parse(args); err != nil {
		return err
	}
	markPortSet(fs, opts)
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	blob, err := daemonGet(cfg.Port, "/status")
	if err != nil {
		return err
	}
	if *asJSON {
		_, err := os.Stdout.Write(blob)
		return err
	}

	var payload struct {
		Version string        `json:"version"`
		Uptime  string        `json:"uptime"`
		Health  health.Report `json:"health"`
	}
	if err := json.Unmarshal(blob, &payload); err != nil {
		return fmt.Errorf("the daemon's status was not readable: %w", err)
	}
	fmt.Printf("agentmd %s · up %s\n", payload.Version, payload.Uptime)
	fmt.Print(renderReport(payload.Health, cfg))
	if payload.Health.Red() {
		// Non-zero, so a shell script can ask "is it fine" without parsing the
		// output — the same reason the gate has an exit code.
		return &exitError{code: 3, quiet: true, err: errors.New("status is red")}
	}
	return nil
}

// cmdProbe runs the round trip now, against the running daemon.
//
// Same code path as the daily run, deliberately: a probe you can only trigger
// on a timer is one nobody can check when they need to know.
func cmdProbe(args []string) error {
	fs := newFlagSet("probe")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the raw result")
	if err := fs.Parse(args); err != nil {
		return err
	}
	markPortSet(fs, opts)
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	blob, err := daemonPost(cfg.Port, "/probe")
	if err != nil {
		return err
	}
	if *asJSON {
		_, err := os.Stdout.Write(blob)
		return err
	}
	var out struct {
		OK     bool        `json:"ok"`
		Detail string      `json:"detail"`
		Result probe.State `json:"result"`
	}
	if err := json.Unmarshal(blob, &out); err != nil {
		return fmt.Errorf("the daemon's probe result was not readable: %w", err)
	}
	for _, q := range out.Result.Queries {
		verdict := "MISSED"
		if q.Found {
			verdict = fmt.Sprintf("found at rank %d", q.Rank)
		}
		fmt.Printf("  %-6s %-24s %s\n", q.Kind, q.Query, verdict)
	}
	if !out.OK {
		fmt.Printf("self-probe FAILED after %s\n", out.Result.Elapsed)
		return &exitError{code: 3, err: errors.New(out.Detail)}
	}
	fmt.Printf("self-probe ok in %s — captured %s and found it again by two "+
		"queries it does not contain\n", out.Result.Elapsed, out.Result.Path)
	return nil
}

// cmdGate answers whether a corpus-wide write job may start.
//
// Exit codes are the contract: 0 passes, 3 refuses, 1 means the gate could not
// decide. A caller that treats anything non-zero as "do not start" is correct,
// which is the point — the gate fails closed.
func cmdGate(args []string) error {
	fs := newFlagSet("gate")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the verdict as JSON")

	// The gate's name is positional and Go's flag package stops parsing at the
	// first non-flag argument, so `gate corpus-write --vault X` would otherwise
	// leave every flag unparsed and fold them into the name. Pull the positional
	// out first and both orderings work — which matters because the natural one
	// is the one every caller writes.
	name, flagArgs := splitPositional(args)
	if err := fs.Parse(flagArgs); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd gate %s [flags]",
			extra[0], gate.CorpusWrite)
	}
	if name == "" {
		return fmt.Errorf("usage: agentmd gate %s [--json]", gate.CorpusWrite)
	}
	if name != gate.CorpusWrite {
		return fmt.Errorf("unknown gate %q; this daemon defines %q", name, gate.CorpusWrite)
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}

	res, gateErr := gate.Evaluate(cfg)
	if *asJSON {
		if err := json.NewEncoder(os.Stdout).Encode(res); err != nil {
			return err
		}
	} else {
		fmt.Print(res.Explain())
	}
	if res.Pass {
		return nil
	}
	if gateErr == nil {
		gateErr = gate.ErrRefused
	}
	return &exitError{code: 3, quiet: true, err: gateErr}
}

func daemonGet(port int, path string) ([]byte, error) {
	url := fmt.Sprintf("http://127.0.0.1:%d%s", port, path)
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Get(url)
	if err != nil {
		return nil, fmt.Errorf("no daemon answering on %s — start one with `agentmd serve`: %w",
			url, err)
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}

func daemonPost(port int, path string) ([]byte, error) {
	url := fmt.Sprintf("http://127.0.0.1:%d%s", port, path)
	resp, err := (&http.Client{Timeout: 60 * time.Second}).Post(url, "application/json", nil)
	if err != nil {
		return nil, fmt.Errorf("no daemon answering on %s — start one with `agentmd serve`: %w",
			url, err)
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}

// cmdClassify reports rank-penalty class counts over the live vault.
//
// This is the drift check on the classifier, and it exists because of a specific
// lesson: session 1's test suite passed unchanged after the classifier's behaviour
// changed. A suite that does not notice a real behavioural change is reporting on
// itself. Unit tests pin hand-written cases; this pins the population, against the
// counts the Python reference measured on the corpus the +3.75 result came from.
func cmdClassify(args []string) error {
	fs := newFlagSet("classify")
	opts := bindCommon(fs)
	showSamples := fs.Int("samples", 0, "print this many example paths per class")
	asJSON := fs.Bool("json", false,
		"emit one JSON object per note instead of the summary, so another tool can "+
			"filter on the classifier's own verdict rather than guessing at it")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(os.Stdout)

	counts := map[string]int{}
	samples := map[string][]string{}
	total, unreadable, probes := 0, 0, 0
	signals := map[string]int{}

	err = filepath.WalkDir(cfg.VaultPath, func(abs string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			if abs != cfg.VaultPath && strings.HasPrefix(d.Name(), ".") {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(d.Name(), ".md") || strings.HasPrefix(d.Name(), ".") {
			return nil
		}
		raw, readErr := os.ReadFile(abs)
		if readErr != nil {
			unreadable++
			return nil
		}
		rel, _ := filepath.Rel(cfg.VaultPath, abs)
		rel = filepath.ToSlash(rel)
		info, _ := d.Info()
		mod := time.Time{}
		if info != nil {
			mod = info.ModTime()
		}
		n := note.Parse(rel, string(raw), mod)
		if *asJSON {
			flags := n.Flags
			if flags == nil {
				flags = []string{}
			}
			// `probe` travels with every row so a downstream job can exclude
			// synthetic notes on the marker rather than on a path convention it
			// would have to keep in step with the shard.
			return enc.Encode(map[string]any{
				"path":   rel,
				"flags":  flags,
				"status": n.Status,
				"probe":  n.Probe,
				"weight": note.Multiplier(n.Flags),
			})
		}
		// The daemon's own self-probe notes are synthetic and are counted apart
		// rather than into the population: a class percentage that silently
		// included them would drift by one note a day for no reason anyone
		// reading it could see.
		if n.Probe {
			probes++
			return nil
		}
		total++
		for _, f := range n.Flags {
			counts[f]++
			if len(samples[f]) < *showSamples {
				samples[f] = append(samples[f], rel)
			}
		}
		// Per-signal breakdown, so a drift in one detector is attributable rather
		// than showing up only as a moved total. Read through the classifier's own
		// detectors — counting these a second way is how a drift check ends up
		// reporting on itself.
		sig := note.DetectSignals(rel, string(raw))
		if sig.LeadIn {
			signals["lead-in"]++
		}
		if sig.MiningConfidence {
			signals["mining_confidence"]++
		}
		if sig.TruncatedSlug {
			signals["truncated-slug"]++
		}
		return nil
	})
	if err != nil {
		return err
	}
	if *asJSON {
		return nil
	}

	fmt.Printf("vault:      %s\n", cfg.VaultPath)
	fmt.Printf("notes:      %d (%d unreadable)\n", total, unreadable)
	fmt.Printf("excluded:   %d self-probe notes (frontmatter `%s: %s`, not a path rule)\n",
		probes, note.ProbeMarker, note.ProbeMarkerValue)
	fmt.Println("classes:")
	keys := make([]string, 0, len(counts))
	for k := range counts {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		pct := 0.0
		if total > 0 {
			pct = 100 * float64(counts[k]) / float64(total)
		}
		weight := note.Weights[k]
		mark := fmt.Sprintf("weight %.2f", weight)
		if weight == 0 {
			mark = "UNPENALIZED (classified only)"
		}
		fmt.Printf("  %-20s %6d  %5.1f%%  %s\n", k, counts[k], pct, mark)
		for _, s := range samples[k] {
			fmt.Printf("      %s\n", s)
		}
	}
	// The report's figures for the same three detectors on the 8,700-note snapshot
	// the +3.75 R@5 result came from. Printed alongside so drift is visible here
	// rather than discovered by a score moving months later.
	fmt.Println("signals (report figures at 8,700 notes in parentheses):")
	for _, ref := range []struct {
		key      string
		reported int
	}{
		{"lead-in", 3413},
		{"mining_confidence", 5741},
		{"truncated-slug", 32},
	} {
		fmt.Printf("  %-20s %6d  (%d)\n", ref.key, signals[ref.key], ref.reported)
	}
	return nil
}

// ---------------------------------------------------------------------------
// retire
// ---------------------------------------------------------------------------

// retiredLaunchdLabel is the orphaned FastMCP daemon: it ran under launchd on port
// 7821, health-checked clean, and was wired to nothing for the project's entire
// life. Principle 4 allows one resident service; this is the second one.
const retiredLaunchdLabel = "com.agentm.memory-server"

func cmdRetire(args []string) error {
	fs := newFlagSet("retire")
	dryRun := fs.Bool("dry-run", false, "report what would happen and change nothing")
	if err := fs.Parse(args); err != nil {
		return err
	}

	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	plist := filepath.Join(home, "Library", "LaunchAgents", retiredLaunchdLabel+".plist")

	fmt.Println("Retiring the orphaned pre-daemon memory server")
	fmt.Println("  label: ", retiredLaunchdLabel)
	fmt.Println("  plist: ", plist)

	loaded := launchdLoaded(retiredLaunchdLabel)
	_, plistErr := os.Stat(plist)
	present := plistErr == nil
	fmt.Printf("  state:  loaded=%v plist_present=%v\n", loaded, present)

	if !loaded && !present {
		fmt.Println("nothing to retire — already gone")
		return nil
	}
	if *dryRun {
		fmt.Println("\ndry run: would bootout the job and archive the plist alongside itself")
		return nil
	}

	if loaded {
		// bootout is the modern form; unload is the fallback for older launchd.
		target := fmt.Sprintf("gui/%d/%s", os.Getuid(), retiredLaunchdLabel)
		if out, err := exec.Command("launchctl", "bootout", target).CombinedOutput(); err != nil {
			if out2, err2 := exec.Command("launchctl", "unload", "-w", plist).CombinedOutput(); err2 != nil {
				return fmt.Errorf("could not stop %s: bootout: %v (%s); unload: %v (%s)",
					retiredLaunchdLabel, err, strings.TrimSpace(string(out)),
					err2, strings.TrimSpace(string(out2)))
			}
		}
		fmt.Println("  stopped the launchd job")
	}

	if present {
		// Archived beside itself rather than deleted: retiring a service should be
		// one `mv` away from reversible.
		archived := plist + ".retired-" + time.Now().Format("20060102")
		if err := os.Rename(plist, archived); err != nil {
			return fmt.Errorf("archiving the plist: %w", err)
		}
		fmt.Println("  archived the plist to", filepath.Base(archived))
		fmt.Println("    (restore with: mv", archived, plist+")")
	}

	// Verify rather than assume.
	time.Sleep(500 * time.Millisecond)
	if free := portIsFree(config.DefaultPort); free {
		fmt.Printf("  port %d is now free\n", config.DefaultPort)
	} else {
		fmt.Printf("  WARNING: port %d is still held — something else is listening\n",
			config.DefaultPort)
	}

	fmt.Println()
	fmt.Println("Remaining, and not something this command can do for you:")
	fmt.Println("  The `agentmemory` MCP entry resolves to the stock filesystem server")
	fmt.Println("  pointed at the vault's parent directory — a coincidence of naming, not")
	fmt.Println("  a relationship. It is registered in the Claude Code app's own settings,")
	fmt.Println("  not in a file this command can safely rewrite, so removing it and")
	fmt.Println("  registering the daemon under that name is a two-step you run yourself:")
	fmt.Println()
	fmt.Println("    1. Remove the existing `agentmemory` entry in the app's MCP settings.")
	fmt.Printf("    2. claude mcp add --transport http agentmemory http://127.0.0.1:%d/mcp\n",
		config.DefaultPort)
	return nil
}

func launchdLoaded(label string) bool {
	out, err := exec.Command("launchctl", "list").CombinedOutput()
	if err != nil {
		return false
	}
	for _, line := range strings.Split(string(out), "\n") {
		if strings.HasSuffix(strings.TrimSpace(line), label) {
			return true
		}
	}
	return false
}

func portIsFree(port int) bool {
	ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		return false
	}
	ln.Close()
	return true
}

func openReadOnly(opts *config.Options) (*config.Config, *index.Index, error) {
	cfg, err := config.Load(*opts)
	if err != nil {
		return nil, nil, err
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath)
	if err != nil {
		return nil, nil, err
	}
	return cfg, idx, nil
}

func splitList(s string) []string {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}
