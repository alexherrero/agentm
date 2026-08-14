package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/rerank"
)

// rerankerFlags are the overrides every command that touches the cross-encoder
// accepts. Mirrors embedderFlags exactly — see resolveReranker for why the
// resolution order is the same.
type rerankerFlags struct {
	url   string
	model string
	off   bool
}

// resolveReranker turns config plus flags into a supervisor, by the same
// three-way precedence resolveEmbedder uses: an explicit URL attaches to a
// server someone else is running, a named model spawns that one, and
// otherwise whatever is installed is discovered. All three can end in
// StateOff, which is a working install rather than a failure — hybrid search
// without a reranker is exactly what task 2 already shipped, and it has to
// keep working exactly as well.
func resolveReranker(
	cfg *config.Config, f rerankerFlags, log *slog.Logger,
) (*rerank.Supervisor, error) {
	// AGENTM_NO_RERANKER mirrors AGENTM_NO_EMBEDDER for the identical reason:
	// a test suite spawning a daemon per case must not load a 600MB model
	// per daemon.
	if f.off || !cfg.RerankEnabled || os.Getenv("AGENTM_NO_RERANKER") == "1" {
		return rerank.New(rerank.Options{Logger: log}), nil
	}

	dir := rerank.DefaultModelDir()
	name := f.model
	if name == "" {
		name = cfg.RerankModel
	}

	url := f.url
	if url == "" {
		url = cfg.RerankerURL
	}

	var model rerank.Model
	if name != "" {
		m, err := rerank.Lookup(dir, name)
		if err != nil {
			return nil, err
		}
		model = m
	} else if m, ok := rerank.Discover(dir); ok {
		model = m
	} else if url != "" {
		return nil, fmt.Errorf(
			"a reranker URL was given but no model was named, and none is installed in %s; "+
				"pass -rerank-model (one of: %v)", dir, rerank.Known())
	} else {
		return rerank.New(rerank.Options{Logger: log}), nil
	}

	if url != "" {
		return rerank.Attach(url, model), nil
	}
	return rerank.New(rerank.Options{Model: model, Logger: log}), nil
}

// startReranker resolves and starts one, waiting up to `wait` for it to go
// warm — the same wait-vs-don't-wait split startEmbedder makes, and for the
// same reason: a one-shot rerank pass cannot proceed without a cross-encoder
// in hand.
func startReranker(
	ctx context.Context, cfg *config.Config, f rerankerFlags, log *slog.Logger, wait time.Duration,
) (*rerank.Supervisor, error) {
	sup, err := resolveReranker(cfg, f, log)
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
		case rerank.StateWarm:
			return sup, nil
		case rerank.StateOff:
			return sup, nil
		case rerank.StateDegraded:
			_ = detail
		}
		select {
		case <-ctx.Done():
			return sup, ctx.Err()
		case <-time.After(200 * time.Millisecond):
		}
	}
	st, detail := sup.State()
	return sup, fmt.Errorf("reranker did not become warm in %s (state %s: %s)", wait, st, detail)
}
