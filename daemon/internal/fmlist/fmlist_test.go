package fmlist

import (
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

func yamlList(t *testing.T, text, key string) []string {
	t.Helper()
	end := strings.Index(text[4:], "\n---")
	var fm map[string]any
	if err := yaml.Unmarshal([]byte(text[4:4+end]), &fm); err != nil {
		t.Fatalf("frontmatter does not parse: %v\n%s", err, text)
	}
	var out []string
	switch v := fm[key].(type) {
	case []any:
		for _, x := range v {
			out = append(out, x.(string))
		}
	case string:
		out = []string{v}
	}
	return out
}

// Items reads what a YAML reader reads, in every shape the vault holds.
func TestItemsAgreesWithAYAMLReader(t *testing.T) {
	for _, note := range []string{
		"---\naliases: [\"when the gate fails, who is told\", plain one]\n---\n\nbody\n",
		"---\naliases: [\"the \\\"dreaming\\\" loop\", 'it''s here']\n---\n\nbody\n",
		"---\naliases:\n  - EnterWorktree\n  - \"isolation.mode: worktree-per-plan\"\nslug: x\n---\n\nbody\n",
		"---\naliases: single\n---\n\nbody\n",
		"---\ntitle: t\n---\n\naliases: [in the body, not a field]\n",
	} {
		if got, want := Items(note, "aliases"), yamlList(t, note, "aliases"); !reflect.DeepEqual(got, want) {
			t.Errorf("Items = %q, a YAML reader says %q\n%s", got, want, note)
		}
	}
}

// Quote round-trips through a YAML reader, comma and quote included.
func TestQuoteRoundTripsThroughYAML(t *testing.T) {
	items := []string{"when the gate fails, who is told", `the "dreaming" loop`, `back\slash`,
		"isolation.mode: worktree-per-plan", "plain", "- leading dash", "it's"}
	quoted := make([]string, len(items))
	for i, s := range items {
		quoted[i] = Quote(s)
	}
	note := "---\naliases: [" + strings.Join(quoted, ", ") + "]\n---\n\nbody\n"
	if got := yamlList(t, note, "aliases"); !reflect.DeepEqual(got, items) {
		t.Fatalf("YAML reads %q, want %q\n%s", got, items, note)
	}
	if got := Items(note, "aliases"); !reflect.DeepEqual(got, items) {
		t.Fatalf("Items reads %q, want %q", got, items)
	}
}

// Replace and Remove take a block list whole and never touch the body.
func TestReplaceAndRemoveTakeTheWholeKeyAndOnlyTheFrontmatter(t *testing.T) {
	note := "---\ntitle: t\nconsolidated_into:\n  - \"[[a]]\"\n  - \"[[b]]\"\nproject: p\n---\n\n```yaml\nconsolidated_into: \"[[example]]\"\n```\n"
	got, ok := Replace(note, "consolidated_into", `consolidated_into: ["[[a]]", "[[b]]", "[[c]]"]`)
	if !ok || strings.Contains(got, "  - \"[[a]]\"") || !strings.Contains(got, "consolidated_into: \"[[example]]\"\n```") {
		t.Fatalf("Replace:\n%s", got)
	}
	if got, ok := Remove(note, "consolidated_into"); !ok || strings.Contains(got, "[[a]]") ||
		!strings.Contains(got, "title: t\nproject: p\n") {
		t.Fatalf("Remove:\n%s", got)
	}
	if _, ok := Replace("---\ntitle: t\n---\n\nconsolidated_into: x\n", "consolidated_into", "x"); ok {
		t.Fatal("a body line is not a field")
	}
	if b := Block(note, "consolidated_into"); b != "consolidated_into:\n  - \"[[a]]\"\n  - \"[[b]]\"" {
		t.Fatalf("Block = %q", b)
	}
}
