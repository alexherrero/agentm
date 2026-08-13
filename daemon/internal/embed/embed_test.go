package embed

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

// Everything here runs against an httptest server rather than a model. The
// claims being tested are about the daemon's side of the contract — how a reply
// is reassembled, what happens when it is malformed — and a real llama-server
// would make each of them depend on a 300MB file and a warm machine.

func fakeServer(t *testing.T, handler http.HandlerFunc) *Client {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	return NewClient(srv.URL, 0)
}

// The server fans a batch across slots and is free to answer out of order. The
// reply's own index field is what places each vector — trusting arrival order
// would mis-assign vectors to notes under exactly the concurrency the batch
// exists to exploit, and every symptom of that looks like "the model is bad at
// retrieval" rather than like a bug.
func TestEmbedPlacesVectorsByReportedIndex(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		// Deliberately reversed: index 2 first, index 0 last.
		json.NewEncoder(w).Encode([]map[string]any{
			{"index": 2, "embedding": [][]float64{{0, 0, 1}}},
			{"index": 1, "embedding": [][]float64{{0, 1, 0}}},
			{"index": 0, "embedding": [][]float64{{1, 0, 0}}},
		})
	})

	got, err := c.Embed(context.Background(), []string{"first", "second", "third"})
	if err != nil {
		t.Fatalf("Embed: %v", err)
	}
	want := [][]float32{{1, 0, 0}, {0, 1, 0}, {0, 0, 1}}
	for i := range want {
		for j := range want[i] {
			if got[i][j] != want[i][j] {
				t.Fatalf("input %d got %v, want %v — the reply was reassembled by "+
					"arrival order instead of by index", i, got[i], want[i])
			}
		}
	}
}

func TestEmbedRejectsShortReply(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]map[string]any{
			{"index": 0, "embedding": [][]float64{{1, 0, 0}}},
		})
	})
	if _, err := c.Embed(context.Background(), []string{"a", "b"}); err == nil {
		t.Fatal("a reply with fewer vectors than inputs was accepted")
	}
}

// A duplicated index would leave another input with no vector at all. Silently
// storing a nil would write an empty blob against a real note.
func TestEmbedRejectsDuplicateIndex(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]map[string]any{
			{"index": 0, "embedding": [][]float64{{1, 0, 0}}},
			{"index": 0, "embedding": [][]float64{{0, 1, 0}}},
		})
	})
	if _, err := c.Embed(context.Background(), []string{"a", "b"}); err == nil {
		t.Fatal("a reply naming the same index twice was accepted")
	}
}

func TestEmbedRejectsOutOfRangeIndex(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]map[string]any{
			{"index": 7, "embedding": [][]float64{{1, 0, 0}}},
		})
	})
	if _, err := c.Embed(context.Background(), []string{"a"}); err == nil {
		t.Fatal("a reply naming an index outside the batch was accepted")
	}
}

// An HTTP error must surface, carrying the server's own message. The
// too-large-to-process 500 is how an oversized note announces itself, and the
// retry ladder above this cannot react to an error it never sees.
func TestEmbedSurfacesServerError(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error":{"message":"input (2092 tokens) is too large to process"}}`))
	})
	_, err := c.Embed(context.Background(), []string{"a"})
	if err == nil {
		t.Fatal("an HTTP 500 was accepted as a successful embedding")
	}
	if !strings.Contains(err.Error(), "too large") {
		t.Errorf("error %q dropped the server's own explanation", err)
	}
}

func TestEmbedNormalizesToUnitLength(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		// Deliberately un-normalized: llama-server normalizes by default, and
		// that default is someone else's to change.
		json.NewEncoder(w).Encode([]map[string]any{
			{"index": 0, "embedding": [][]float64{{3, 4, 0}}},
		})
	})
	got, err := c.Embed(context.Background(), []string{"a"})
	if err != nil {
		t.Fatalf("Embed: %v", err)
	}
	// 3-4-5 triangle: the unit vector is exactly (0.6, 0.8, 0).
	if math.Abs(float64(got[0][0])-0.6) > 1e-6 || math.Abs(float64(got[0][1])-0.8) > 1e-6 {
		t.Fatalf("got %v, want (0.6, 0.8, 0)", got[0])
	}
}

func TestNormalizeLeavesZeroVectorAlone(t *testing.T) {
	v := Normalize([]float32{0, 0, 0})
	for i, x := range v {
		if x != 0 || math.IsNaN(float64(x)) {
			t.Fatalf("component %d is %v; a zero vector must not be divided by its own length", i, x)
		}
	}
}

func TestHealthyRejectsLoadingServer(t *testing.T) {
	c := fakeServer(t, func(w http.ResponseWriter, r *http.Request) {
		// llama-server answers 503 while the weights load.
		w.WriteHeader(http.StatusServiceUnavailable)
	})
	if err := c.Healthy(context.Background()); err == nil {
		t.Fatal("a still-loading server reported healthy")
	}
}

func TestLookupResolvesKnownModels(t *testing.T) {
	dir := t.TempDir()
	for _, name := range []string{
		"embeddinggemma-300M-Q8_0.gguf",
		"embeddinggemma-300M-Q8_0",
	} {
		m, err := Lookup(dir, name)
		if err != nil {
			t.Fatalf("Lookup(%q): %v", name, err)
		}
		if m.Dim != 768 {
			t.Errorf("%s: Dim = %d, want 768", name, m.Dim)
		}
		if m.Path != filepath.Join(dir, "embeddinggemma-300M-Q8_0.gguf") {
			t.Errorf("%s: Path = %s", name, m.Path)
		}
	}
	if _, err := Lookup(dir, "some-other-model.gguf"); err == nil {
		t.Fatal("an unknown model was accepted; its dimension would have to be guessed")
	}
}

// The two candidates must not share a dimension, or a vector written by one and
// read as the other would pass the width check that exists to catch exactly that.
func TestCatalogModelsAreDistinguishable(t *testing.T) {
	dir := t.TempDir()
	gemma, err := Lookup(dir, "embeddinggemma-300M-Q8_0")
	if err != nil {
		t.Fatal(err)
	}
	qwen, err := Lookup(dir, "Qwen3-Embedding-0.6B-Q8_0")
	if err != nil {
		t.Fatal(err)
	}
	if gemma.Dim == qwen.Dim {
		t.Errorf("both models report %d dimensions; the width check cannot tell them apart", gemma.Dim)
	}
	if gemma.Name == qwen.Name {
		t.Error("both models share a name")
	}
	// Both are asymmetric retrieval models: the query side must be scaffolded
	// differently from the document side, or the training's distinction is
	// discarded and recall quietly drops.
	for _, m := range []Model{gemma, qwen} {
		if m.WrapQuery("x") == m.WrapDoc("x") {
			t.Errorf("%s wraps queries and documents identically", m.Name)
		}
		if !strings.Contains(m.WrapQuery("needle"), "needle") {
			t.Errorf("%s dropped the query text", m.Name)
		}
	}
}

// Discovery must be deterministic. Two machines that resolved different models
// from the same directory would build indexes nobody can compare.
func TestDiscoverIsDeterministic(t *testing.T) {
	dir := t.TempDir()
	for _, f := range []string{
		"embeddinggemma-300M-Q8_0.gguf",
		"Qwen3-Embedding-0.6B-Q8_0.gguf",
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

// A supervisor with no installed model is StateOff, not an error. An install
// without weights is the lexical-only daemon that shipped before this, and it
// has to keep working exactly as well.
func TestSupervisorWithoutModelIsOff(t *testing.T) {
	s := New(Options{Model: Model{Name: "absent", Path: filepath.Join(t.TempDir(), "nope.gguf")}})
	st, detail := s.State()
	if st != StateOff {
		t.Fatalf("state = %s (%s), want %s", st, detail, StateOff)
	}
	if s.Available() {
		t.Error("an absent model reports itself available")
	}
	if _, err := s.EmbedQuery(context.Background(), "x"); err == nil {
		t.Error("embedding against an absent model succeeded")
	}
	// Start and Close must both be safe no-ops in this state.
	s.Start(context.Background())
	if err := s.Close(); err != nil {
		t.Errorf("Close: %v", err)
	}
}

// A model whose weights return the wrong width must be refused rather than
// stored. Vectors of an unexpected dimension are not a weak signal; they are a
// different geometry, and they surface hundreds of notes later.
func TestEmbedRejectsWrongDimension(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/health") {
			w.WriteHeader(http.StatusOK)
			return
		}
		json.NewEncoder(w).Encode([]map[string]any{
			{"index": 0, "embedding": [][]float64{{1, 0, 0}}}, // 3 dims, catalog says 768
		})
	}))
	t.Cleanup(srv.Close)

	m, err := Lookup(t.TempDir(), "embeddinggemma-300M-Q8_0")
	if err != nil {
		t.Fatal(err)
	}
	s := Attach(srv.URL, m)
	if !s.Available() {
		t.Fatal("an attached supervisor is not available")
	}
	_, err = s.EmbedQuery(context.Background(), "x")
	if err == nil {
		t.Fatal("a 3-dimension vector was accepted for a 768-dimension model")
	}
	if !strings.Contains(err.Error(), "768") {
		t.Errorf("error %q does not say what width was expected", err)
	}
}

// A server that answers /health with 200 while failing every embedding must be
// declared degraded, not left reporting warm.
//
// This is the observed failure, not a hypothetical: a llama-server whose backend
// faults keeps serving `{"status":"ok"}` from /health and returns 500 "Compute
// error" for a five-token input. A supervisor that trusted /health would report
// `embedder ok (warm)` indefinitely while every search silently fell back to its
// lexical arm — the quietly-missing capability the status surface exists to
// prevent.
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

	m, err := Lookup(t.TempDir(), "embeddinggemma-300M-Q8_0")
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
		if _, err := s.EmbedQuery(context.Background(), "x"); err == nil {
			t.Fatal("a 500 was reported as success")
		}
	}

	st, detail := s.State()
	if st != StateDegraded {
		t.Fatalf("state = %s after %d consecutive failures, want %s", st, failThreshold, StateDegraded)
	}
	if !strings.Contains(detail, "lexical-only") {
		t.Errorf("detail %q does not say what stopped working", detail)
	}
	if s.Available() {
		t.Error("a wedged embedder still reports itself available")
	}
}

// One failure must not condemn a healthy child — an oversized note is routinely
// the caller's fault, and the retry ladder above is about to shorten it.
func TestOneFailureDoesNotDegrade(t *testing.T) {
	var calls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls == 1 {
			w.WriteHeader(http.StatusInternalServerError)
			w.Write([]byte(`{"error":{"message":"input is too large to process"}}`))
			return
		}
		json.NewEncoder(w).Encode([]map[string]any{
			{"index": 0, "embedding": [][]float64{make([]float64, 768)}},
		})
	}))
	t.Cleanup(srv.Close)

	m, _ := Lookup(t.TempDir(), "embeddinggemma-300M-Q8_0")
	s := Attach(srv.URL, m)

	if _, err := s.EmbedQuery(context.Background(), "too long"); err == nil {
		t.Fatal("the first call should have failed")
	}
	if st, _ := s.State(); st != StateWarm {
		t.Fatalf("state = %s after one failure, want still %s", st, StateWarm)
	}
	// A success must clear the run, so an occasional oversized note never
	// accumulates into a condemnation across an entire backfill.
	if _, err := s.EmbedQuery(context.Background(), "fine"); err != nil {
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

// Attach must apply the model's query scaffolding, same as a spawned child. A
// measurement run that attached to a warm server and skipped the prefix would be
// measuring a different model from the one the daemon ships.
func TestAttachAppliesQueryPrefix(t *testing.T) {
	var seen struct{ Content []string }
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&seen)
		json.NewEncoder(w).Encode([]map[string]any{
			{"index": 0, "embedding": [][]float64{make([]float64, 768)}},
		})
	}))
	t.Cleanup(srv.Close)

	m, err := Lookup(t.TempDir(), "embeddinggemma-300M-Q8_0")
	if err != nil {
		t.Fatal(err)
	}
	s := Attach(srv.URL, m)
	if _, err := s.EmbedQuery(context.Background(), "needle"); err != nil {
		t.Fatalf("EmbedQuery: %v", err)
	}
	if len(seen.Content) != 1 {
		t.Fatalf("server saw %d inputs, want 1", len(seen.Content))
	}
	if !strings.HasPrefix(seen.Content[0], m.QueryPrefix) {
		t.Errorf("server saw %q, which does not carry the query prefix %q",
			seen.Content[0], m.QueryPrefix)
	}
}
