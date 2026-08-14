package rerank

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"time"
)

// Client speaks llama-server's rerank API over loopback.
//
// Two endpoints, both plain JSON: `GET /health` says whether the model
// finished loading, and `POST /v1/rerank` scores a batch of documents against
// one query. Nothing here is llama.cpp-specific beyond those two shapes,
// mirroring package embed's own client for the identical reason — the
// daemon's dependency is an HTTP contract it can restart, not a library it
// links.
type Client struct {
	base string
	http *http.Client
}

// NewClient points a client at a base URL such as http://127.0.0.1:8955.
func NewClient(base string, timeout time.Duration) *Client {
	if timeout <= 0 {
		timeout = 120 * time.Second
	}
	return &Client{
		base: base,
		// No keep-alive tuning and no retry, same reasoning as embed.Client: a
		// request that fails is a signal the supervisor needs, and
		// swallowing it here would hide a dead child behind a slow search.
		http: &http.Client{Timeout: timeout},
	}
}

// Base is the server's URL, for reporting.
func (c *Client) Base() string { return c.base }

// Healthy reports whether the server has finished loading its model. Same
// contract as embed.Client.Healthy: llama-server answers /health with 503
// while weights are loading, so "connection refused" and "not ready yet" are
// different conditions and the supervisor waits through both.
func (c *Client) Healthy(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/health", nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<12))
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("reranker not ready (HTTP %d)", resp.StatusCode)
	}
	return nil
}

type rerankResult struct {
	Index          int     `json:"index"`
	RelevanceScore float64 `json:"relevance_score"`
}

type rerankResponse struct {
	Results []rerankResult `json:"results"`
}

// Rerank scores every document against one query and returns one raw
// cross-encoder logit per document, in input order.
//
// The server answers sorted by score, with each result carrying the index of
// the document it scored — the same "reply is free to reorder, index is the
// only thing that places a value" contract embed.Client.Embed trusts for
// vectors, applied here to scores. An unindexed reply would silently
// mis-assign a high score to the wrong document, and every symptom of that
// looks like "the reranker disagrees with the embedder" rather than like a
// decoding bug.
//
// Scores are raw and unbounded, not 0..1: bge-reranker-v2-m3 and
// jina-reranker-v2 were both measured emitting logits well outside that
// range (bge +1.93/-11.04, jina +0.55/-3.74, on separate probes). Sigmoid
// converts to the scale the floor is defined on; this method deliberately
// does not do that conversion itself, so a caller inspecting a raw score
// cannot mistake it for an already-calibrated one.
func (c *Client) Rerank(ctx context.Context, query string, docs []string) ([]float64, error) {
	if len(docs) == 0 {
		return nil, nil
	}
	body, err := json.Marshal(map[string]any{"query": query, "documents": docs})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/v1/rerank", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("reranker returned HTTP %d: %s", resp.StatusCode, snippet(raw))
	}

	var parsed rerankResponse
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("reranker reply was not the expected shape: %w (%s)", err, snippet(raw))
	}
	if len(parsed.Results) != len(docs) {
		return nil, fmt.Errorf("asked to score %d documents, got %d results", len(docs), len(parsed.Results))
	}

	out := make([]float64, len(docs))
	seen := make([]bool, len(docs))
	for _, r := range parsed.Results {
		if r.Index < 0 || r.Index >= len(docs) {
			return nil, fmt.Errorf("reranker returned index %d, outside the batch of %d", r.Index, len(docs))
		}
		if seen[r.Index] {
			return nil, fmt.Errorf("reranker returned index %d twice", r.Index)
		}
		seen[r.Index] = true
		out[r.Index] = r.RelevanceScore
	}
	for i, ok := range seen {
		if !ok {
			return nil, fmt.Errorf("reranker skipped index %d", i)
		}
	}
	return out, nil
}

// Sigmoid maps a raw cross-encoder logit onto 0..1, the scale a Model's Floor
// is defined and compared on. See the package doc and Client.Rerank for why
// the conversion is not folded into the HTTP call itself.
func Sigmoid(x float64) float64 {
	return 1 / (1 + math.Exp(-x))
}

func snippet(b []byte) string {
	const max = 200
	s := string(bytes.TrimSpace(b))
	if len(s) > max {
		return s[:max] + "…"
	}
	return s
}
