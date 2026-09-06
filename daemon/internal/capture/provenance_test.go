package capture

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// Capture is where source provenance enters the corpus, and the corpus is what
// the source registry is rebuilt from. A memory that names its source without
// saying what that source contained lets a rebuild recover the name and not the
// skip decision — which is a registry that has to re-read everything it can name.
func TestCaptureWritesSourceProvenance(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Text:          "the decision we reached",
		Title:         "a decision",
		Source:        "email:<abc@example.com>",
		SourceHash:    "abc123def456",
		SourceVersion: "ingest/1",
	})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}
	body := readNote(t, cp, res.Path)

	// Read back through the parser the rebuild uses. `yamlScalar` quotes a value
	// containing a colon and every one of these has one, so asserting raw bytes
	// would be asserting the encoder rather than the contract.
	n := note.Parse(res.Path, body, time.Time{})
	for field, got := range map[string]string{
		"source":         n.Source,
		"source_hash":    n.SourceHash,
		"source_version": n.SourceVersion,
	} {
		want := map[string]string{
			"source":         "email:<abc@example.com>",
			"source_hash":    "abc123def456",
			"source_version": "ingest/1",
		}[field]
		if got != want {
			t.Errorf("%s = %q, want %q:\n%s", field, got, want, body)
		}
	}
}

// A hash with nothing to hash names no unit, and a rebuild reading one would
// recover a row keyed on nothing.
func TestProvenanceIsOnlyWrittenAlongsideASource(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Text: "something nobody sourced", Title: "a thought",
		SourceHash: "abc123", SourceVersion: "ingest/1",
	})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}
	body := readNote(t, cp, res.Path)

	for _, absent := range []string{"source_hash:", "source_version:"} {
		if strings.Contains(body, absent) {
			t.Errorf("a note with no source carries %q:\n%s", absent, body)
		}
	}
}

// A capture with a source and no provenance is the ordinary case for everything
// the corpus already holds, and it must not sprout empty fields.
func TestASourceWithoutProvenanceWritesNeitherField(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Text: "from a page", Title: "a page", Source: "https://example.com/a",
	})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}
	body := readNote(t, cp, res.Path)

	if got := note.Parse(res.Path, body, time.Time{}).Source; got != "https://example.com/a" {
		t.Errorf("source = %q:\n%s", got, body)
	}
	for _, absent := range []string{"source_hash:", "source_version:"} {
		if strings.Contains(body, absent) {
			t.Errorf("an empty %q was written:\n%s", absent, body)
		}
	}
}

func readNote(t *testing.T, cp *Capturer, rel string) string {
	t.Helper()
	blob, err := os.ReadFile(filepath.Join(cp.cfg.VaultPath, filepath.FromSlash(rel)))
	if err != nil {
		t.Fatalf("reading %s: %v", rel, err)
	}
	return string(blob)
}

// One field, one question (the provenance ruling of 2026-09-06): `source:`
// says how the material arrived, `source_id:` / `source_url:` say what it
// was. A capture that names both writes both, and the parser the rebuild
// uses resolves the unit from the reference field.
func TestCaptureWritesTheReferenceInItsOwnField(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Text:          "what the page said",
		Title:         "a fetched page",
		Source:        "external-fetch",
		SourceURL:     "https://example.com/page",
		SourceHash:    "abc123def456",
		SourceVersion: "ingest/1",
	})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}
	body := readNote(t, cp, res.Path)
	if !strings.Contains(body, "source: external-fetch\n") {
		t.Errorf("the transport is not in `source:`:\n%s", body)
	}
	// The address is asserted through the parser rather than as raw bytes:
	// `yamlScalar` quotes a value containing a colon, and every URL has one.
	if !strings.Contains(body, "source_url:") {
		t.Errorf("no `source_url:` line at all:\n%s", body)
	}
	if !strings.Contains(body, "trust: untrusted\n") {
		t.Errorf("a fetched page earns the contract's tier for its transport:\n%s", body)
	}
	n := note.Parse(res.Path, body, time.Time{})
	if n.Source != "https://example.com/page" {
		t.Errorf("the unit resolves to %q, so a rebuild would key the registry "+
			"on the transport rather than the page", n.Source)
	}
	if n.SourceHash != "abc123def456" || n.SourceVersion != "ingest/1" {
		t.Errorf("the hash and version did not survive alongside the reference: %q/%q",
			n.SourceHash, n.SourceVersion)
	}
}

// A registry identity takes the same route, and the hash rides with it even
// though `source:` holds a transport rather than the unit.
func TestCaptureWritesASourceIdentityBesideItsTransport(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Text:       "what the thread said",
		Title:      "a thread",
		Source:     "email",
		SourceID:   "email:<abc@example.com>",
		SourceHash: "deadbeef",
	})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}
	body := readNote(t, cp, res.Path)
	n := note.Parse(res.Path, body, time.Time{})
	if n.Source != "email:<abc@example.com>" {
		t.Errorf("the unit resolves to %q", n.Source)
	}
	if n.SourceHash != "deadbeef" {
		t.Errorf("the hash did not ride with the identity: %q", n.SourceHash)
	}
	if !strings.Contains(body, "trust: untrusted\n") {
		t.Errorf("`email` is an untrusted transport in the contract:\n%s", body)
	}
}
