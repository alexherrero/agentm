package rerank

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

// State is what the status surface reports about the reranker. Same four
// values, same meanings, as embed.State — the two children are supervised
// identically even though what they serve differs.
type State string

const (
	StateOff      State = "off"
	StateStarting State = "starting"
	StateWarm     State = "warm"
	StateDegraded State = "degraded"
)

// Supervisor owns one llama-server child running in rerank mode: spawns it,
// waits for it to load, restarts it with backoff when it dies, and answers
// for its health. Loopback only, no config surface, no MCP exposure — the
// same posture as the embedder's supervisor, and for the same reason: this
// process is not a service anyone else can find.
type Supervisor struct {
	model  Model
	binary string
	ctxN   int
	log    *slog.Logger

	mu       sync.RWMutex
	state    State
	detail   string
	client   *Client
	started  time.Time
	restarts int
	// fails counts consecutive failed rerank calls — the real liveness
	// signal, see failThreshold.
	fails     int
	killChild func()

	stop context.CancelFunc
	done chan struct{}
}

// failThreshold matches embed's: three consecutive failures condemn the
// child. The same pathology applies to a reranker child as to an embedder
// one — a llama-server whose backend has faulted keeps answering /health
// with 200 while every real request 500s — so the same defense applies for
// the same reason, not by copy-paste habit. One failure is routinely a
// caller problem (an oversized batch); three in a row means the process
// itself stopped doing its job.
const failThreshold = 3

// Options configure a supervisor.
type Options struct {
	Model  Model
	Binary string
	Port   int
	Logger *slog.Logger
}

// ErrUnavailable is returned by Rerank when there is no warm child to ask.
// Callers are expected to treat it as "no rerank this time," the same
// degrade-not-fail contract embed.ErrUnavailable documents.
var ErrUnavailable = errors.New("reranker unavailable")

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
		log:    log,
		state:  StateOff,
		done:   make(chan struct{}),
	}
	if !o.Model.Installed() {
		s.detail = "no reranker model installed"
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

// Attach builds a supervisor over a server someone else is running — the
// one-shot `agentmd search -mode rerank` path and the scorecard's bake-off
// runs use this, for the identical reason embed.Attach exists: loading a
// 300-600MB model per query would price a measurement run in model loads
// rather than in searches.
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

// Available reports whether a rerank pass can be served right now.
func (s *Supervisor) Available() bool {
	st, _ := s.State()
	return st == StateWarm
}

// Restarts counts how many times the child has been respawned.
func (s *Supervisor) Restarts() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.restarts
}

// Start launches the child and keeps it alive until the context is
// cancelled or Close is called. It returns immediately; readiness is
// observed through State.
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
	}
	return nil
}

// supervise is the restart loop. Backoff doubles from one second to a
// one-minute ceiling and resets only after the child has actually served —
// identical to embed's, for the identical reason: a model that loads and
// then dies on its first request must not reset the backoff on every
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
		s.detail = "rerank off, unreranked"
		if err != nil {
			s.detail = fmt.Sprintf("rerank off, unreranked (%v)", err)
		}
		n := s.restarts
		s.mu.Unlock()
		s.log.Warn("reranker child exited; retrying",
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
	port, err := freePort()
	if err != nil {
		return false, err
	}
	base := "http://127.0.0.1:" + strconv.Itoa(port)

	// --rerank puts the server in reranking mode, serving /v1/rerank instead
	// of a chat or embedding endpoint. -np 1 and both batch sizes pinned to
	// the same value are carried over verbatim from the embedder's launch —
	// see internal/embed's own comment on runOnce for the two measured
	// defects this avoids (an unset -b silently capping every request at
	// 2048 regardless of -c, and llama-server dividing -c across parallel
	// slots when -np is left at its default of four). Both children run
	// under the identical Metal constraint on this machine — a larger buffer
	// page-faults on an idle box — so both are launched at the identical
	// 2048/2048/2048 triple.
	args := []string{
		"-m", s.model.Path,
		"--rerank",
		"--port", strconv.Itoa(port),
		"--host", "127.0.0.1",
		"-np", "1",
		"-c", strconv.Itoa(s.ctxN),
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

	// Exactly one waiter, started immediately and drained on every exit path
	// — see embed.Supervisor.runOnce's comment for why two waiters on one
	// process is a race that orphans children under a crash loop. The same
	// 73-orphan incident that motivated it there is the reason it is not
	// simplified away here.
	waitErr := make(chan error, 1)
	go func() { waitErr <- cmd.Wait() }()

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
	s.log.Info("reranker warm", "model", s.model.Name, "port", port)

	select {
	case <-ctx.Done():
		reap()
		return true, ctx.Err()
	case e := <-waitErr:
		reaped = true
		s.mu.Lock()
		s.client = nil
		s.killChild = nil
		s.mu.Unlock()
		return true, e
	}
}

// waitHealthy polls /health until the weights finish loading. Same generous
// three-minute budget as the embedder's: a cold 600MB model on a spinning-up
// machine legitimately takes tens of seconds.
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
	return fmt.Errorf("reranker did not become healthy: %w", last)
}

// Rerank scores every document against one query, returning raw
// cross-encoder logits in input order. Failures count toward the same
// liveness threshold embed.Supervisor.embed uses and for the identical
// reason: a wedged llama-server can answer /health with 200 while failing
// every real request.
func (s *Supervisor) Rerank(ctx context.Context, query string, docs []string) ([]float64, error) {
	s.mu.RLock()
	client, state := s.client, s.state
	s.mu.RUnlock()
	if client == nil || state != StateWarm {
		return nil, ErrUnavailable
	}
	scores, err := client.Rerank(ctx, query, docs)
	if err != nil {
		s.recordFailure(err)
		return nil, err
	}
	s.mu.Lock()
	s.fails = 0
	s.mu.Unlock()
	return scores, nil
}

// recordFailure counts a failed rerank call and condemns the child once the
// run of failures is long enough to mean the process itself is broken.
func (s *Supervisor) recordFailure(cause error) {
	s.mu.Lock()
	s.fails++
	n, kill := s.fails, s.killChild
	if n >= failThreshold {
		s.state = StateDegraded
		s.detail = fmt.Sprintf("rerank off, unreranked (%d consecutive failures: %v)", n, cause)
		s.fails = 0
	}
	s.mu.Unlock()

	if n < failThreshold {
		return
	}
	s.log.Warn("reranker failed repeatedly; replacing the child",
		"failures", n, "err", cause,
		"note", "the server can report /health ok while every rerank call fails")
	if kill != nil {
		kill()
	}
}

// freePort asks the kernel for an unused loopback port and immediately
// releases it — the same race every ephemeral-port allocator runs; a
// collision surfaces as a failed start and the backoff loop tries another.
func freePort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
}
