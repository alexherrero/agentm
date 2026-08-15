package embed

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

// Client speaks llama-server's embedding API over loopback.
//
// Two endpoints, both plain JSON: `GET /health` says whether the model finished
// loading, and `POST /embedding` returns one vector per input. Nothing here is
// llama.cpp-specific beyond those two shapes, which is deliberate — the daemon's
// dependency is an HTTP contract it can restart, not a library it links.
type Client struct {
	base string
	http *http.Client
}

// NewClient points a client at a base URL such as http://127.0.0.1:8899.
func NewClient(base string, timeout time.Duration) *Client {
	if timeout <= 0 {
		timeout = 120 * time.Second
	}
	return &Client{
		base: base,
		// No keep-alive tuning and no retry: a request that fails is a signal the
		// supervisor needs, and swallowing it here would hide a dead child behind
		// a slow search.
		http: &http.Client{Timeout: timeout},
	}
}

// Base is the server's URL, for reporting.
func (c *Client) Base() string { return c.base }

// Healthy reports whether the server has finished loading its model.
//
// llama-server answers /health with 503 while the weights are still loading, so
// "connection refused" and "not ready yet" are different conditions and the
// supervisor waits through both rather than restarting into a loop.
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
		return fmt.Errorf("embedder not ready (HTTP %d)", resp.StatusCode)
	}
	return nil
}

// embedResponse is llama-server's reply shape.
//
// The embedding is doubly nested — `[[float…]]` rather than `[float…]` — because
// the endpoint can return one vector per token when pooling is disabled. The
// daemon always runs it pooled, so there is exactly one row, but the wire format
// keeps the outer list either way and decoding it as a flat array fails at
// runtime rather than at compile time.
type embedResponse struct {
	Index     int         `json:"index"`
	Embedding [][]float64 `json:"embedding"`
}

// Embed returns one vector per input text, in input order.
//
// The server is free to answer out of order — it fans a batch across slots — so
// the response's own index field is what places each vector, not its position in
// the reply. Trusting arrival order here would mis-assign vectors to notes under
// exactly the concurrency the batch exists to exploit, and every symptom of that
// looks like "the model is bad at retrieval."
func (c *Client) Embed(ctx context.Context, texts []string) ([][]float32, error) {
	if len(texts) == 0 {
		return nil, nil
	}
	body, err := json.Marshal(map[string]any{"content": texts})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/embedding", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 256<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("embedder returned HTTP %d: %s", resp.StatusCode, snippet(raw))
	}

	var rows []embedResponse
	if err := json.Unmarshal(raw, &rows); err != nil {
		return nil, fmt.Errorf("embedder reply was not the expected shape: %w (%s)", err, snippet(raw))
	}
	if len(rows) != len(texts) {
		return nil, fmt.Errorf("asked for %d embeddings, got %d", len(texts), len(rows))
	}

	out := make([][]float32, len(texts))
	for _, r := range rows {
		if r.Index < 0 || r.Index >= len(texts) {
			return nil, fmt.Errorf("embedder returned index %d, outside the batch of %d", r.Index, len(texts))
		}
		if len(r.Embedding) == 0 || len(r.Embedding[0]) == 0 {
			return nil, fmt.Errorf("embedder returned an empty vector at index %d", r.Index)
		}
		v := make([]float32, len(r.Embedding[0]))
		for i, f := range r.Embedding[0] {
			v[i] = float32(f)
		}
		if out[r.Index] != nil {
			return nil, fmt.Errorf("embedder returned index %d twice", r.Index)
		}
		out[r.Index] = Normalize(v)
	}
	for i, v := range out {
		if v == nil {
			return nil, fmt.Errorf("embedder skipped index %d", i)
		}
	}
	return out, nil
}

// Normalize scales a vector to unit length so a dot product is a cosine.
//
// llama-server already normalizes by default, and this repeats it anyway: the
// flag is a server-side default that a future build or a stray argument can
// change, and the failure mode if it does is not an error but a similarity
// ordering that is subtly wrong. Normalizing an already-unit vector costs one
// pass and removes the dependency on someone else's default.
//
// A zero vector is returned unchanged rather than divided by zero. It means the
// model saw nothing embeddable, which is a real thing an empty note produces.
func Normalize(v []float32) []float32 {
	var sum float64
	for _, x := range v {
		sum += float64(x) * float64(x)
	}
	if sum == 0 {
		return v
	}
	inv := float32(1 / math.Sqrt(sum))
	for i := range v {
		v[i] *= inv
	}
	return v
}

func snippet(b []byte) string {
	const max = 200
	s := string(bytes.TrimSpace(b))
	if len(s) > max {
		return s[:max] + "…"
	}
	return s
}
