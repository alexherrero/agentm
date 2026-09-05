// Package mcpsrv serves the daemon's only client-facing surface: two MCP tools
// over HTTP.
//
// Exactly two, deliberately. Sessions call memory_search and iterate — an agent
// searching a warm index in a loop beats a single blind lookup — instead of
// receiving a fixed ~80KB dump at session start that every session pays for
// whether it is relevant or not. Context flows by pull, not by push.
package mcpsrv

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/capture"
	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// protocolVersion is the MCP revision this surface implements.
const protocolVersion = "2025-06-18"

// StatusFunc returns whatever the daemon wants to report about itself.
type StatusFunc func() map[string]any

// ProbeFunc runs one self-probe now and returns its result. Nil when the daemon
// has no probe wired, in which case the endpoint says so rather than pretending
// to have run one.
type ProbeFunc func() (any, error)

// Server is the HTTP handler set.
type Server struct {
	cfg     *config.Config
	idx     *index.Index
	cap     *capture.Capturer
	log     *slog.Logger
	status  StatusFunc
	probe   ProbeFunc
	embed   EmbedFunc
	onWrite func(rel string)
	started time.Time
	version string
}

func New(cfg *config.Config, idx *index.Index, cp *capture.Capturer, log *slog.Logger,
	status StatusFunc, onWrite func(rel string), version string) *Server {
	return &Server{
		cfg: cfg, idx: idx, cap: cp, log: log, status: status,
		onWrite: onWrite, started: time.Now(), version: version,
	}
}

// SetProbe wires the on-demand self-probe. It is set after construction because
// the probe talks to this server over HTTP and therefore cannot exist until the
// listener does.
func (s *Server) SetProbe(fn ProbeFunc) { s.probe = fn }

// EmbedFunc turns a query into a vector and names the model that produced it. It
// returns a nil vector when there is no warm embedder, which is what makes a
// hybrid search degrade to its lexical arm rather than fail.
//
// It takes the terms query and, separately, whatever question the caller
// supplied (task 3.5) rather than a single already-chosen text, because only
// the function's owner (main.go, which holds the embedder and therefore knows
// its window) can decide which one to embed and how much of it fits. This
// package still knows nothing about child processes or their windows — it
// hands both strings across the seam and is told back a vector.
type EmbedFunc func(ctx context.Context, query, question string) ([]float32, string)

// SetEmbedder wires the vector arm. A function rather than the supervisor itself,
// so this package keeps knowing nothing about child processes — it asks for a
// vector and is told either a vector or nothing.
func (s *Server) SetEmbedder(fn EmbedFunc) { s.embed = fn }

// Handler wires the routes.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/mcp", s.handleMCP)
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/status", s.handleStatus)
	mux.HandleFunc("/probe", s.handleProbe)
	return s.localOnly(mux)
}

// localOnly refuses anything that did not come from this machine, and anything
// that reached this machine through a browser.
//
// The daemon holds the operator's entire memory, including scanned documents and
// financial records once his own spaces join the repository, so binding to
// loopback is where the reasoning starts rather than where it ends.
//
// Three checks, because a peer address alone is not enough. A web page the
// operator visits can make his browser issue requests to 127.0.0.1, and those
// arrive with a loopback peer address like any other — so the peer check passes
// and the page gets to drive the tool surface. DNS rebinding removes the
// remaining obstacle: the attacker's domain resolves to 127.0.0.1, the browser
// treats the daemon as same-origin, and responses become readable. `Origin` and
// `Host` are what distinguish those requests from a local client's, which is why
// the retired daemon's own doctor spec checked for exactly this.
//
// Deliberately not a bearer token. A token would additionally gate other
// processes running as the operator, and any such process can already read the
// vault files directly — so it would add setup friction for every client while
// closing nothing this does not close.
func (s *Server) localOnly(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !isLoopbackAddr(r.RemoteAddr) {
			http.Error(w, "local requests only", http.StatusForbidden)
			return
		}
		// A rebound request carries the attacker's hostname here, because that is
		// what the browser was told to connect to.
		if h := r.Host; h != "" && !isLoopbackHost(h) {
			http.Error(w, "unrecognized Host; local requests only", http.StatusForbidden)
			return
		}
		// A native client sends no Origin. A browser always does, and a browser
		// has no business here whatever it claims.
		if origin := r.Header.Get("Origin"); origin != "" && !isLoopbackOrigin(origin) {
			http.Error(w, "cross-origin requests are refused", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func isLoopbackAddr(remoteAddr string) bool {
	host := remoteAddr
	if h, _, err := net.SplitHostPort(remoteAddr); err == nil {
		host = h
	}
	return isLoopbackName(strings.Trim(host, "[]"))
}

func isLoopbackHost(hostHeader string) bool {
	host := hostHeader
	if h, _, err := net.SplitHostPort(hostHeader); err == nil {
		host = h
	}
	return isLoopbackName(strings.Trim(host, "[]"))
}

func isLoopbackOrigin(origin string) bool {
	u, err := url.Parse(origin)
	if err != nil {
		return false
	}
	return isLoopbackName(u.Hostname())
}

func isLoopbackName(host string) bool {
	if host == "localhost" {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return false
}

// ---------------------------------------------------------------------------
// JSON-RPC
// ---------------------------------------------------------------------------

type rpcRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type rpcResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Result  any             `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

func (s *Server) handleMCP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST a JSON-RPC request to this endpoint", http.StatusMethodNotAllowed)
		return
	}
	defer r.Body.Close()

	var req rpcRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, rpcResponse{
			JSONRPC: "2.0",
			Error:   &rpcError{Code: -32700, Message: "parse error: " + err.Error()},
		})
		return
	}

	// Notifications carry no id and get no body.
	if len(req.ID) == 0 && strings.HasPrefix(req.Method, "notifications/") {
		w.WriteHeader(http.StatusAccepted)
		return
	}

	resp := rpcResponse{JSONRPC: "2.0", ID: req.ID}
	switch req.Method {
	case "initialize":
		resp.Result = map[string]any{
			"protocolVersion": protocolVersion,
			"capabilities":    map[string]any{"tools": map[string]any{}},
			"serverInfo":      map[string]any{"name": "agentm", "version": s.version},
			"instructions": "Two tools. Search before you answer from assumption, and " +
				"iterate: try two or three distinct vocabularies for the same idea " +
				"before concluding the memory does not exist. Capture is instant and " +
				"free — it never waits on a model or the network.",
		}
	case "ping":
		resp.Result = map[string]any{}
	case "tools/list":
		contract, _ := s.cfg.Rules.Get()
		resp.Result = map[string]any{"tools": toolSpecs(contract)}
	case "tools/call":
		result, err := s.callTool(req.Params)
		if err != nil {
			resp.Result = toolError(err)
		} else {
			resp.Result = result
		}
	default:
		resp.Error = &rpcError{Code: -32601, Message: "unknown method " + req.Method}
	}
	writeJSON(w, http.StatusOK, resp)
}

type callParams struct {
	Name      string          `json:"name"`
	Arguments json.RawMessage `json:"arguments"`
}

func (s *Server) callTool(raw json.RawMessage) (any, error) {
	var p callParams
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, fmt.Errorf("bad params: %w", err)
	}
	switch p.Name {
	case "memory_search":
		return s.toolSearch(p.Arguments)
	case "memory_capture":
		return s.toolCapture(p.Arguments)
	default:
		return nil, fmt.Errorf("unknown tool %q; this daemon serves memory_search and memory_capture", p.Name)
	}
}

type searchArgs struct {
	Query  string `json:"query"`
	K      int    `json:"k"`
	After  string `json:"after"`
	Before string `json:"before"`
	// IncludeArchived is the contract's explicit archive query: an archived
	// memory has left everyday search and comes back only when asked for by
	// name. Published in the inputSchema below (filing v2 part 6).
	IncludeArchived bool `json:"include_archived"`
	// Mode is published in the inputSchema below as of the hook cutover
	// (task 5). It was accepted-but-hidden from task 1 onward: advertising it
	// earlier would have let the agent opt into a mode with 0% correct
	// rejection before the ladder had a rung that earned it. The rung that was
	// meant to earn it — a cross-encoder floor — was refuted (task 3); what
	// actually made this safe to publish is the fast path's own injection
	// policy (inject with metadata, never a manufactured empty — see the
	// prompt-submit hook), which does not depend on `mode` carrying any
	// rejection guarantee. `rerank` is deliberately not one of the published
	// enum values: the MCP surface has no reranker child wired to it, and
	// rerank does not ship to the hook either (refuted, task 3).
	Mode string `json:"mode"`
	// Question is published on the same seam as Mode, for the same reason and
	// as of the same cutover: a caller can ask the dense arm to embed the
	// natural question (task 3.5) instead of Query. Query keeps driving the
	// lexical arms unconditionally either way.
	Question string `json:"question"`
	// Lex3 is accepted on the same unpublished seam as Mode and Question: a
	// measurement driver can widen the lexical arm from 2-term to 2- and 3-term
	// subsets (task 4, column `+lex3`) before the ladder has earned it a place
	// the agent can see.
	Lex3 bool `json:"lex3"`
}

func (s *Server) toolSearch(raw json.RawMessage) (any, error) {
	var a searchArgs
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &a); err != nil {
			return nil, fmt.Errorf("bad arguments: %w", err)
		}
	}
	if strings.TrimSpace(a.Query) == "" {
		return nil, fmt.Errorf("query is required")
	}
	if a.K <= 0 {
		a.K = 5
	}
	if a.K > 50 {
		a.K = 50
	}
	q := index.Query{Text: a.Query, K: a.K, After: a.After, Before: a.Before, Mode: a.Mode, Lex3: a.Lex3,
		IncludeArchived: a.IncludeArchived}
	if a.Mode == index.ModeHybrid && s.embed != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		q.Vector, q.EmbedModel = s.embed(ctx, a.Query, a.Question)
	}
	out, err := s.idx.Search(q)
	if err != nil {
		return nil, err
	}
	return toolOK(out)
}

func (s *Server) toolCapture(raw json.RawMessage) (any, error) {
	var req capture.Request
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &req); err != nil {
			return nil, fmt.Errorf("bad arguments: %w", err)
		}
	}
	res, err := s.cap.Do(req)
	if err != nil {
		return nil, err
	}
	if s.onWrite != nil {
		s.onWrite(res.Path)
	}
	return toolOK(res)
}

// toolOK returns the MCP content envelope. The structured result is duplicated as
// JSON text because clients differ in which one they read, and a tool that is
// invisible to half of them is the failure this whole build exists to end.
func toolOK(v any) (any, error) {
	blob, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	var structured map[string]any
	if err := json.Unmarshal(blob, &structured); err != nil {
		structured = map[string]any{"result": json.RawMessage(blob)}
	}
	return map[string]any{
		"content":           []any{map[string]any{"type": "text", "text": string(blob)}},
		"structuredContent": structured,
		"isError":           false,
	}, nil
}

func toolError(err error) any {
	return map[string]any{
		"content": []any{map[string]any{"type": "text", "text": err.Error()}},
		"isError": true,
	}
}

// ---------------------------------------------------------------------------
// Tool declarations
// ---------------------------------------------------------------------------

// toolSpecs describes the two tools. The descriptions are load-bearing: the
// week-1 experiment found the two drivers split on search *behaviour* rather than
// on index quality — persistent-but-credulous against calibrated-but-quick — and
// the fix for that is a sentence in the tool description, not a system.
// The type enum is passed in rather than read from a constant: it comes from
// the filing contract, so a type the operator adds to standards/storage-rules.md
// is offered to the model on the next tools/list. A nil contract means it would
// not parse, and the enum is omitted — the tool still takes a type, and capture
// is what refuses an unvalidatable one, with the parse error attached.
func toolSpecs(r *rules.Rules) []map[string]any {
	typeSchema := map[string]any{
		"type":        "string",
		"description": "One of the six types.",
	}
	if r != nil {
		typeSchema["enum"] = r.TypesSorted()
		typeSchema["default"] = r.DefaultType
	}

	return []map[string]any{
		{
			"name": "memory_search",
			"description": strings.Join([]string{
				"Search the operator's durable memory: preferences, workflows, conventions, fixes, ideas, references, and project state.",
				"",
				"Ranking is BM25 over a full-text index with porter stemming, titles and aliases weighted above body text. Unfiled notes and miner fragments are ranked lower but never hidden — a demoted note that is the best thing the corpus has still comes back first.",
				"",
				"How to use it well:",
				"- Terms are ANDed. Every word must appear in the same note, so a long natural-language question usually matches nothing. Search two or three distinctive words instead.",
				"- Iterate. If the first phrasing comes back empty or thin, try a different vocabulary for the same idea — the note may have been written in words the question does not use.",
				"- Do not stop at the first plausible hit when the question deserves better, and answer \"nothing found\" only after two or three distinct vocabularies have failed.",
				"- For a question about when something happened or what was decided in a period, bound it with after/before. Episodic questions are time questions.",
				"- If the exact-terms default (mode \"and\") comes back empty or thin and you suspect a vocabulary gap rather than an absent fact, retry with mode \"hybrid\" and question set to the full natural-language question — it adds a dense-vector arm that can find a note with no term overlap at all. Results from any mode are candidates matched by similarity, not verified answers; judge them, don't just relay them.",
			}, "\n"),
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"query": map[string]any{
						"type":        "string",
						"description": "Two to five distinctive words. Not a sentence — terms are ANDed.",
					},
					"k": map[string]any{
						"type": "integer", "default": 5, "minimum": 1, "maximum": 50,
						"description": "How many results to return.",
					},
					"after": map[string]any{
						"type":        "string",
						"description": "Only notes captured on or after this date (YYYY-MM-DD or RFC3339).",
					},
					"before": map[string]any{
						"type":        "string",
						"description": "Only notes captured before this date (YYYY-MM-DD or RFC3339).",
					},
					"mode": map[string]any{
						"type": "string", "enum": []string{"and", "fusion", "hybrid"}, "default": "and",
						"description": "\"and\" (default) requires every query term in one note. \"fusion\" best-matches any subset of terms — looser than \"and\", still exact-word. \"hybrid\" adds a dense-vector arm on top of fusion, fused by reciprocal rank — set `question` alongside it so the vector arm embeds the real question rather than `query`'s bare terms.",
					},
					"include_archived": map[string]any{
						"type":        "boolean",
						"description": "Also return memories whose lifecycle is archived — the explicit archive query. Off by default: an archived memory has left everyday search while staying on disk, and comes back only when asked for by name.",
					},
					"question": map[string]any{
						"type":        "string",
						"description": "The full natural-language question, used only when mode is \"hybrid\": the dense arm embeds this instead of query. query keeps driving the exact-term arms unchanged either way.",
					},
				},
				"required": []string{"query"},
			},
		},
		{
			"name": "memory_capture",
			"description": strings.Join([]string{
				"Write one durable memory. Instant: it writes the file and updates the index in a single step, with no model call and no network, and it works offline. It is safe to call freely.",
				"",
				"Capture one concept per call. A research afternoon produces many small memories, each tagged, not one long summary — recall works by searching small addressable notes, and a large blob defeats that whether or not it contains the answer.",
				"",
				"Record what was learned, not what was said.",
				"",
				"Set status: \"active\" when the operator asked for this in the conversation — he has already approved it by asking. Leave it unset (it defaults to \"unfiled\") for anything captured unattended; unfiled notes are fully searchable and simply rank lower until filing promotes them.",
			}, "\n"),
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"text": map[string]any{
						"type":        "string",
						"description": "The fact, in plain prose. One concept.",
					},
					"title": map[string]any{
						"type":        "string",
						"description": "Short title. Derived from the text when omitted; the title is weighted 4x in ranking, so a good one is worth writing.",
					},
					"type": typeSchema,
					"status": map[string]any{
						"type": "string", "enum": []string{"unfiled", "active"}, "default": "unfiled",
						"description": "\"active\" for a capture the operator asked for; \"unfiled\" for anything unattended.",
					},
					"tags": map[string]any{
						"type": "array", "items": map[string]any{"type": "string"},
					},
					"aliases": map[string]any{
						"type": "array", "items": map[string]any{"type": "string"},
						"description": "Alternate phrasings someone might later search for. Indexed above the body — this is the direct fix for asking in words the note does not contain.",
					},
					"source": map[string]any{
						"type":        "string",
						"description": "URL or message-id, for anything ingested from outside.",
					},
					"space": map[string]any{
						"type": "string", "default": "memory",
						"description": "Which space to write into.",
					},
				},
				"required": []string{"text"},
			},
		},
	}
}

// ---------------------------------------------------------------------------
// Plain HTTP
// ---------------------------------------------------------------------------

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		// `status` carries the shape the doctor skill's liveness row already
		// checks for, so this daemon is a drop-in for the health contract the
		// retired one published rather than a second dialect of it.
		"status":  "ok",
		"ok":      true,
		"service": "agentm",
		"version": s.version,
		"uptime":  time.Since(s.started).Round(time.Second).String(),
	})
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	out := map[string]any{
		"service": "agentm",
		"version": s.version,
		"uptime":  time.Since(s.started).Round(time.Second).String(),
	}
	if s.status != nil {
		for k, v := range s.status() {
			out[k] = v
		}
	}
	writeJSON(w, http.StatusOK, out)
}

// handleProbe runs one self-probe on demand.
//
// POST, because it writes: the probe captures a synthetic note, so a GET that
// did this would be a side-effecting read and something would eventually
// prefetch it.
func (s *Server) handleProbe(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST to run a self-probe", http.StatusMethodNotAllowed)
		return
	}
	if s.probe == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"ok":     false,
			"detail": "this daemon has no self-probe wired",
		})
		return
	}
	res, err := s.probe()
	if err != nil {
		// The probe failing is a real answer, not a server error: the caller
		// asked whether the round trip works and the answer is no.
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": false, "detail": err.Error(), "result": res,
		})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "result": res})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	_ = enc.Encode(v)
}
