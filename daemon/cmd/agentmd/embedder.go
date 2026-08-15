package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"strings"
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
	// port is the fixed loopback port to spawn a NEW child on, when spawning
	// is what resolution does (no attach URL resolved). Zero — the default
	// for every command except `serve` — keeps picking a free one, unchanged.
	// Not a CLI flag: `serve` is the only caller that ever sets it (see
	// cmdServe), and exposing the daemon's own bind port on the command line
	// would invite exactly the collision this value exists to let OTHER
	// commands (the backfill, an explicit-URL measurement run) avoid by
	// always getting an isolated, unshared child.
	port int
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
	return embed.New(embed.Options{Model: model, Port: f.port, Logger: log}), nil
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

// embedderAttachTarget reports whether resolution already has somewhere
// explicit to point the embedder: an -embedder-url flag, or daemon.embedder_url
// in config. Shared by the two functions below so they agree about what counts
// as "already resolved" without duplicating the check.
func embedderAttachTarget(f embedderFlags, cfg *config.Config) bool {
	return f.url != "" || cfg.EmbedderURL != ""
}

// embedderSpawnPort is the port `serve` should spawn its own embedder on, so a
// one-shot hybrid search (see embedderAttachDefault) has somewhere fixed to
// attach to instead of loading its own model per query.
//
// Returns 0 — pick a free one, the default for every command but `serve` —
// once something already resolves an explicit attach target: spawning on a
// fixed port would then be moot, and worse, would collide with whatever the
// operator pointed EmbedderURL at instead.
func embedderSpawnPort(f embedderFlags, cfg *config.Config) int {
	if embedderAttachTarget(f, cfg) {
		return 0
	}
	return embed.DefaultAttachPort
}

// embedderAttachDefault is the URL a one-shot `agentmd search -mode hybrid`
// should attach to when nothing more specific was given — the resident
// `agentmd serve` daemon's own embedder, which embedderSpawnPort binds to this
// same fixed port for exactly this reason. Attaching never blocks: if nothing
// is listening there (serve is down, or hasn't bound an embedder), the first
// embed call fails fast and the search degrades to its lexical arm, the same
// graceful path a genuinely absent embedder always took.
//
// Returns "" once something already resolves an explicit target, leaving it
// untouched — an operator's own -embedder-url or daemon.embedder_url is never
// second-guessed.
func embedderAttachDefault(f embedderFlags, cfg *config.Config) string {
	if embedderAttachTarget(f, cfg) {
		return ""
	}
	return fmt.Sprintf("http://127.0.0.1:%d", embed.DefaultAttachPort)
}

// queryEmbedText decides what text the dense arm embeds — task 3.5's
// question-passthrough rung.
//
// The natural question, when the caller supplied one (CLI `-question`, MCP's
// unpublished `question` argument on the same seam `mode` already uses),
// truncated defensively to the model's own window before it is wrapped: the
// production hook will eventually hand this path a whole pasted prompt, and
// that input is not bounded the way a terms string always has been. The
// extracted terms otherwise — returned exactly as given, which is what makes
// "no question supplied" byte-identical to every measurement before this
// task rather than merely close to it.
//
// The lexical arms never see this decision. They search index.Query.Text,
// which every caller sets from the terms and never from the question — the
// AND/fusion term reduction is correct for FTS5 and this task does not touch
// it.
func queryEmbedText(terms, question string, ctxTokens int) string {
	if q := strings.TrimSpace(question); q != "" {
		return index.TruncateQuery(q, ctxTokens)
	}
	return terms
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
	// health.Embedder.Vectors is documented as a coverage count ("X of Y
	// embedded"), so it reads VectorStats.Notes — distinct notes with a current
	// embedding — not the raw chunk-row count. A note split into several chunks
	// must not inflate what the status line reports as covered.
	out.Vectors, out.InScope, out.Stale, out.Dim = stats.Notes, stats.InScope, stats.Stale, stats.Dim
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
	Model string   `json:"model"`
	Dim   int      `json:"dim"`
	Scope []string `json:"scope"`
	// Embedded counts notes, not chunks: a note split into several chunks is
	// still one embedded note.
	Embedded int `json:"embedded"`
	// Chunks is the total number of chunk vectors written this run. Equal to
	// Embedded when every note fit the window in one piece; larger whenever
	// Chunked is nonzero.
	Chunks int `json:"chunks"`
	// Chunked counts notes that did not fit the model's window in one piece and
	// were split into more than one chunk — the population this daemon used to
	// silently truncate from the head before chunking existed. Reported rather
	// than absorbed, same as the truncation count it replaces.
	Chunked int `json:"chunked"`
	// Shortened counts individual chunks the server rejected at their first
	// size, which then had to be halved down (EmbedRetryCut) before it accepted
	// them — the up-front byte-budget estimate turning out wrong for that one
	// piece of text, not a note being split into chunks.
	Shortened int `json:"shortened,omitempty"`
	Remaining int `json:"remaining"`
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

// runBackfill embeds every in-scope note that has no current embedding.
//
// It walks in batches and commits each batch, so an interrupted run keeps what it
// finished rather than restarting from nothing — at ten thousand notes and a
// model that takes minutes, "resume" is the difference between a maintenance
// task and a ceremony.
//
// A note longer than the model's window is split into several chunks
// (index.ChunkText) rather than truncated from its head, so none of it goes
// unembedded. Chunk counts are still reported rather than silently absorbed,
// the same discipline the truncation counter this replaces already applied —
// the operator should be able to see how many notes needed more than one
// vector rather than discovering it as unexplained misses.
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

		// Each note becomes one or more window-sized chunks, every one carrying
		// the title. embedBatch keeps treating "texts" as one flat list — a text
		// is a text whether it is a whole short note or one slice of a long one
		// — so textNote and textChunk are what let this loop find its way back
		// from a flat embedding result to which note, and which chunk of it,
		// each vector belongs to.
		var texts []string
		var textNote []int
		var textChunk []int
		for i, d := range pending {
			chunks := index.ChunkText(d.Title, d.Body, model.CtxTokens)
			if len(chunks) > 1 {
				rep.Chunked++
			}
			for ci, c := range chunks {
				texts = append(texts, c)
				textNote = append(textNote, i)
				textChunk = append(textChunk, ci)
			}
		}

		vecs, shortened, failed, err := embedBatch(ctx, sup, texts)
		if err != nil {
			return rep, fmt.Errorf("embedding %d notes starting at %s: %w",
				len(pending), pending[0].Path, err)
		}
		rep.Shortened += shortened

		// A note with any failed chunk is skipped whole, not partially stored.
		// A partial chunk set is worse than none: it looks exactly like a note
		// the model ranked poorly rather than one a fraction of whose content
		// was silently never given a vector.
		failedNote := make(map[int]bool, len(failed))
		for _, ti := range failed {
			failedNote[textNote[ti]] = true
		}
		for i, d := range pending {
			if !failedNote[i] {
				continue
			}
			rep.Failed++
			if len(rep.FailedPaths) < 20 {
				rep.FailedPaths = append(rep.FailedPaths, d.Path)
			}
			if log != nil {
				log.Warn("could not embed note; skipping it and moving on", "path", d.Path)
			}
		}

		rows := make([]index.VectorRow, 0, len(texts))
		embeddedNotes := make(map[int]bool, len(pending))
		for ti := range texts {
			ni := textNote[ti]
			if failedNote[ni] {
				continue
			}
			rows = append(rows, index.VectorRow{
				DocID:    pending[ni].ID,
				ChunkIdx: textChunk[ti],
				MtimeNS:  pending[ni].MtimeNS,
				Vec:      vecs[ti],
			})
			embeddedNotes[ni] = true
		}
		if err := idx.PutVectors(model.Name, rows); err != nil {
			return rep, err
		}
		rep.Embedded += len(embeddedNotes)
		rep.Chunks += len(rows)

		// A batch that embedded no note would otherwise loop forever: the notes
		// stay pending by design, so the next query returns the same ones. Stop
		// and report instead of spinning.
		if len(embeddedNotes) == 0 {
			rep.Stalled = true
			break
		}
		if log != nil && rep.Embedded%500 < len(embeddedNotes) {
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
