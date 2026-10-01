// Package people reads the operator's alias and deny table, and decides whether
// a name a note carries stands for a person, and which one (task 179).
//
// agentm-vault § Dreaming, "Entity pages are built each night": enrichment says
// who the people are — its deep pass returns `people:`, the people a note
// names — and the nightly entity builder finds where each one appears by
// matching their names across the vault. The table is the operator's hand on
// both. It lives at `standards/people/aliases.md`, outside the always-load
// glob so no session carries it, and it holds three lists in one fenced
// `people` block:
//
//	you:      the vault owner's own names; never a person on a page
//	aliases:  a person's full name and the other spellings that mean them
//	deny:     names that are not people — a product, a place, a model
//
// A missing file is an empty table: nothing is merged or denied, and the
// owner is left out only by the prompt's own instruction.
package people

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"gopkg.in/yaml.v3"
)

// RelPath is the table's place under the vault root.
const RelPath = "standards/people/aliases.md"

// Table is the operator's alias and deny table.
type Table struct {
	You     []string            `yaml:"you"`
	Aliases map[string][]string `yaml:"aliases"`
	Deny    []string            `yaml:"deny"`
}

var blockRe = regexp.MustCompile("(?s)```people\\s*\\n(.*?)\\n```")

// Load reads the table under vaultRoot. A missing file is an empty table.
func Load(vaultRoot string) (Table, error) {
	if vaultRoot == "" {
		return Table{}, nil
	}
	raw, err := os.ReadFile(filepath.Join(vaultRoot, filepath.FromSlash(RelPath)))
	if os.IsNotExist(err) {
		return Table{}, nil
	}
	if err != nil {
		return Table{}, err
	}
	return Parse(string(raw))
}

// Parse reads the table from the file's text: the one fenced `people` block.
// A file with no block is an empty table; a block that is not valid YAML is an
// error, so a typo is reported rather than read as "nobody".
func Parse(text string) (Table, error) {
	m := blockRe.FindStringSubmatch(strings.ReplaceAll(text, "\r\n", "\n"))
	if m == nil {
		return Table{}, nil
	}
	var t Table
	if err := yaml.Unmarshal([]byte(m[1]), &t); err != nil {
		return Table{}, fmt.Errorf("%s: the people block is not valid YAML: %w", RelPath, err)
	}
	return t, nil
}

// Clean folds a name's whitespace, trims quotes and stray punctuation, and
// returns "" for what cannot be a name at all.
func Clean(name string) string {
	name = strings.Join(strings.Fields(name), " ")
	name = strings.Trim(name, "\"'`.,;:()[]{}*_")
	if name == "" || strings.IndexFunc(name, unicode.IsLetter) < 0 {
		return ""
	}
	return name
}

func fold(s string) string { return strings.ToLower(Clean(s)) }

func contains(list []string, name string) bool {
	for _, v := range list {
		if fold(v) == name {
			return true
		}
	}
	return false
}

// Canonical is the name the table files a spelling under, and whether it is a
// person at all. The owner's names and denied names are not; an alias is its
// person's full name; any other name is itself.
func (t Table) Canonical(name string) (string, bool) {
	key := fold(name)
	if key == "" || contains(t.You, key) || contains(t.Deny, key) {
		return "", false
	}
	full := make([]string, 0, len(t.Aliases))
	for f := range t.Aliases {
		full = append(full, f)
	}
	sort.Strings(full)
	for _, f := range full {
		if fold(f) == key || contains(t.Aliases[f], key) {
			if contains(t.Deny, fold(f)) || contains(t.You, fold(f)) {
				return "", false
			}
			return Clean(f), true
		}
	}
	return Clean(name), true
}

// Spellings are the texts that stand for a canonical person: the name and, from
// the table, its aliases. Longest first, so a matcher tries the full name before
// a part of it.
func (t Table) Spellings(canonical string) []string {
	out := []string{Clean(canonical)}
	for f, aliases := range t.Aliases {
		if fold(f) != fold(canonical) {
			continue
		}
		for _, a := range aliases {
			if c := Clean(a); c != "" && !contains(out, fold(c)) {
				out = append(out, c)
			}
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return len(out[i]) > len(out[j]) })
	return out
}

// Text is a note's text prepared once for many lookups: its lower-case form
// and the set of words in it. Matching a vault's worth of notes against every
// person goes through it, so a note is lower-cased and split once rather than
// once per name.
type Text struct {
	raw, lower string
	words      map[string]bool
}

// NewText prepares text for Mentions.
func NewText(text string) Text {
	t := Text{raw: text, lower: strings.ToLower(text), words: map[string]bool{}}
	for _, w := range strings.FieldsFunc(t.lower, func(r rune) bool { return !isWordRune(r) }) {
		t.words[w] = true
	}
	return t
}

// Mentions reports whether text names the person under any of their spellings,
// as a whole word or phrase.
func Mentions(text string, spellings []string) bool {
	return NewText(text).Mentions(spellings)
}

// Mentions reports whether the text names the person under any of their
// spellings. A spelling of two words or more matches in any case. A one-word
// spelling matches only as written, so a person called Will or Mark is not
// found in every "will" and "mark" — and never when it is a month, a weekday
// or one of the common words that are also names, which a sentence capitalizes
// as often as a person's name ("May retro", "Will you ship it?").
func (t Text) Mentions(spellings []string) bool {
	for _, s := range spellings {
		s = Clean(s)
		if s == "" {
			continue
		}
		lower := strings.ToLower(s)
		// Every word of the spelling must be in the text before a search for
		// the phrase is worth making; most notes fail here for free.
		present := true
		for _, w := range strings.FieldsFunc(lower, func(r rune) bool { return !isWordRune(r) }) {
			if !t.words[w] {
				present = false
				break
			}
		}
		if !present {
			continue
		}
		if !strings.Contains(s, " ") {
			if commonWords[lower] {
				continue
			}
			if containsWord(t.raw, s) {
				return true
			}
			continue
		}
		if containsWord(t.lower, lower) {
			return true
		}
	}
	return false
}

// commonWords are the one-word spellings that never name a person on their
// own: the months, the weekdays, and the common words that are also given
// names. A full name carrying them still matches ("May Chen").
var commonWords = map[string]bool{
	"january": true, "february": true, "march": true, "april": true, "may": true,
	"june": true, "july": true, "august": true, "september": true, "october": true,
	"november": true, "december": true, "monday": true, "tuesday": true,
	"wednesday": true, "thursday": true, "friday": true, "saturday": true,
	"sunday": true, "will": true, "mark": true, "grace": true, "hope": true,
	"faith": true, "joy": true, "rose": true, "bill": true, "art": true,
	"jack": true, "frank": true, "summer": true, "dawn": true, "rob": true,
	"sue": true, "chase": true, "drew": true, "max": true, "page": true,
	"ray": true, "victor": true, "sky": true, "lane": true,
}

// containsWord finds needle in hay with no letter, digit or underscore on
// either side, reading the characters around it as Unicode: a typographic
// apostrophe, an em dash or an ellipsis ends a word.
func containsWord(hay, needle string) bool {
	for from := 0; ; {
		i := strings.Index(hay[from:], needle)
		if i < 0 {
			return false
		}
		start, end := from+i, from+i+len(needle)
		before, _ := utf8.DecodeLastRuneInString(hay[:start])
		after, _ := utf8.DecodeRuneInString(hay[end:])
		if (start == 0 || !isWordRune(before)) && (end == len(hay) || !isWordRune(after)) {
			return true
		}
		from = start + 1
	}
}

func isWordRune(r rune) bool {
	return r == '_' || unicode.IsLetter(r) || unicode.IsDigit(r)
}

// Ground keeps the names a pass returned that stand for a person the note's own
// text names: each is cleaned, filed under its canonical name, dropped when the
// table says it is the owner or no person, and dropped when neither it nor its
// canonical name's spellings appear in text. Sorted and deduplicated, so the
// field a note carries does not move between two passes that agree.
//
// This is the check the design names "the grounding judge" for names, made
// deterministic and free: a name the note does not contain was inferred, and an
// inferred person is exactly what must not earn a page.
func (t Table) Ground(text string, names []string) []string {
	prepared := NewText(text)
	seen := map[string]bool{}
	var out []string
	for _, n := range names {
		clean := Clean(n)
		canonical, ok := t.Canonical(clean)
		if !ok {
			continue
		}
		if !prepared.Mentions([]string{clean}) && !prepared.Mentions(t.Spellings(canonical)) {
			continue
		}
		if key := fold(canonical); !seen[key] {
			seen[key] = true
			out = append(out, canonical)
		}
	}
	sort.Strings(out)
	return out
}
