package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Every production caller opens the index with the sidecar directory (plan
// 05: `.lifecycle.json` lives in the engine state directory). A call site that
// used the plain index.Open would read the legacy location only and rank the
// whole corpus off its fallback anchor — silently, which is why this is a
// test over the source rather than a note in a comment.
func TestEveryCommandOpensTheIndexWithTheSidecarDir(t *testing.T) {
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	var offenders []string
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		src, err := os.ReadFile(filepath.Join(".", name))
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(src), "index.Open(") {
			offenders = append(offenders, name)
		}
	}
	if len(offenders) > 0 {
		t.Fatalf("index.Open used in %v; production opens through index.OpenWithSidecar(..., cfg.EngineStateDir, ...)", offenders)
	}
}
