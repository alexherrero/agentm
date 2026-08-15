package rerank

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Everything here runs against an httptest server rather than a model, same
// discipline as package embed's tests: the claims under test are about the
// daemon's side of the contract, and a real llama-server would make each of
// them depend on a 600MB file and a warm machine.

func fakeServer(t *testing.T, handler http.HandlerFunc) *Client {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	return NewClient(srv.URL, 0)
}

// The server answers sorted by score and is free to reorder — the reply's own
// index field is what places each score. Trusting arrival order would
// mis-assign a high score to the wrong document, and the symptom would read
// as "the reranker disagrees with fusion" rather than as a decoding bug.
func TestRerankPlacesScoresByReportedIndex(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		// Deliberately reordered and out of input order: index 2 first.
		json.NewEncoder(w).Encode(map[string]any{
			"results": []map[string]any{
				{"index": 2, "relevance_score": 8.5},
				{"index": 0, "relevance_score": -3.1},
				{"index": 1, "relevance_score": 0.2},
			},
		})
	})
	got, err := c.Rerank(context.Background(), "q", []string{"a", "b", "c"})
	if err != nil {
		t.Fatalf("Rerank: %v", err)
	}
	want := []float64{-3.1, 0.2, 8.5}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("doc %d got %v, want %v — the reply was reassembled by "+
				"arrival order instead of by index", i, got[i], want[i])
		}
	}
}

func TestRerankRejectsShortReply(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{
			"results": []map[string]any{{"index": 0, "relevance_score": 1.0}},
		})
	})
	if _, err := c.Rerank(context.Background(), "q", []string{"a", "b"}); err == nil {
		t.Fatal("a reply with fewer scores than documents was accepted")
	}
}

// A duplicated index would leave another document with no score at all —
// silently defaulting it to zero would be indistinguishable from a real
// score near the floor.
func TestRerankRejectsDuplicateIndex(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{
			"results": []map[string]any{
				{"index": 0, "relevance_score": 1.0},
				{"index": 0, "relevance_score": 2.0},
			},
		})
	})
	if _, err := c.Rerank(context.Background(), "q", []string{"a", "b"}); err == nil {
		t.Fatal("a reply naming the same index twice was accepted")
	}
}

func TestRerankRejectsOutOfRangeIndex(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{
			"results": []map[string]any{{"index": 7, "relevance_score": 1.0}},
		})
	})
	if _, err := c.Rerank(context.Background(), "q", []string{"a"}); err == nil {
		t.Fatal("a reply naming an index outside the batch was accepted")
	}
}

// An HTTP error must surface, carrying the server's own message — the caller
// above cannot react to a failure it never sees.
func TestRerankSurfacesServerError(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error":{"message":"input (2092 tokens) is too large to process"}}`))
	})
	_, err := c.Rerank(context.Background(), "q", []string{"a"})
	if err == nil {
		t.Fatal("an HTTP 500 was accepted as a successful rerank")
	}
	if !strings.Contains(err.Error(), "too large") {
		t.Errorf("error %q dropped the server's own explanation", err)
	}
}

func TestRerankEmptyBatchReturnsNothing(t *testing.T) {
	c := NewClient("http://unused.invalid", 0)
	got, err := c.Rerank(context.Background(), "q", nil)
	if err != nil || got != nil {
		t.Fatalf("Rerank(nil) = %v, %v; want nil, nil without a request", got, err)
	}
}

func TestHealthyRejectsLoadingServer(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	})
	if err := c.Healthy(context.Background()); err == nil {
		t.Fatal("a still-loading server reported healthy")
	}
}

// Sigmoid is the scale conversion the floor is defined on. Pinned to
// hand-computed literals, not derived from the implementation's own formula —
// a test that reuses the code under test to build its expectation proves only
// that they agree.
func TestSigmoidKnownValues(t *testing.T) {
	cases := []struct {
		in, want float64
	}{
		{0, 0.5},
		{100, 1.0}, // saturates; must not overflow or NaN
		{-100, 0.0},
	}
	for _, c := range cases {
		got := Sigmoid(c.in)
		if math.Abs(got-c.want) > 1e-9 {
			t.Errorf("Sigmoid(%v) = %v, want %v", c.in, got, c.want)
		}
	}
	// Measured raw logits from the task-3 bake-off probe, both models: a
	// relevant pair must sigmoid above 0.5 and an irrelevant one below it,
	// which is the property the floor's placement above 0 in raw-logit terms
	// depends on.
	if Sigmoid(1.93) <= 0.5 {
		t.Error("a measured relevant-pair logit did not sigmoid above 0.5")
	}
	if Sigmoid(-11.04) >= 0.5 {
		t.Error("a measured irrelevant-pair logit did not sigmoid below 0.5")
	}
}

func TestSigmoidIsMonotonic(t *testing.T) {
	prev := Sigmoid(-10)
	for x := -9.0; x <= 10; x++ {
		cur := Sigmoid(x)
		if cur <= prev {
			t.Fatalf("Sigmoid not increasing at x=%v: %v <= %v", x, cur, prev)
		}
		prev = cur
	}
}

func TestLookupResolvesKnownModels(t *testing.T) {
	dir := t.TempDir()
	for _, name := range []string{
		"bge-reranker-v2-m3-Q8_0.gguf",
		"bge-reranker-v2-m3-Q8_0",
	} {
		m, err := Lookup(dir, name)
		if err != nil {
			t.Fatalf("Lookup(%q): %v", name, err)
		}
		if m.CtxTokens != 2048 {
			t.Errorf("%s: CtxTokens = %d, want 2048", name, m.CtxTokens)
		}
		if m.Path != filepath.Join(dir, "bge-reranker-v2-m3-Q8_0.gguf") {
			t.Errorf("%s: Path = %s", name, m.Path)
		}
	}
	if _, err := Lookup(dir, "some-other-model.gguf"); err == nil {
		t.Fatal("an unknown model was accepted; its context window would have to be guessed")
	}
}

// The two candidates must carry independent floors — a floor fitted to one
// model's logit scale is not a floor for the other.
func TestCatalogModelsHaveIndependentFloors(t *testing.T) {
	dir := t.TempDir()
	bge, err := Lookup(dir, "bge-reranker-v2-m3-Q8_0")
	if err != nil {
		t.Fatal(err)
	}
	jina, err := Lookup(dir, "jina-reranker-v2-base-multilingual-Q8_0")
	if err != nil {
		t.Fatal(err)
	}
	if bge.Name == jina.Name {
		t.Error("both models share a name")
	}
	for _, m := range []Model{bge, jina} {
		if m.Floor <= 0 || m.Floor >= 1 {
			t.Errorf("%s: Floor = %v, want a value strictly between 0 and 1 "+
				"(the scale Sigmoid produces)", m.Name, m.Floor)
		}
	}
}

func TestDiscoverIsDeterministic(t *testing.T) {
	dir := t.TempDir()
	for _, f := range []string{
		"bge-reranker-v2-m3-Q8_0.gguf",
		"jina-reranker-v2-base-multilingual-Q8_0.gguf",
	} {
		if err := os.WriteFile(filepath.Join(dir, f), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	first, ok := Discover(dir)
	if !ok {
		t.Fatal("Discover found nothing in a directory with two models")
	}
	for i := 0; i < 20; i++ {
		again, _ := Discover(dir)
		if again.Name != first.Name {
			t.Fatalf("Discover returned %s then %s", first.Name, again.Name)
		}
	}
}

func TestDiscoverFindsNothingInEmptyDir(t *testing.T) {
	if _, ok := Discover(t.TempDir()); ok {
		t.Fatal("Discover found a model in an empty directory")
	}
}

// A supervisor with no installed model is StateOff, not an error — a
// reranker-less install must keep serving hybrid search exactly as well as
// before this package existed.
func TestSupervisorWithoutModelIsOff(t *testing.T) {
	s := New(Options{Model: Model{Name: "absent", Path: filepath.Join(t.TempDir(), "nope.gguf")}})
	st, detail := s.State()
	if st != StateOff {
		t.Fatalf("state = %s (%s), want %s", st, detail, StateOff)
	}
	if s.Available() {
		t.Error("an absent model reports itself available")
	}
	if _, err := s.Rerank(context.Background(), "q", []string{"a"}); err == nil {
		t.Error("reranking against an absent model succeeded")
	}
	s.Start(context.Background())
	if err := s.Close(); err != nil {
		t.Errorf("Close: %v", err)
	}
}

// A server that answers /health with 200 while failing every rerank call must
// be declared degraded, not left reporting warm — the identical wedged-child
// pathology embed.Supervisor guards against, observed on the same class of
// process.
func TestRepeatedFailuresDegradeDespiteHealthyEndpoint(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/health") {
			w.WriteHeader(http.StatusOK) // wedged, and still claiming to be fine
			return
		}
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error":{"message":"Compute error."}}`))
	}))
	t.Cleanup(srv.Close)

	m, err := Lookup(t.TempDir(), "bge-reranker-v2-m3-Q8_0")
	if err != nil {
		t.Fatal(err)
	}
	s := Attach(srv.URL, m)

	if err := s.client.Healthy(context.Background()); err != nil {
		t.Fatalf("the fixture's /health should pass: %v", err)
	}

	for i := 0; i < failThreshold; i++ {
		if st, _ := s.State(); st != StateWarm {
			t.Fatalf("degraded after %d failures, want %d", i, failThreshold)
		}
		if _, err := s.Rerank(context.Background(), "q", []string{"a"}); err == nil {
			t.Fatal("a 500 was reported as success")
		}
	}

	st, detail := s.State()
	if st != StateDegraded {
		t.Fatalf("state = %s after %d consecutive failures, want %s", st, failThreshold, StateDegraded)
	}
	if !strings.Contains(detail, "unreranked") {
		t.Errorf("detail %q does not say what stopped working", detail)
	}
	if s.Available() {
		t.Error("a wedged reranker still reports itself available")
	}
}

// One failure must not condemn a healthy child — an oversized batch is
// routinely the caller's fault.
func TestOneFailureDoesNotDegrade(t *testing.T) {
	var calls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls == 1 {
			w.WriteHeader(http.StatusInternalServerError)
			w.Write([]byte(`{"error":{"message":"input is too large to process"}}`))
			return
		}
		json.NewEncoder(w).Encode(map[string]any{
			"results": []map[string]any{{"index": 0, "relevance_score": 1.0}},
		})
	}))
	t.Cleanup(srv.Close)

	m, _ := Lookup(t.TempDir(), "bge-reranker-v2-m3-Q8_0")
	s := Attach(srv.URL, m)

	if _, err := s.Rerank(context.Background(), "q", []string{"too long"}); err == nil {
		t.Fatal("the first call should have failed")
	}
	if st, _ := s.State(); st != StateWarm {
		t.Fatalf("state = %s after one failure, want still %s", st, StateWarm)
	}
	if _, err := s.Rerank(context.Background(), "q", []string{"fine"}); err != nil {
		t.Fatalf("second call: %v", err)
	}
	for i := 0; i < failThreshold-1; i++ {
		s.recordFailure(errTest)
	}
	if st, _ := s.State(); st != StateWarm {
		t.Fatalf("state = %s; the successful call did not reset the failure run", st)
	}
}

var errTest = errors.New("test")
