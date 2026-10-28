package dreaming

import (
	"path/filepath"
	"strings"
	"testing"
)

// A note whose file name holds a character a wikilink cannot carry is linked
// by a Markdown link to its percent-encoded vault path (task 193 LD-6). The
// wikilink `[[…/Talk [Septembe|…]]` closes at the first `]` and Obsidian draws
// it as a link to nothing, so the note stayed an orphan however often it was
// listed. scripts/test_vault_orphan_census.py resolves this exact form.
func TestABracketNamedNoteIsListedWithAMarkdownLink(t *testing.T) {
	notes := fixtureEntityNotes()
	notes["personal/talks/Talk [Septembe.md"] = "---\nupdated: 2026-09-21\n---\n\nDemoed github.com/alexherrero/crickets.\n"
	root, x := entityVault(t, notes)
	plan, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	var page string
	for _, in := range plan.Intents {
		if in.Rel == "memory/entities/repos/alexherrero-crickets.md" {
			page = string(in.After)
		}
	}
	want := "- 2026-09-21 · [Talk (Septembe](<personal/talks/Talk%20%5BSeptembe.md>)\n"
	if !strings.Contains(page, want) {
		t.Errorf("the crickets page lacks %q:\n%s", want, page)
	}
	if strings.Contains(page, "[[personal/talks/Talk [") {
		t.Errorf("the bracket-named note is still listed by a wikilink that cannot resolve:\n%s", page)
	}
	// Every other note keeps its wikilink.
	if !strings.Contains(page, "[[agent/memory/semantic/a-card|A card]]") {
		t.Errorf("a plain note lost its wikilink:\n%s", page)
	}
}

func TestNoteLinkEncodesEveryCharacterAWikilinkCannotCarry(t *testing.T) {
	for rel, want := range map[string]string{
		"notes/a|b.md":      "[a-b](<notes/a%7Cb.md>)",
		"notes/a#b.md":      "[a#b](<notes/a%23b.md>)",
		"notes/a^b.md":      "[a^b](<notes/a%5Eb.md>)",
		"notes/[x] y/z].md": "[z)](<notes/%5Bx%5D%20y/z%5D.md>)",
		"notes/plain.md":    "[[notes/plain|plain]]",
	} {
		if got := noteLink(mention{Rel: rel}); got != want {
			t.Errorf("noteLink(%q) = %q, want %q", rel, got, want)
		}
	}
}
