package crystallize

// Task 182's release review: a block-list stamp, a stamp line quoted in the
// body, and an instrument offered as a crystallize source.

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

func yamlFrontmatter(t *testing.T, note string) map[string]any {
	t.Helper()
	end := strings.Index(note[4:], "\n---")
	out := map[string]any{}
	if err := yaml.Unmarshal([]byte(note[4:4+end]), &out); err != nil {
		t.Fatalf("frontmatter does not parse: %v\n%s", err, note)
	}
	return out
}

// A card whose stamp is a YAML block list (Obsidian's property editor writes
// lists that way) gains a second lesson: both must be named and it must parse.
func TestReleaseReviewStampOnBlockListKeepsBothAndParses(t *testing.T) {
	card := "---\ntitle: A card\nconsolidated_into:\n  - \"[[lesson-a]]\"\nproject: p\n---\n\nbody\n"
	if got := StampedLessons(card); !reflect.DeepEqual(got, []string{"lesson-a"}) {
		t.Errorf("StampedLessons(block list) = %q, want [lesson-a]", got)
	}
	out := Stamp(card, "lesson-b")
	_ = yamlFrontmatter(t, out) // fails the test if the YAML no longer parses
	if got := StampedLessons(out); !reflect.DeepEqual(got, []string{"lesson-a", "lesson-b"}) {
		t.Errorf("after stamping a block list, lessons = %q, want both:\n%s", got, out)
	}
}

// A stamp lives in the frontmatter. A card whose body quotes a stamp line (a
// note about crystallize itself) is not stamped, and stamping it must write
// the frontmatter, not rewrite the quoted body line.
func TestReleaseReviewStampIgnoresABodyLine(t *testing.T) {
	card := "---\ntitle: How crystallize stamps\nproject: p\n---\n\nThe stamp looks like:\n\n```yaml\nconsolidated_into: \"[[example-lesson]]\"\n```\n"
	if got := StampedLessons(card); len(got) != 0 {
		t.Errorf("StampedLessons read the body: %q", got)
	}
	out := Stamp(card, "real-lesson")
	fm := yamlFrontmatter(t, out)
	if fm["consolidated_into"] == nil {
		t.Errorf("no stamp in the frontmatter; body rewritten instead:\n%s", out)
	}
	if !strings.Contains(out, "consolidated_into: \"[[example-lesson]]\"\n```") {
		t.Errorf("the quoted body line was rewritten:\n%s", out)
	}
}

// #823: an `instrument:` note is "left byte for byte" — a measurement reads it.
// Crystallize gathers sources and then Stamps them; an instrument must not be
// a candidate source.
func TestReleaseReviewInstrumentIsNotACrystallizeSource(t *testing.T) {
	root := t.TempDir()
	dir := root + "/memory/semantic"
	if err := writeFile182(dir+"/eval-canary.md", "---\ntitle: eval canary\ninstrument: retrieval eval canary\nlifecycle: pinned\ntags: [retrieval, eval]\n---\n\nThe canary token zqxcanary proves the retrieval index answers.\n"); err != nil {
		t.Fatal(err)
	}
	cards, _ := cardsAndTraces(root, root)
	for _, c := range cards {
		if strings.HasSuffix(c.Rel, "eval-canary.md") {
			t.Fatalf("the instrument is a crystallize source (would be stamped consolidated_into): %+v", c.Rel)
		}
	}
}

func writeFile182(p, s string) error {
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	return os.WriteFile(p, []byte(s), 0o644)
}
