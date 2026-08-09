// Package mcpsrv serves the daemon's only client-facing surface: two MCP tools
// over HTTP.
//
// Exactly two, deliberately. Sessions call memory_search and iterate — an agent
// searching a warm index in a loop beats a single blind lookup — instead of
// receiving a fixed ~80KB dump at session start that every session pays for
// whether it is relevant or not. Context flows by pull, not by push.
package mcpsrv

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/capture"
	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
)

// protocolVersion is the MCP revision this surface implements.
const protocolVersion = "2025-06-18"

// StatusFunc returns whatever the daemon wants to report about itself.
type StatusFunc func() map[string]any

// Server is the HTTP handler set.
type Server struct {
	cfg     *config.Config
	idx     *index.Index
	cap     *capture.Capturer
	log     *slog.Logger
	status  StatusFunc
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

// Handler wires the routes.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/mcp", s.handleMCP)
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/status", s.handleStatus)
	return s.localOnly(mux)
}

// localOnly refuses anything that did not come from this machine. The daemon
// holds the operator's entire memory, including scanned documents and financial
// records once his own spaces join the repository; it has no business being
// reachable from the network, and binding to loopback alone is not something to
// rely on a single flag for.
func (s *Server) localOnly(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host := r.RemoteAddr
		if i := strings.LastIndex(host, ":"); i > 0 {
			host = host[:i]
		}
		host = strings.Trim(host, "[]")
		if host != "127.0.0.1" && host != "::1" && host != "localhost" {
			http.Error(w, "local requests only", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
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
		resp.Result = map[string]any{"tools": toolSpecs()}
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
	out, err := s.idx.Search(index.Query{
		Text: a.Query, K: a.K, After: a.After, Before: a.Before,
	})
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
func toolSpecs() []map[string]any {
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
					"type": map[string]any{
						"type": "string", "enum": config.Types, "default": config.DefaultType,
						"description": "One of the six types.",
					},
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

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	_ = enc.Encode(v)
}
