// Package probe is principle 3 running as a live process against the daemon
// itself.
//
// Once a day it writes a synthetic memory, asks for it back sideways, and
// records whether it came. That is the whole system's definition of working —
// "nothing is saved until a fresh session can ask and get it back" — checked
// against the running daemon rather than asserted by a test that passed once in
// CI. The system this replaces had 2,964 green unit tests while returning zero
// results on every interactive prompt, and no scheduled check that would have
// noticed.
//
// Three details are what make it a real probe rather than a self-congratulating
// one:
//
//   - It goes over HTTP, to the daemon's own MCP surface, so it exercises the
//     wiring a session actually uses. An in-process call would reach past the
//     layer that was broken the last time this failed.
//   - It asks sideways. The alias nonce appears nowhere in the note's prose, so
//     finding it proves the meta column is indexed rather than proving the index
//     can echo a phrase back.
//   - It leaves the note behind. A probe that tidied up perfectly would be
//     unfalsifiable from the outside; the current probe note is the artifact
//     anyone can go and look at.
package probe

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/health"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/note"
)

// Tag is the searchable marker on every probe note. It is one FTS token on
// purpose: finding every probe the daemon has ever written is a single query
// against the daemon's own index, not a directory walk that would re-introduce
// exactly the path-based thinking the marker rule exists to replace.
const Tag = "selfprobe"

// State is the last run, persisted so a probe that failed at 3am is still the
// reason the status is red at 9.
type State struct {
	At      time.Time `json:"at"`
	OK      bool      `json:"ok"`
	Elapsed string    `json:"elapsed"`
	Detail  string    `json:"detail,omitempty"`
	Path    string    `json:"path,omitempty"`
	// Queries records what was asked and whether each came back, so a failure
	// report names the half that broke instead of saying "the probe failed".
	Queries []QueryResult `json:"queries,omitempty"`
}

// QueryResult is one sideways question and its answer.
type QueryResult struct {
	Kind  string `json:"kind"`
	Query string `json:"query"`
	Found bool   `json:"found"`
	Rank  int    `json:"rank,omitempty"`
}

// Runner performs the probe. It holds the index only to keep the cache in step
// when it retires an old probe note; every claim it makes about the round trip
// is made over HTTP.
type Runner struct {
	cfg       *config.Config
	idx       *index.Index
	baseURL   string
	statePath string
	markPath  func(rel string)
	client    *http.Client
}

// New builds a runner against a daemon already listening at baseURL.
func New(cfg *config.Config, idx *index.Index, baseURL string, markPath func(rel string)) *Runner {
	return &Runner{
		cfg:       cfg,
		idx:       idx,
		baseURL:   strings.TrimSuffix(baseURL, "/"),
		statePath: filepath.Join(cfg.StateDir, "probe-state.json"),
		markPath:  markPath,
		client:    &http.Client{Timeout: 30 * time.Second},
	}
}

// StatePath is where the last result is recorded, for reporting.
func (r *Runner) StatePath() string { return r.statePath }

// Load reads the last recorded run. A missing or unreadable record reads as
// "never ran", which is the honest answer and the one that eventually goes red.
func (r *Runner) Load() (State, bool) {
	blob, err := os.ReadFile(r.statePath)
	if err != nil {
		return State{}, false
	}
	var st State
	if err := json.Unmarshal(blob, &st); err != nil {
		return State{}, false
	}
	if st.At.IsZero() {
		return State{}, false
	}
	return st, true
}

// Due reports whether the interval has elapsed since the last run. A failed run
// counts as a run: retrying every fifteen minutes would write a note per retry
// and turn a broken round trip into a second problem.
func (r *Runner) Due(now time.Time) bool {
	st, ok := r.Load()
	if !ok {
		return true
	}
	return now.Sub(st.At) >= r.cfg.ProbeEvery
}

// Run performs one probe and records the result. The returned error is the
// probe's own failure, already recorded — callers log it rather than treating it
// as a reason to stop.
func (r *Runner) Run(now time.Time) (State, error) {
	bodyNonce := "pb" + nonce()
	aliasNonce := "pa" + nonce()
	stamp := now.UTC().Format("2006-01-02T15:04:05Z")

	st := State{At: now.UTC()}
	started := time.Now()

	fail := func(format string, args ...any) (State, error) {
		st.Detail = fmt.Sprintf(format, args...)
		st.Elapsed = time.Since(started).Round(time.Millisecond).String()
		st.OK = false
		r.save(st)
		return st, fmt.Errorf("self-probe failed: %s", st.Detail)
	}

	// --- write --------------------------------------------------------------
	res, err := r.call("memory_capture", map[string]any{
		"title": "AgentM self-probe " + stamp,
		"text": strings.Join([]string{
			"Synthetic round-trip probe written by the daemon at " + stamp + ".",
			"",
			"This note is not a memory. It exists so the daemon can prove, once a day, " +
				"that something written can be found again — the property everything " +
				"else in this system is downstream of. Marker: " + bodyNonce + ".",
			"",
			"It is safe to delete. The next run writes another one and retires this.",
		}, "\n"),
		"type":   "reference",
		"status": "active",
		"tags":   []string{Tag, "synthetic"},
		// The alias nonce appears nowhere above. Asking for it is the sideways
		// question: it can only be answered from the meta column.
		"aliases": []string{aliasNonce},
		"probe":   true,
	})
	if err != nil {
		return fail("capture did not complete: %v", err)
	}
	rel, _ := res["path"].(string)
	if rel == "" {
		return fail("capture returned no path")
	}
	st.Path = rel
	if r.markPath != nil {
		r.markPath(rel)
	}

	// The file is truth and the index is a cache, so the probe checks the file
	// exists before it believes a search that says it does.
	abs := filepath.Join(r.cfg.VaultPath, filepath.FromSlash(rel))
	if _, err := os.Stat(abs); err != nil {
		return fail("capture reported %s but nothing is on disk: %v", rel, err)
	}

	// --- ask sideways -------------------------------------------------------
	for _, q := range []struct{ kind, query string }{
		{"alias", aliasNonce},
		{"body", bodyNonce},
	} {
		found, rank, err := r.findPath(q.query, rel)
		if err != nil {
			return fail("the %s query %q failed: %v", q.kind, q.query, err)
		}
		st.Queries = append(st.Queries, QueryResult{
			Kind: q.kind, Query: q.query, Found: found, Rank: rank,
		})
		if !found {
			return fail(
				"captured %s, and the %s query %q did not return it — the round trip is "+
					"broken, which means capture is writing memories the system cannot find",
				rel, q.kind, q.query)
		}
	}

	elapsed := time.Since(started)
	st.Elapsed = elapsed.Round(time.Millisecond).String()
	if r.cfg.ProbeBudget > 0 && elapsed > r.cfg.ProbeBudget {
		return fail("the round trip took %s, past the %s budget",
			elapsed.Round(time.Millisecond), r.cfg.ProbeBudget)
	}

	st.OK = true
	r.save(st)

	// Retiring the previous probes is bookkeeping and not part of the result: a
	// probe that proved the round trip and then failed to tidy up has still
	// proved the round trip.
	r.retirePrevious(rel)
	return st, nil
}

// retirePrevious deletes every probe note except the current one.
//
// They are found through the daemon's own search on the marker tag, not by
// walking a directory, because the marker is the identity — the design is
// explicit that a probe is excluded by what it carries and not by where it
// sits, and a cleanup keyed on a path would quietly disagree with that the
// first time capture's shard rolled over into a new month.
func (r *Runner) retirePrevious(keep string) {
	res, err := r.call("memory_search", map[string]any{"query": Tag, "k": 50})
	if err != nil {
		return
	}
	rows, _ := res["results"].([]any)
	for _, row := range rows {
		m, ok := row.(map[string]any)
		if !ok {
			continue
		}
		rel, _ := m["path"].(string)
		if rel == "" || rel == keep {
			continue
		}
		abs := filepath.Join(r.cfg.VaultPath, filepath.FromSlash(rel))
		raw, err := os.ReadFile(abs)
		if err != nil {
			continue
		}
		// Confirm the marker on the file itself. The tag put this row in front of
		// us; the marker is what authorizes deleting it, and a note that merely
		// mentions the word is not a probe.
		if !note.Parse(rel, string(raw), time.Time{}).Probe {
			continue
		}
		if err := os.Remove(abs); err != nil {
			continue
		}
		if r.markPath != nil {
			r.markPath(rel)
		}
		_ = r.idx.Delete(rel)
	}
}

// AsHealth converts the recorded state into what the status surface reports.
func AsHealth(st State, ok bool) (health.ProbeState, time.Time) {
	if !ok {
		return health.ProbeState{}, time.Time{}
	}
	out := health.ProbeState{
		At:       st.At.UTC().Format(time.RFC3339),
		OK:       st.OK,
		Detail:   st.Detail,
		Path:     st.Path,
		Recorded: true,
	}
	if d, err := time.ParseDuration(st.Elapsed); err == nil {
		out.Elapsed = health.Duration(d)
	}
	return out, st.At
}

func (r *Runner) save(st State) {
	blob, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return
	}
	if err := os.MkdirAll(filepath.Dir(r.statePath), 0o755); err != nil {
		return
	}
	tmp := r.statePath + ".tmp"
	if err := os.WriteFile(tmp, append(blob, '\n'), 0o644); err != nil {
		return
	}
	_ = os.Rename(tmp, r.statePath)
}

// findPath issues one search and reports whether `want` came back and where.
func (r *Runner) findPath(query, want string) (bool, int, error) {
	res, err := r.call("memory_search", map[string]any{"query": query, "k": 5})
	if err != nil {
		return false, 0, err
	}
	rows, _ := res["results"].([]any)
	for i, row := range rows {
		m, ok := row.(map[string]any)
		if !ok {
			continue
		}
		if p, _ := m["path"].(string); p == want {
			return true, i + 1, nil
		}
	}
	return false, 0, nil
}

// call issues one MCP tools/call over the daemon's own HTTP surface — the same
// path a Claude Code session takes, which is the only reason this proves
// anything.
func (r *Runner) call(tool string, args map[string]any) (map[string]any, error) {
	payload, err := json.Marshal(map[string]any{
		"jsonrpc": "2.0",
		"id":      1,
		"method":  "tools/call",
		"params":  map[string]any{"name": tool, "arguments": args},
	})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequest(http.MethodPost, r.baseURL+"/mcp", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	resp, err := r.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d from %s: %s", resp.StatusCode, tool, strings.TrimSpace(string(raw)))
	}

	var env struct {
		Error *struct {
			Message string `json:"message"`
		} `json:"error"`
		Result struct {
			IsError           bool           `json:"isError"`
			StructuredContent map[string]any `json:"structuredContent"`
			Content           []struct {
				Text string `json:"text"`
			} `json:"content"`
		} `json:"result"`
	}
	if err := json.Unmarshal(raw, &env); err != nil {
		return nil, fmt.Errorf("undecodable response: %w", err)
	}
	if env.Error != nil {
		return nil, fmt.Errorf("%s", env.Error.Message)
	}
	if env.Result.IsError {
		msg := "tool reported an error"
		if len(env.Result.Content) > 0 {
			msg = env.Result.Content[0].Text
		}
		return nil, fmt.Errorf("%s", msg)
	}
	if env.Result.StructuredContent != nil {
		return env.Result.StructuredContent, nil
	}
	if len(env.Result.Content) > 0 {
		var m map[string]any
		if err := json.Unmarshal([]byte(env.Result.Content[0].Text), &m); err == nil {
			return m, nil
		}
	}
	return nil, fmt.Errorf("%s returned no structured result", tool)
}

// nonce is a token that has never appeared in the corpus before, so a search for
// it can only be answered by the note this run just wrote. Twelve hex characters
// from crypto/rand; math/rand would repeat across restarts on a fixed seed and a
// repeated nonce would let a stale index pass a probe.
func nonce() string {
	b := make([]byte, 6)
	if _, err := rand.Read(b); err != nil {
		// crypto/rand does not fail in practice, and a probe is better run with
		// a time-derived token than not run at all.
		return fmt.Sprintf("%012x", time.Now().UnixNano())
	}
	return hex.EncodeToString(b)
}
