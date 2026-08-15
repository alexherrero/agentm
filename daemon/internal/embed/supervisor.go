package embed

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"os"
	"os/exec"
	"strconv"
	"sync"
	"time"
)

// State is what the status surface reports about the embedder.
type State string

const (
	// StateOff means no embedder was configured or installed. Hybrid retrieval
	// is unavailable and the daemon is lexical-only — a legitimate install, not
	// a fault.
	StateOff State = "off"
	// StateStarting means the child is up but its weights are still loading.
	StateStarting State = "starting"
	// StateWarm means embeddings are flowing.
	StateWarm State = "warm"
	// StateDegraded means the child was expected and is not answering. Hybrid is
	// off and search silently falls back to lexical — which is why this state has
	// to be visible on the status surface rather than inferred from bad results.
	StateDegraded State = "degraded"
)

// Supervisor owns one llama-server child: it spawns it, waits for it to load,
// restarts it with backoff when it dies, and answers for its health.
//
// Loopback only, no config surface, no MCP exposure. The daemon is the only
// thing that talks to this process and it is not a service anyone else can find.
type Supervisor struct {
	model  Model
	binary string
	ctxN   int
	// port is the fixed loopback port to bind the child to. Zero (the default
	// for every caller but `serve`, per Options.Port) asks the kernel for a
	// free one instead — see runOnce.
	port int
	log  *slog.Logger

	mu       sync.RWMutex
	state    State
	detail   string
	client   *Client
	started  time.Time
	restarts int
	// fails counts consecutive failed embeddings. It is the real liveness
	// signal — see failThreshold.
	fails int
	// killChild ends the current child, so a wedged one can be replaced from the
	// request path rather than only when it happens to exit on its own.
	killChild func()

	stop context.CancelFunc
	done chan struct{}
}

// failThreshold is how many consecutive failed embeddings condemn a child.
//
// This exists because `/health` is not a liveness check for this server. A
// llama-server whose backend has faulted keeps answering `GET /health` with
// `{"status":"ok"}` and returns HTTP 500 "Compute error" for every embedding,
// including a five-token one — observed on this project, where a single bad
// decode poisoned the process permanently. A supervisor trusting `/health` would
// report `embedder ok (warm)` forever while every search silently fell back to
// its lexical arm, which is precisely the quietly-missing capability the status
// surface exists to prevent.
//
// So the work is the probe: three consecutive failures mean the child is not
// doing its job, whatever it says about itself, and it is killed and replaced.
// Three rather than one because a single failure is routinely the caller's fault
// — an oversized input the retry ladder is about to shorten — and killing a
// healthy model over one long note would be its own outage.
const failThreshold = 3

// Options configure a supervisor.
type Options struct {
	// Model is the weights to serve. A model that is not Installed() yields a
	// supervisor permanently in StateOff rather than an error — an install
	// without models is supposed to run lexical-only and say so.
	Model Model
	// Binary is the llama-server executable. Empty resolves it from PATH.
	Binary string
	// Port is the loopback port. Zero picks a free one, which is the normal case:
	// nothing else needs to find this process. `serve` is the exception — it
	// passes DefaultAttachPort so a one-shot `agentmd search -mode hybrid` has
	// somewhere fixed to attach to (see cmd/agentmd's cmdServe/cmdSearch).
	Port int
	// Logger receives child lifecycle events.
	Logger *slog.Logger
}

// ErrUnavailable is returned by Embed when there is no warm child to ask.
//
// Callers are expected to treat it as "no vector arm this time" and fall back to
// lexical, not as a failed search. A hybrid search that errors out because a
// model is loading would make the daemon less reliable than the lexical-only one
// it replaces.
var ErrUnavailable = errors.New("embedder unavailable")

// New builds a supervisor. It does not start anything.
func New(o Options) *Supervisor {
	log := o.Logger
	if log == nil {
		log = slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelWarn}))
	}
	binary := o.Binary
	if binary == "" {
		binary = "llama-server"
	}
	s := &Supervisor{
		model:  o.Model,
		binary: binary,
		ctxN:   o.Model.CtxTokens,
		port:   o.Port,
		log:    log,
		state:  StateOff,
		done:   make(chan struct{}),
	}
	if !o.Model.Installed() {
		s.detail = "no embedding model installed"
		close(s.done)
		return s
	}
	if _, err := exec.LookPath(binary); err != nil {
		s.state = StateDegraded
		s.detail = fmt.Sprintf("%s is not on PATH", binary)
		close(s.done)
		return s
	}
	s.state = StateStarting
	return s
}

// Attach builds a supervisor over a server someone else is running.
//
// This is what the one-shot `agentmd search` path and the scorecard use. Loading
// a 300MB–600MB model per query would price a measurement run in model loads
// rather than in searches, so an already-warm server is pointed at instead of
// spawned. Nothing is supervised in this mode — there is no child to restart.
func Attach(base string, model Model) *Supervisor {
	s := &Supervisor{
		model:  model,
		state:  StateWarm,
		detail: "attached to " + base,
		client: NewClient(base, 0),
		done:   make(chan struct{}),
		log:    slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelWarn})),
	}
	close(s.done)
	return s
}

// Model reports the model this supervisor serves.
func (s *Supervisor) Model() Model { return s.model }

// State reports the current state and a human-readable detail.
func (s *Supervisor) State() (State, string) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.state, s.detail
}

// Available reports whether a vector arm can be served right now.
func (s *Supervisor) Available() bool {
	st, _ := s.State()
	return st == StateWarm
}

// Restarts counts how many times the child has been respawned. A number that
// climbs is the difference between "it crashed once at boot" and "it is
// crashlooping and every search has silently been lexical for an hour."
func (s *Supervisor) Restarts() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.restarts
}

// Start launches the child and keeps it alive until the context is cancelled or
// Close is called. It returns immediately; readiness is observed through State.
func (s *Supervisor) Start(ctx context.Context) {
	st, _ := s.State()
	if st == StateOff {
		return
	}
	select {
	case <-s.done:
		return // Attach mode, or already finished.
	default:
	}
	ctx, cancel := context.WithCancel(ctx)
	s.stop = cancel
	go s.supervise(ctx)
}

// Close stops the child and waits for the supervision loop to finish.
func (s *Supervisor) Close() error {
	if s.stop != nil {
		s.stop()
	}
	select {
	case <-s.done:
	case <-time.After(10 * time.Second):
		// The loop kills its child before returning; ten seconds is far past
		// what that takes, and blocking daemon shutdown on a wedged model
		// process would be the model taking the daemon down after all.
	}
	return nil
}

// supervise is the restart loop.
//
// Backoff doubles from one second to a one-minute ceiling and resets only after
// the child has actually served — not merely started. A model that loads and
// then dies on its first request would otherwise reset the backoff on every
// attempt and spin at one second forever.
func (s *Supervisor) supervise(ctx context.Context) {
	defer close(s.done)

	backoff := time.Second
	const maxBackoff = time.Minute

	for {
		if ctx.Err() != nil {
			return
		}
		served, err := s.runOnce(ctx)
		if ctx.Err() != nil {
			return
		}
		if served {
			backoff = time.Second
		}
		s.mu.Lock()
		s.restarts++
		s.state = StateDegraded
		s.detail = "hybrid off, lexical-only"
		if err != nil {
			s.detail = fmt.Sprintf("hybrid off, lexical-only (%v)", err)
		}
		n := s.restarts
		s.mu.Unlock()
		s.log.Warn("embedder child exited; retrying",
			"restarts", n, "backoff", backoff, "err", err)

		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		if backoff < maxBackoff {
			backoff *= 2
			if backoff > maxBackoff {
				backoff = maxBackoff
			}
		}
	}
}

// runOnce spawns the child, waits for health, and blocks until it exits. The
// bool reports whether the child ever became healthy.
func (s *Supervisor) runOnce(ctx context.Context) (served bool, err error) {
	port := s.port
	if port == 0 {
		port, err = freePort()
		if err != nil {
			return false, err
		}
	}
	base := "http://127.0.0.1:" + strconv.Itoa(port)

	// --embeddings puts the server in embedding mode; --pooling mean is what
	// makes one vector per document instead of one per token. The ubatch is
	// pinned to the full context because a non-causal embedding model needs the
	// whole sequence in a single micro-batch — leaving it at the default
	// silently truncates every note longer than 512 tokens, which is most of
	// what is worth embedding.
	//
	// `-np 1` matters for the same reason and is easier to get wrong: llama-server
	// divides -c across its parallel slots, so the default four slots would give
	// each sequence a quarter of the window and truncate long notes without
	// saying anything. One slot is slower and is the only setting under which the
	// context this daemon thinks it has is the context a note actually gets.
	args := []string{
		"-m", s.model.Path,
		"--embeddings",
		"--pooling", "mean",
		"--port", strconv.Itoa(port),
		"--host", "127.0.0.1",
		"-np", "1",
		"-c", strconv.Itoa(s.ctxN),
		// Both batch sizes, not just one. `-ub` is the physical micro-batch and
		// `-b` is the logical batch, and a sequence has to fit under *both* — `-b`
		// defaults to 2048 regardless of the context, so setting only `-ub` caps
		// every note at 2048 tokens no matter how large the window is.
		//
		// This was measured, not assumed: with `-c 8192 -ub 8192` and `-b` left
		// alone, an 8k-window model rejected a 3,600-token note with "Compute
		// error" and `n_batch = 2048` in its log. The symptom reads as a model
		// fault — it is a launch-argument fault, and it made a good model look
		// like it could not embed the corpus.
		"-b", strconv.Itoa(s.ctxN),
		"-ub", strconv.Itoa(s.ctxN),
		"-ngl", "99",
	}
	cmd := exec.CommandContext(ctx, s.binary, args...)
	cmd.Stdout = nil
	cmd.Stderr = nil
	if err := cmd.Start(); err != nil {
		return false, err
	}

	// Exactly one waiter, started immediately and drained on every exit path.
	//
	// The earlier shape called cmd.Wait() in a defer *and* cmd.Process.Wait() in a
	// goroutine. Two waiters on one process is a race: whichever reaps first wins,
	// the other gets "Wait was already called", and the Kill in between can land
	// on a PID the kernel has already recycled. Under a crash-loop — which is
	// exactly when the restart path runs — that is how a supervisor ends up with
	// more children than it thinks it has, each holding GPU memory, until the
	// machine cannot start the next one. Observed on this project: 73 orphaned
	// servers, and the resulting out-of-memory errors read as a bad model.
	waitErr := make(chan error, 1)
	go func() { waitErr <- cmd.Wait() }()

	// reap kills the child if it is still running and blocks until it is gone, so
	// no path out of this function leaves a model process behind.
	reaped := false
	reap := func() {
		if reaped {
			return
		}
		reaped = true
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
		}
		<-waitErr
	}
	defer reap()

	client := NewClient(base, 0)
	if err := s.waitHealthy(ctx, client); err != nil {
		return false, err
	}

	s.mu.Lock()
	s.state = StateWarm
	s.detail = "warm"
	s.client = client
	s.started = time.Now()
	s.fails = 0
	s.killChild = func() { _ = cmd.Process.Kill() }
	s.mu.Unlock()
	s.log.Info("embedder warm", "model", s.model.Name, "dim", s.model.Dim, "port", port)

	// Block until the child exits or the daemon is shutting down.
	select {
	case <-ctx.Done():
		reap()
		return true, ctx.Err()
	case e := <-waitErr:
		reaped = true // the single waiter already returned; nothing left to reap
		s.mu.Lock()
		s.client = nil
		s.killChild = nil
		s.mu.Unlock()
		return true, e
	}
}

// waitHealthy polls /health until the weights finish loading.
//
// The budget is generous because a cold 600MB model on a spinning-up machine
// legitimately takes tens of seconds, and a supervisor that gave up at five
// would restart into the same load and never finish one.
func (s *Supervisor) waitHealthy(ctx context.Context, c *Client) error {
	deadline := time.Now().Add(3 * time.Minute)
	var last error
	for time.Now().Before(deadline) {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err := c.Healthy(ctx); err == nil {
			return nil
		} else {
			last = err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
	return fmt.Errorf("embedder did not become healthy: %w", last)
}

// EmbedQuery embeds one search query, with the model's query-side scaffolding.
func (s *Supervisor) EmbedQuery(ctx context.Context, text string) ([]float32, error) {
	vecs, err := s.embed(ctx, []string{s.model.WrapQuery(text)})
	if err != nil {
		return nil, err
	}
	return vecs[0], nil
}

// EmbedDocs embeds note bodies, with the model's document-side scaffolding.
func (s *Supervisor) EmbedDocs(ctx context.Context, texts []string) ([][]float32, error) {
	wrapped := make([]string, len(texts))
	for i, t := range texts {
		wrapped[i] = s.model.WrapDoc(t)
	}
	return s.embed(ctx, wrapped)
}

func (s *Supervisor) embed(ctx context.Context, texts []string) ([][]float32, error) {
	s.mu.RLock()
	client, state := s.client, s.state
	s.mu.RUnlock()
	if client == nil || state != StateWarm {
		return nil, ErrUnavailable
	}
	vecs, err := client.Embed(ctx, texts)
	if err != nil {
		s.recordFailure(err)
		return nil, err
	}
	for i, v := range vecs {
		if len(v) != s.model.Dim {
			// A width mismatch is not a transient fault and restarting will not
			// fix it, so it does not count toward the threshold — the weights are
			// simply not what this build thinks they are.
			return nil, fmt.Errorf(
				"model %s returned %d dimensions at index %d, catalog says %d — "+
					"the weights are not what this build thinks they are",
				s.model.Name, len(v), i, s.model.Dim)
		}
	}
	s.mu.Lock()
	s.fails = 0
	s.mu.Unlock()
	return vecs, nil
}

// recordFailure counts a failed embedding and condemns the child once the run of
// failures is long enough to mean the process itself is broken.
func (s *Supervisor) recordFailure(cause error) {
	s.mu.Lock()
	s.fails++
	n, kill := s.fails, s.killChild
	if n >= failThreshold {
		s.state = StateDegraded
		s.detail = fmt.Sprintf("hybrid off, lexical-only (%d consecutive failures: %v)", n, cause)
		s.fails = 0
	}
	s.mu.Unlock()

	if n < failThreshold {
		return
	}
	s.log.Warn("embedder failed repeatedly; replacing the child",
		"failures", n, "err", cause,
		"note", "the server can report /health ok while every embedding fails")
	if kill != nil {
		// Killing makes the supervision loop's Wait return, which restarts the
		// child under the usual backoff. In Attach mode there is nothing to kill
		// and the state stays degraded, which is the honest answer: this process
		// does not own that server and cannot repair it.
		kill()
	}
}

// freePort asks the kernel for an unused loopback port and immediately releases
// it. There is a race between release and the child's bind, and it is the same
// race every ephemeral-port allocator runs; a collision surfaces as a failed
// start and the backoff loop tries another.
func freePort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
}
