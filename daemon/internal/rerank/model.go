// Package rerank runs a local cross-encoder as a second supervised child
// process and speaks its rerank API, mirroring package embed's contract for
// the embedder — spawn, health-check, restart with backoff — over a different
// llama-server mode and a different wire shape.
//
// It is not built on top of package embed. The two children are launched
// differently (`--rerank` against `--embeddings --pooling mean`), speak
// different endpoints (`/v1/rerank` against `/embedding`), and score
// differently (one raw relevance logit per document against one vector) —
// sharing the supervision skeleton would mean a generic child-process package
// neither model actually needs today. Task 2's embedder package and its tests
// stay untouched; this is a second, independent child, exactly as the design
// describes it.
package rerank

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Model is one pinned cross-encoder: where its weights are, what context
// window it is launched with, and the relevance floor chosen for it.
type Model struct {
	// Name is the identifier reported on the status surface and recorded
	// beside every measurement. Not stored per-row anywhere — unlike a vector,
	// a rerank score is never persisted, so there is no comparability hazard
	// to guard against here the way embed.Model.Name guards one.
	Name string
	// Path is the GGUF file. Empty means the model is not installed.
	Path string
	// CtxTokens is what the child is launched with, and therefore the window
	// a query+document pair must fit inside. It is also the window this
	// package's callers chunk long documents to before scoring them — the
	// same figure the embedder's own window was measured at, because both
	// children run under the identical Metal constraint recorded in
	// internal/embed: larger batch buffers page-fault on this hardware.
	CtxTokens int
	// Floor is the minimum score, on the 0..1 scale Sigmoid produces, a
	// document must reach to survive into a reranked result set. Chosen
	// off-gold, before any gold-set scoring run — the literature prior
	// (~0.35) sanity-checked against this model's own score distribution on
	// probe queries the gold set does not contain, never swept against the
	// gold set itself. See the task-3 close-out in
	// progress-hybrid-retrieval.md for the probe and the number it produced;
	// NOTES.md's "floor 14" entry is the on-record example of why a swept
	// constant is not shippable even when it measures well.
	//
	// Per-model. bge-reranker-v2-m3 and jina-reranker-v2 are independently
	// trained cross-encoders with unrelated logit scales — a floor fitted to
	// one is not a floor for the other, only a number that happens to also be
	// between zero and one.
	Floor float64
}

// Installed reports whether this model's weights are actually on disk.
func (m Model) Installed() bool {
	if m.Path == "" {
		return false
	}
	fi, err := os.Stat(m.Path)
	return err == nil && !fi.IsDir()
}

// catalog is every reranker this daemon knows how to drive, keyed by the
// GGUF's base filename. Adding one is a code change on purpose, same
// reasoning as package embed's catalog: the context window and the floor are
// facts measured about the weights, and a config file that could assert them
// wrongly would produce a rerank pass that is silently miscalibrated rather
// than absent.
var catalog = map[string]Model{
	"bge-reranker-v2-m3-Q8_0.gguf": {
		Name:      "bge-reranker-v2-m3-Q8_0",
		CtxTokens: 2048,
		// Measured 2026-08-13 against an off-gold probe of 12 answerable and
		// 12 negative (query, passage) pairs drawn from the frozen corpus,
		// excluding every gold-set expected-answer path and every gold
		// question's own phrasing — see progress-hybrid-retrieval.md's
		// task-3 close-out for the full distribution. Sigmoid scores: every
		// negative fell at or below 0.0237; every answerable landed at or
		// above 0.9374 except one genuinely weak paraphrase at 0.2520. The
		// literature prior (~0.35) would have discarded that one real hit,
		// while the negative population leaves enormous headroom below it —
		// this model separates far more sharply than the prior assumes, so
		// the floor is derived from its own measured gap instead of the
		// prior: 0.10 sits with real margin above the negative ceiling
		// (4x) and below the weakest observed true positive (2.5x), on the
		// conservative side because a floor that clips a weak true hit is
		// the failure this task's own risk section names, and clean
		// rejection of a negative has roughly four times the margin to
		// spare.
		Floor: 0.10,
	},
	"jina-reranker-v2-base-multilingual-Q8_0.gguf": {
		Name: "jina-reranker-v2-base-multilingual-Q8_0",
		// 1024, not 2048 — measured, not assumed. The child is launched at
		// -c 2048 like the embedder and the other reranker candidate (the
		// task's own ops note), but llama-server's /props reports back
		// n_ctx: 1024 regardless: this model's own n_ctx_train is smaller
		// than the launch flag, and llama-server silently caps to it rather
		// than erroring at startup — the identical phenomenon task 2
		// documented for EmbeddingGemma's window, now measured on a
		// reranker. Chunking this model at 2048 produced a 400
		// exceed_context_size_error ("input (1923 tokens) is larger than
		// the max context size (1024 tokens)") on the very first probe
		// pair tried.
		CtxTokens: 1024,
		// Measured against the identical probe bge was scored on (see that
		// entry's comment), re-chunked to this model's real 1024-token
		// window. jina's scores run far more compressed than bge's: every
		// answerable landed at or above 0.4677, and negatives mostly
		// clustered under 0.05 but one reached 0.2251 — a materially
		// noisier negative population than bge's. Here the literature prior
		// survives the sanity check rather than needing correction: 0.35
		// sits almost exactly at the empirical midpoint of the gap
		// (0.2251-0.4677), clears the worst observed negative by 0.125 and
		// sits 0.118 below the weakest observed true positive. Kept as-is
		// rather than tuned tighter to this one probe sample.
		Floor: 0.35,
	},
}

// DefaultModelDir is where install.sh puts every model's weights, reranker
// and embedder alike — one directory, resolved from $HOME rather than
// written as a literal, for the same reason no vault path is ever a constant
// here.
func DefaultModelDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".local", "share", "agentm", "models")
}

// Lookup resolves a model by GGUF filename or by the bare catalog name,
// against a directory of weights.
//
// An unknown filename is an error rather than a guess, same reasoning as
// package embed: guessing a context window would silently mis-truncate every
// chunk sent to the model, which surfaces nowhere except as a reranker that
// scores worse than its own probe suggested.
func Lookup(dir, name string) (Model, error) {
	if name == "" {
		return Model{}, fmt.Errorf("no reranker model named")
	}
	base := filepath.Base(name)
	m, ok := catalog[base]
	if !ok {
		for file, cand := range catalog {
			if cand.Name == strings.TrimSuffix(base, ".gguf") {
				m, base, ok = cand, file, true
				break
			}
		}
	}
	if !ok {
		return Model{}, fmt.Errorf(
			"unknown reranker model %q; this build knows %s", name, strings.Join(Known(), ", "))
	}
	if filepath.IsAbs(name) {
		m.Path = name
	} else {
		m.Path = filepath.Join(dir, base)
	}
	return m, nil
}

// Known lists the catalog, sorted, for error messages.
func Known() []string {
	out := make([]string, 0, len(catalog))
	for _, m := range catalog {
		out = append(out, m.Name)
	}
	sort.Strings(out)
	return out
}

// Discover picks the installed model from a directory when the operator has
// not named one.
//
// Ties break by the catalog's sort order rather than filesystem order, same
// reasoning as package embed's Discover: two installs choosing different
// rerankers from the same directory would make their measurements
// incomparable for no visible reason.
func Discover(dir string) (Model, bool) {
	if dir == "" {
		return Model{}, false
	}
	var found []Model
	for file, m := range catalog {
		m.Path = filepath.Join(dir, file)
		if m.Installed() {
			found = append(found, m)
		}
	}
	if len(found) == 0 {
		return Model{}, false
	}
	sort.Slice(found, func(i, j int) bool { return found[i].Name < found[j].Name })
	return found[0], true
}
