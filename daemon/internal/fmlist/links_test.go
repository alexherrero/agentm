package fmlist

import (
	"reflect"
	"testing"
)

// The link-valued keys' parser (task 193 LD-5). The cases match
// scripts/test_note_links.py's.
func TestLinksReadsEveryForm(t *testing.T) {
	for _, c := range []struct {
		value string
		want  []Link
	}{
		{"memory/semantic/x.md", []Link{{Target: "memory/semantic/x.md"}}},
		{"x", []Link{{Target: "x"}}},
		{`"[[x]]"`, []Link{{Target: "x"}}},
		{`"[[agent/memory/semantic/x|label]]"`, []Link{{Target: "agent/memory/semantic/x"}}},
		{`"[[x#heading]]"`, []Link{{Target: "x"}}},
		{`["[[a]]", "[[b|B]]"]`, []Link{{Target: "a"}, {Target: "b"}}},
		{`['memory/a.md', 'memory/b.md']`, []Link{{Target: "memory/a.md"}, {Target: "memory/b.md"}}},
		{`[a, b]`, []Link{{Target: "a"}, {Target: "b"}}},
		{`[[a]]`, []Link{{Target: "a"}}},   // unquoted: YAML's nested list, read as the link it meant
		{`[[[a]]]`, []Link{{Target: "a"}}}, // a flow list of one unquoted wikilink
		{"src-1 at v3", []Link{{Target: "src-1 at v3", Source: true}}},
		{`"src-1 at v3"`, []Link{{Target: "src-1 at v3", Source: true}}},
		{"", nil},
		{"   ", nil},
	} {
		if got := Links(c.value); !reflect.DeepEqual(got, c.want) {
			t.Errorf("Links(%q) = %+v, want %+v", c.value, got, c.want)
		}
	}
}

func TestLinksOfReadsTheKeyInEveryListShape(t *testing.T) {
	text := "---\ntitle: x\nrelated: [\"[[a]]\", \"[[b|B]]\"]\nderived_from:\n  - \"[[agent/memory/semantic/c]]\"\n  - memory/semantic/d.md\n" +
		"superseded_by: \"[[e]]\"\nsupersedes: src-1 at v3\n---\n\nrelated: [[not-frontmatter]]\n"
	for key, want := range map[string][]Link{
		"related":       {{Target: "a"}, {Target: "b"}},
		"derived_from":  {{Target: "agent/memory/semantic/c"}, {Target: "memory/semantic/d.md"}},
		"superseded_by": {{Target: "e"}},
		"supersedes":    {{Target: "src-1 at v3", Source: true}},
		"absent":        nil,
	} {
		if got := LinksOf(text, key); !reflect.DeepEqual(got, want) {
			t.Errorf("LinksOf(%s) = %+v, want %+v", key, got, want)
		}
	}
}

func TestLinkStemMemoryRelAndFirst(t *testing.T) {
	wiki, bare := Link{Target: "agent/memory/semantic/x"}, Link{Target: "memory/semantic/x.md"}
	if wiki.Stem() != "x" || bare.Stem() != "x" || (Link{Target: "x"}).Stem() != "x" {
		t.Errorf("stems: %q %q", wiki.Stem(), bare.Stem())
	}
	for _, l := range []Link{wiki, bare} {
		if got := l.MemoryRel("agent"); got != "memory/semantic/x.md" {
			t.Errorf("%+v.MemoryRel(agent) = %q", l, got)
		}
	}
	if got := (Link{Target: "memory/semantic/x"}).MemoryRel(""); got != "memory/semantic/x.md" {
		t.Errorf("flat vault MemoryRel = %q", got)
	}
	if l, ok := First([]Link{{Target: "id at v", Source: true}, {Target: "y"}}); !ok || l.Target != "y" {
		t.Errorf("First skipped nothing: %+v %v", l, ok)
	}
	if _, ok := First([]Link{{Target: "id at v", Source: true}}); ok {
		t.Errorf("First found a note in a source version")
	}
}

// A writer's value (task 193 step 7) reads back as the note it names.
func TestPathLinkAndLinkValueRoundTrip(t *testing.T) {
	if got := PathLink("agent/memory/semantic/x.md"); got != "[[agent/memory/semantic/x|x]]" {
		t.Errorf("PathLink = %q", got)
	}
	if got := PathLink("personal/Talk [Sept].md"); got != "personal/Talk [Sept].md" {
		t.Errorf("an unlinkable path = %q, want the bare path", got)
	}
	one := LinkValue(PathLink("agent/memory/semantic/x.md"))
	if one != `"[[agent/memory/semantic/x|x]]"` {
		t.Errorf("one = %s", one)
	}
	two := LinkValue(PathLink("agent/memory/semantic/a.md"), PathLink("agent/memory/semantic/b.md"))
	if two != `["[[agent/memory/semantic/a|a]]", "[[agent/memory/semantic/b|b]]"]` {
		t.Errorf("two = %s", two)
	}
	if LinkValue() != "" || LinkValue("  ") != "" {
		t.Errorf("no link should render empty")
	}
	text := "---\nsuperseded_by: " + two + "\n---\n"
	got := LinksOf(text, "superseded_by")
	if len(got) != 2 || got[0].MemoryRel("agent") != "memory/semantic/a.md" || got[1].MemoryRel("agent") != "memory/semantic/b.md" {
		t.Errorf("round trip: %+v", got)
	}
}
