package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/embed"
	"github.com/alexherrero/agentm/daemon/internal/health"
	"github.com/alexherrero/agentm/daemon/internal/index"
)

// embedderFlags are the overrides every command that touches the vector arm
// accepts.
type embedderFlags struct {
	url   string
	model string
	off   bool
}

// resolveEmbedder turns config plus flags into a supervisor.
//
// Three ways to get one, in descending precedence: an explicit URL attaches to a
// server someone else is running, a named model spawns that one, and otherwise
// whatever is installed is discovered. All three can end in StateOff, which is a
// working install rather than a failure — a daemon with no embedder is the
// lexical-only daemon that shipped before this, and it has to keep working
// exactly as well.
func resolveEmbedder(
	cfg *config.Config, f embedderFlags, log *slog.Logger,
) (*embed.Supervisor, error) {
	// AGENTM_NO_EMBEDDER is the environment-level off switch, and it exists for
	// test suites more than for people. Every `agentmd serve` discovers whatever
	// model is installed and spawns it, so a suite that starts a dozen daemons on
	// a developer's machine loads a dozen copies of a 333MB model — which is how
	// this project ended up with 73 orphaned servers and a GPU too exhausted to
	// run the measurement the suite was supporting.
	if f.off || !cfg.EmbedEnabled || os.Getenv("AGENTM_NO_EMBEDDER") == "1" {
		return embed.New(embed.Options{Logger: log}), nil
	}

	dir := embed.DefaultModelDir()
	name := f.model
	if name == "" {
		name = cfg.EmbedModel
	}

	url := f.url
	if url == "" {
		url = cfg.EmbedderURL
	}

	var model embed.Model
	if name != "" {
		m, err := embed.Lookup(dir, name)
		if err != nil {
			return nil, err
		}
		model = m
	} else if m, ok := embed.Discover(dir); ok {
		model = m
	} else if url != "" {
		// A URL with no resolvable model is the one combination that cannot be
		// served: the dimension and the prompt scaffolding are properties of the
		// weights, and guessing them produces vectors that are wrong rather than
		// absent.
		return nil, fmt.Errorf(
			"an embedder URL was given but no model was named, and none is installed in %s; "+
				"pass -embed-model (one of: %v)", dir, embed.Known())
	} else {
		return embed.New(embed.Options{Logger: log}), nil
	}

	if url != "" {
		return embed.Attach(url, model), nil
	}
	return embed.New(embed.Options{Model: model, Logger: log}), nil
}

// startEmbedder resolves and starts one, waiting up to `wait` for it to go warm.
//
// Waiting is right for the commands that cannot proceed without vectors — the
// backfill, and a one-shot hybrid search. `serve` does not wait: the daemon must
// answer lexical searches while a model loads, not refuse to start for three
// minutes because one is cold.
func startEmbedder(
	ctx context.Context, cfg *config.Config, f embedderFlags, log *slog.Logger, wait time.Duration,
) (*embed.Supervisor, error) {
	sup, err := resolveEmbedder(cfg, f, log)
	if err != nil {
		return nil, err
	}
	sup.Start(ctx)
	if wait <= 0 {
		return sup, nil
	}
	deadline := time.Now().Add(wait)
	for time.Now().Before(deadline) {
		st, detail := sup.State()
		switch st {
		case embed.StateWarm:
			return sup, nil
		case embed.StateOff:
			return sup, nil
		case embed.StateDegraded:
			// Keep waiting: the supervisor's own backoff may still recover it,
			// and a transient bind collision on the first attempt is common.
			_ = detail
		}
		select {
		case <-ctx.Done():
			return sup, ctx.Err()
		case <-time.After(200 * time.Millisecond):
		}
	}
	st, detail := sup.State()
	return sup, fmt.Errorf("embedder did not become warm in %s (state %s: %s)", wait, st, detail)
}

// embedQuery embeds one search query, returning nil when there is no embedder.
//
// A nil vector is what makes ModeHybrid degrade to its lexical arm, so "no
// embedder" travels as an absent vector rather than as an error the caller has to
// decide how to swallow.
func embedQuery(ctx context.Context, sup *embed.Supervisor, text string) []float32 {
	if sup == nil || !sup.Available() {
		return nil
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	v, err := sup.EmbedQuery(ctx, text)
	if err != nil {
		return nil
	}
	return v
}

// embedderHealth assembles the status surface's view of the vector arm from its
// two owners: the supervisor knows whether the model is answering, the index
// knows whether there is anything to compare against.
//
// Both halves are needed to say anything true. A warm model over an empty table
// and a dead model over a full one both produce lexical-only searches, and a
// status line reporting only one of them would call one of those cases healthy.
func embedderHealth(sup *embed.Supervisor, idx *index.Index, cfg *config.Config) health.Embedder {
	st, detail := sup.State()
	out := health.Embedder{
		State:    string(st),
		Detail:   detail,
		Restarts: sup.Restarts(),
	}
	if st == embed.StateOff {
		return out
	}
	model := sup.Model()
	out.Model = model.Name
	stats, err := idx.VectorStats(model.Name, cfg.EmbedScope)
	if err != nil {
		return out
	}
	out.Vectors, out.InScope, out.Stale, out.Dim = stats.Vectors, stats.InScope, stats.Stale, stats.Dim
	return out
}

// embedBatch embeds a batch, falling back to per-note retries when the server
// rejects it. It returns the vectors, how many notes had to be shortened beyond
// the up-front estimate, and an error only if a note could not be embedded at all.
//
// The fallback exists because the character-per-token budget is an estimate and
// cannot be anything else without linking a tokenizer. When it is wrong the server
// answers with a 500 for the whole batch, so a batch failure says nothing about
// which note was oversized — the retry has to go one at a time to find out.
//
// Losing the note is the outcome this prevents. A note that never embeds is
// invisible to the vector arm forever and looks exactly like a note the model
// simply ranked poorly, which is the hardest kind of retrieval bug to find.
func embedBatch(
	ctx context.Context, sup *embed.Supervisor, texts []string,
) (vecs [][]float32, shortened int, failed []int, err error) {
	if v, e := sup.EmbedDocs(ctx, texts); e == nil {
		return v, 0, nil, nil
	}

	out := make([][]float32, len(texts))
	for i, t := range texts {
		// Up to four halvings, which takes any document this corpus contains
		// under any window: the largest note here is ~200k tokens and the
		// smallest window is 2k, and four halvings of a budget-cut text is far
		// past that.
		for attempt := 0; attempt < 5; attempt++ {
			v, e := sup.EmbedDocs(ctx, []string{t})
			if e == nil {
				out[i] = v[0]
				if attempt > 0 {
					shortened++
				}
				break
			}
			if ctx.Err() != nil {
				return nil, shortened, nil, ctx.Err()
			}
			t = index.EmbedRetryCut(t)
			if t == "" {
				break
			}
		}
		if out[i] == nil {
			// Skipped, not fatal. Not every failure is a length problem —
			// llama.cpp answers "Compute error" for backend faults that
			// shortening cannot fix — and aborting the run on one of them is
			// worse than useless: batches commit as they go, so the next run
			// resumes straight back onto the same note and dies again. That is a
			// permanent wall dressed up as a retry.
			//
			// The note stays pending rather than being recorded as done, so a
			// later run under a different model or a less loaded machine picks it
			// up by itself, and the count is reported loudly at the end.
			failed = append(failed, i)
		}
	}
	return out, shortened, failed, nil
}

// backfillReport is what one embedding pass did.
type backfillReport struct {
	Model     string   `json:"model"`
	Dim       int      `json:"dim"`
	Scope     []string `json:"scope"`
	Embedded  int      `json:"embedded"`
	Truncated int      `json:"truncated"`
	Remaining int      `json:"remaining"`
	// Failed counts notes the model refused even after shortening. They stay
	// pending rather than being marked done, so a later run retries them — but
	// they are reported, because a note the vector arm cannot see is invisible
	// in exactly the way a note it ranks poorly is not.
	Failed      int      `json:"failed"`
	FailedPaths []string `json:"failed_paths,omitempty"`
	// Stalled is set when a whole batch failed, which would otherwise loop.
	Stalled  bool          `json:"stalled,omitempty"`
	Elapsed  time.Duration `json:"-"`
	ElapsedS string        `json:"elapsed"`
}

// runBackfill embeds every in-scope note that has no current vector.
//
// It walks in batches and commits each batch, so an interrupted run keeps what it
// finished rather than restarting from nothing — at ten thousand notes and a
// model that takes minutes, "resume" is the difference between a maintenance
// task and a ceremony.
//
// Truncation is counted and reported rather than silently absorbed. A note longer
// than the model's window is embedded from its head, which is a real answer for a
// note whose subject is stated up front and a bad one for a long document that
// buries its point — either way the operator should be able to see how many are
// in that category rather than discovering it as unexplained misses.
func runBackfill(
	ctx context.Context, idx *index.Index, sup *embed.Supervisor,
	scope []string, batch int, limit int, log *slog.Logger,
) (backfillReport, error) {
	model := sup.Model()
	rep := backfillReport{Model: model.Name, Dim: model.Dim, Scope: scope}
	started := time.Now()

	if batch <= 0 {
		batch = 16
	}
	for {
		if ctx.Err() != nil {
			return rep, ctx.Err()
		}
		want := batch
		if limit > 0 && rep.Embedded+want > limit {
			want = limit - rep.Embedded
		}
		if want <= 0 {
			break
		}
		pending, err := idx.PendingEmbeds(model.Name, scope, want)
		if err != nil {
			return rep, err
		}
		if len(pending) == 0 {
			break
		}

		texts := make([]string, len(pending))
		for i, d := range pending {
			t, cut := index.TruncateForModel(
				index.EmbedText(d.Title, d.Body), model.CtxTokens)
			if cut {
				rep.Truncated++
			}
			texts[i] = t
		}

		vecs, shortened, failed, err := embedBatch(ctx, sup, texts)
		if err != nil {
			return rep, fmt.Errorf("embedding %d notes starting at %s: %w",
				len(pending), pending[0].Path, err)
		}
		rep.Truncated += shortened
		skipped := make(map[int]bool, len(failed))
		for _, i := range failed {
			skipped[i] = true
			rep.Failed++
			if len(rep.FailedPaths) < 20 {
				rep.FailedPaths = append(rep.FailedPaths, pending[i].Path)
			}
			if log != nil {
				log.Warn("could not embed note; skipping it and moving on",
					"path", pending[i].Path)
			}
		}
		rows := make([]index.VectorRow, 0, len(pending))
		for i, d := range pending {
			if skipped[i] {
				continue
			}
			rows = append(rows, index.VectorRow{DocID: d.ID, MtimeNS: d.MtimeNS, Vec: vecs[i]})
		}
		if err := idx.PutVectors(model.Name, rows); err != nil {
			return rep, err
		}
		rep.Embedded += len(rows)

		// A batch of nothing but failures would otherwise loop forever: the notes
		// stay pending by design, so the next query returns the same ones. Stop
		// and report instead of spinning.
		if len(rows) == 0 {
			rep.Stalled = true
			break
		}
		if log != nil && rep.Embedded%500 < len(rows) {
			log.Info("embedding", "done", rep.Embedded, "last", pending[len(pending)-1].Path)
		}
	}

	remaining, err := idx.PendingEmbeds(model.Name, scope, 0)
	if err != nil {
		return rep, err
	}
	rep.Remaining = len(remaining)
	rep.Elapsed = time.Since(started)
	rep.ElapsedS = rep.Elapsed.Round(time.Millisecond).String()
	return rep, nil
}
