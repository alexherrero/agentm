// Package embed runs the local embedding model as a supervised child process and
// speaks its HTTP API.
//
// The daemon never links an inference engine. Every credible one is cgo, and
// `CGO_ENABLED=0` is what makes the static binary, the toolchain-free CI build,
// and the NAS cross-compile work — so the model runs as a `llama-server` child
// on loopback, health-checked and restarted by this package, and a crash degrades
// retrieval visibly instead of taking the committer, the watcher, or the gate
// down with it.
package embed

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Model is one pinned embedding model: where its weights are, what shape its
// output is, and the prompt scaffolding it was trained with.
//
// The prefixes are not decoration. Both candidates are asymmetric retrieval
// models — they were trained with a query wrapped one way and a document wrapped
// another, and embedding both sides identically throws away the distinction the
// training built. Getting this wrong does not error; it quietly costs recall,
// which is the kind of defect a bake-off would blame on the model.
type Model struct {
	// Name is the identifier stored beside every vector. A vector embedded by a
	// different model is not comparable to this one, so the name is what makes a
	// model swap detectable rather than silently wrong.
	Name string
	// Path is the GGUF file. Empty means the model is not installed.
	Path string
	// Dim is the embedding width, asserted against what the server returns.
	Dim int
	// CtxTokens is what the child is launched with, and therefore the point at
	// which a long note is truncated. Reported, never silently absorbed.
	CtxTokens int
	// QueryPrefix and DocPrefix wrap the two sides of an asymmetric pair.
	QueryPrefix string
	DocPrefix   string
}

// Installed reports whether this model's weights are actually on disk.
func (m Model) Installed() bool {
	if m.Path == "" {
		return false
	}
	fi, err := os.Stat(m.Path)
	return err == nil && !fi.IsDir()
}

// WrapQuery and WrapDoc apply the asymmetric scaffolding.
func (m Model) WrapQuery(s string) string { return m.QueryPrefix + s }
func (m Model) WrapDoc(s string) string   { return m.DocPrefix + s }

// catalog is every model this daemon knows how to drive, keyed by the GGUF's
// base filename. Adding one is a code change on purpose: the dimension, the
// context window, and the prompt scaffolding are facts about the weights, and a
// config file that could assert them wrongly would produce vectors that are
// merely bad rather than absent.
var catalog = map[string]Model{
	"embeddinggemma-300M-Q8_0.gguf": {
		Name:      "embeddinggemma-300M-Q8_0",
		Dim:       768,
		CtxTokens: 2048,
		// Google's documented retrieval format for EmbeddingGemma. The document
		// side carries a title slot; the daemon has one per note and fills it.
		QueryPrefix: "task: search result | query: ",
		DocPrefix:   "title: none | text: ",
	},
	"Qwen3-Embedding-0.6B-Q8_0.gguf": {
		Name: "Qwen3-Embedding-0.6B-Q8_0",
		Dim:  1024,
		// The weights support 32k. This is 2048 because the batch buffers are
		// sized from it, and larger ones fault on Apple Metal: at `-b/-ub 8192`
		// this model died six requests into an idle machine with 37GB free, with
		// `kIOGPUCommandBufferCallbackErrorPageFault`, and llama.cpp says of that
		// state "recreate the backend to recover" — the process never serves
		// again. 4096 faulted the same way one request later.
		//
		// Recorded rather than raised, because it is the whole reason this model
		// lost the bake-off: its advantage over EmbeddingGemma was a longer
		// window, and a window whose compute buffer faults is not a window. Worth
		// re-testing on other hardware; this is a property of the pairing, not of
		// the weights.
		CtxTokens: 2048,
		// Qwen3-Embedding takes an instruction on the query side only and the
		// document raw. The instruction is the one from the model card's
		// retrieval example, kept verbatim — a paraphrase of it is a different
		// prompt and measures as a different model.
		QueryPrefix: "Instruct: Given a search query, retrieve relevant notes that answer the query\nQuery: ",
		DocPrefix:   "",
	},
}

// DefaultModelDir is where install.sh puts the weights. Resolved from $HOME
// rather than written as a literal, for the same reason no vault path is ever a
// constant here.
func DefaultModelDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".local", "share", "agentm", "models")
}

// Lookup resolves a model by GGUF filename or by the bare name, against a
// directory of weights.
//
// An unknown filename is an error rather than a guess. Guessing a dimension
// would produce a table of vectors that are the wrong width, which surfaces
// hundreds of notes later as a cosine that cannot be computed.
func Lookup(dir, name string) (Model, error) {
	if name == "" {
		return Model{}, fmt.Errorf("no embedding model named")
	}
	base := filepath.Base(name)
	m, ok := catalog[base]
	if !ok {
		// Accept the bare catalog name too, so a config can say
		// `embeddinggemma-300M-Q8_0` without the extension.
		for file, cand := range catalog {
			if cand.Name == strings.TrimSuffix(base, ".gguf") {
				m, base, ok = cand, file, true
				break
			}
		}
	}
	if !ok {
		return Model{}, fmt.Errorf(
			"unknown embedding model %q; this build knows %s", name, strings.Join(Known(), ", "))
	}
	if filepath.IsAbs(name) {
		m.Path = name
	} else {
		m.Path = filepath.Join(dir, base)
	}
	return m, nil
}

// Known lists the catalog, sorted, for error messages.
func Known() []string {
	out := make([]string, 0, len(catalog))
	for _, m := range catalog {
		out = append(out, m.Name)
	}
	sort.Strings(out)
	return out
}

// Discover picks the installed model from a directory when the operator has not
// named one.
//
// Ties are broken by the catalog's sort order rather than by whatever the
// filesystem returns first, so two installed models resolve the same way on
// every machine — a daemon that silently chose a different embedder per host
// would make two vaults' indexes incomparable for no visible reason.
func Discover(dir string) (Model, bool) {
	if dir == "" {
		return Model{}, false
	}
	var found []Model
	for file, m := range catalog {
		m.Path = filepath.Join(dir, file)
		if m.Installed() {
			found = append(found, m)
		}
	}
	if len(found) == 0 {
		return Model{}, false
	}
	sort.Slice(found, func(i, j int) bool { return found[i].Name < found[j].Name })
	return found[0], true
}
