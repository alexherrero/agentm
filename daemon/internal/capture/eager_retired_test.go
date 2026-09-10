package capture

import (
	"go/parser"
	"go/token"
	"io/fs"
	"strings"
	"testing"
)

// A capture makes no model call, and the guarantee is structural rather than
// timed: this package does not reference enrichment at all.
//
// What this replaces was a timing test — capture twenty notes with an eager
// pass whose stub sleeps a second, and assert no capture waited. It measured a
// real property while the eager trigger existed. It cannot fail any more, and
// a test that cannot fail is worse than no test: it reads as coverage of a
// path that is gone.
//
// The import is the honest assertion. Re-add `FireEager` to the capture path
// and this goes red before any timing test would have to be scheduled, on
// every platform, in milliseconds.
func TestCaptureDoesNotReachEnrichment(t *testing.T) {
	fset := token.NewFileSet()
	pkgs, err := parser.ParseDir(fset, ".", func(fs.FileInfo) bool { return true }, parser.ImportsOnly)
	if err != nil {
		t.Fatalf("parsing this package: %v", err)
	}
	seen := false
	for name, pkg := range pkgs {
		if strings.HasSuffix(name, "_test") {
			continue
		}
		for path, file := range pkg.Files {
			if strings.HasSuffix(path, "_test.go") {
				continue
			}
			seen = true
			for _, imp := range file.Imports {
				if strings.Contains(imp.Path.Value, "/internal/enrich") {
					t.Errorf("%s imports enrichment; capture makes no model call", path)
				}
			}
		}
	}
	if !seen {
		t.Fatal("parsed no non-test source in this package; the check proved nothing")
	}
}
