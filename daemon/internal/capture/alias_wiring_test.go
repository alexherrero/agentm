package capture

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/extract"
)

// readNote returns the frontmatter line starting with `prefix` from the note the
// capture wrote, or "" when there is none.
func frontmatterLine(t *testing.T, vault, rel, prefix string) string {
	t.Helper()
	blob, err := os.ReadFile(filepath.Join(vault, rel))
	if err != nil {
		t.Fatalf("reading the captured note: %v", err)
	}
	for _, line := range strings.Split(string(blob), "\n") {
		if line == "---" && strings.HasPrefix(prefix, "---") {
			continue
		}
		if strings.HasPrefix(line, prefix) {
			return line
		}
		if line == "---" {
			// End of frontmatter — stop before scanning the body, or a body line
			// that happens to start with `aliases:` would answer instead.
			if strings.Contains(string(blob), "\n---\n") && strings.Index(string(blob), line) > 0 {
				break
			}
		}
	}
	return ""
}

// The unit tests prove the extractor derives the right set. This proves the set
// reaches the file, which is a different claim and the one that matters: an
// extractor wired to nothing is a very well-tested no-op.
func TestDerivedAliasesReachTheWrittenNote(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Title: "The filing contract",
		Text:  "The Open Knowledge Format (OKF) requires a type field, and the index carries idx_timestamp_desc.",
	})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}

	line := frontmatterLine(t, cp.cfg.VaultPath, res.Path, "aliases:")
	if line == "" {
		t.Fatal("the note carries no aliases line; extraction is wired to nothing")
	}
	for _, want := range []string{"OKF", "Open Knowledge Format", "idx_timestamp_desc", "timestamp"} {
		if !strings.Contains(line, want) {
			t.Errorf("aliases line is missing %q: %s", want, line)
		}
	}
}

// A caller who names an alias meant it, and the cap is what makes that
// load-bearing: losing a deliberate alias to a decomposed fragment of some
// identifier would be the wrong trade every time.
//
// The body here is written to blow the cap on its own, so the assertion is about
// what survives rather than about what happens to fit. An earlier version of
// this test asserted the supplied alias came *first* in the emitted line, which
// was the wrong claim — `cleanList` sorts on the way out, and that sort is worth
// keeping, since alphabetical frontmatter is what makes a diff readable. The
// ordering inside mergeAliases decides selection, not display.
func TestASuppliedAliasSurvivesTheCap(t *testing.T) {
	cp := newHarness(t)

	var body strings.Builder
	body.WriteString("Columns: ")
	for i := 0; i < 40; i++ {
		fmt.Fprintf(&body, "some_column_name_%c, ", 'a'+rune(i%26))
	}

	res, err := cp.Do(Request{
		Title:   "Schema",
		Text:    body.String(),
		Aliases: []string{"zzz-the-schema-i-meant"},
	})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}

	line := frontmatterLine(t, cp.cfg.VaultPath, res.Path, "aliases:")
	count := strings.Count(line, ",") + 1
	if count <= extract.MaxAliases-2 {
		t.Fatalf("the fixture did not reach the cap (%d aliases), so this test "+
			"proves nothing about surviving it: %s", count, line)
	}
	// Alphabetically last on purpose: a cap applied after the sort would drop it.
	if !strings.Contains(line, "zzz-the-schema-i-meant") {
		t.Errorf("the supplied alias was dropped by the cap: %s", line)
	}
}

// Nothing is invented. A note with no structure to surface gets no aliases, and
// no `aliases:` line at all — an empty list would be a claim that extraction ran
// and found nothing worth having, which is true but not worth a line of every
// note's frontmatter.
func TestPlainProseGetsNoAliasLine(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Title: "A preference",
		Text:  "Use Edit rather than Write for a file that already exists.",
	})
	if err != nil {
		t.Fatalf("capture: %v", err)
	}
	if line := frontmatterLine(t, cp.cfg.VaultPath, res.Path, "aliases:"); line != "" {
		t.Errorf("plain prose produced an aliases line: %s", line)
	}
}
