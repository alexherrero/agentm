package enrich

import (
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

func yamlAliases(t *testing.T, note string) []string {
	t.Helper()
	end := strings.Index(note[4:], "\n---")
	var fm struct {
		Aliases []string `yaml:"aliases"`
	}
	if err := yaml.Unmarshal([]byte(note[4:4+end]), &fm); err != nil {
		t.Fatalf("frontmatter does not parse: %v\n%s", err, note)
	}
	return fm.Aliases
}

// Task 182's release review: the alias merge split a quoted alias on its comma,
// escaped a quote twice, and dropped a block-list stamp on rewrite.

// A capture-time alias is the asker's phrasing; capture writes it JSON-quoted
// (capture.quoteList). A comma inside it must not split it in two.
func TestReleaseReviewAliasWithCommaSurvivesRewrite(t *testing.T) {
	previous := "---\ntype: reference\nstatus: active\n" +
		`aliases: ["when the gate fails, who is told", "plain one"]` +
		"\n---\n\nbody\n"
	next := RenderNote(Response{Title: "Gate", Type: "reference", Confidence: 0.9, Body: "body"}, Stamp{})
	out := CarryProvenance(previous, next)
	want := []string{"when the gate fails, who is told", "plain one"}
	if got := yamlAliases(t, out); !reflect.DeepEqual(got, want) {
		t.Fatalf("aliases after rewrite = %q, want %q\n%s", got, want, out)
	}
}

// An alias holding a double quote (capture escapes it as \") must survive a
// rewrite unchanged, and a second rewrite must not change it again.
func TestReleaseReviewAliasWithQuoteIsStable(t *testing.T) {
	previous := "---\ntype: reference\nstatus: active\n" +
		`aliases: ["the \"dreaming\" loop"]` + "\n---\n\nbody\n"
	next := RenderNote(Response{Title: "Gate", Type: "reference", Confidence: 0.9, Body: "body"}, Stamp{})
	once := CarryProvenance(previous, next)
	want := []string{`the "dreaming" loop`}
	if got := yamlAliases(t, once); !reflect.DeepEqual(got, want) {
		t.Fatalf("after one rewrite aliases = %q, want %q\n%s", got, want, once)
	}
}

// consolidated_into joined carriedFields in task 182. A card whose stamp is a
// block list (the same shape mergeAliases is written to read) must keep it.
func TestReleaseReviewBlockListStampIsCarried(t *testing.T) {
	previous := "---\ntype: reference\nstatus: active\nconsolidated_into:\n  - \"[[lesson-a]]\"\n  - \"[[lesson-b]]\"\n---\n\nbody\n"
	next := RenderNote(Response{Title: "Card", Type: "reference", Confidence: 0.9, Body: "body"}, Stamp{})
	out := CarryProvenance(previous, next)
	if !strings.Contains(out, "lesson-a") || !strings.Contains(out, "lesson-b") {
		t.Fatalf("block-list consolidated_into dropped by the rewrite:\n%s", out)
	}
}
