package index

import (
	"database/sql"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/cardshape"
)

// The exact-name rule (task 179, the operator's ruling of 2026-09-30).
//
// An entity page lists every note that mentions one repository, issue, release
// or person, which makes it long, and a long page loses to short ones under
// BM25's length normalization. Measured on the live index the day the pages
// were built: the 576 issue and release pages each carry their repository's
// name, and "alexherrero/crickets" put the repository's own page 107th — no
// shorter title, compacter page or higher bar brought it inside the top ten.
// So a query that is an entity's name, or asks about one in so many words —
// "what do I know about crickets", "who is Jane Doe", "what shipped in agentm
// v10.0.0" — puts that entity's page first, the way a file-name match would.
// Every other query ranks exactly as before: the rule looks up a name, it does
// not weigh anything.

// namedLeadIns are the question frames the rule reads a name out of. The first
// that prefixes the query is removed; what is left must be the whole name.
var namedLeadIns = []string{
	"what do i know about", "what do we know about", "what do you know about",
	"what does the vault know about", "tell me about", "who is", "who's",
	"what is", "what's", "what shipped in", "what was in", "what changed in",
	"what happened in", "what happened with", "what happened to", "show me",
	"about",
}

// foldName is a name as the rule compares it: lower case, one space between
// words, no surrounding quotes or closing punctuation.
func foldName(s string) string {
	s = strings.ToLower(strings.Join(strings.Fields(s), " "))
	return strings.Trim(s, " ?.!,;:\"'`")
}

// QueryName is the name a query asks about: the query itself, or what follows
// one of the question frames, with a leading "the" dropped.
func QueryName(query string) string {
	q := foldName(query)
	for _, lead := range namedLeadIns {
		if strings.HasPrefix(q, lead+" ") {
			q = strings.TrimSpace(q[len(lead):])
			break
		}
	}
	return foldName(strings.TrimPrefix(q, "the "))
}

// isEntityPagePath is a path the builder writes: `…/entities/<folder>/<slug>.md`.
func isEntityPagePath(rel string) bool {
	parts := strings.Split(filepath.ToSlash(rel), "/")
	if len(parts) < 3 || !strings.HasSuffix(rel, ".md") || parts[len(parts)-3] != "entities" {
		return false
	}
	for _, folder := range cardshape.EntityFolders {
		if parts[len(parts)-2] == folder {
			return true
		}
	}
	return false
}

// entityNamesLocked maps every name an indexed entity page answers to — its
// title, its aliases and its id without the namespace — to the page. A name two
// pages share names neither. Read from the pages on disk and kept until an
// entity page is written or removed. Callers hold x.mu.
func (x *Index) entityNamesLocked() map[string]string {
	if x.entityNames != nil {
		return x.entityNames
	}
	names := map[string]string{}
	clash := map[string]bool{}
	rows, err := x.db.Query(`SELECT path FROM docmeta WHERE path LIKE '%/entities/%/%.md'`)
	if err != nil {
		return names
	}
	var paths []string
	for rows.Next() {
		var p string
		if rows.Scan(&p) == nil && isEntityPagePath(p) {
			paths = append(paths, p)
		}
	}
	rows.Close()
	for _, p := range paths {
		raw, err := os.ReadFile(filepath.Join(x.vault, filepath.FromSlash(p)))
		if err != nil {
			continue
		}
		fields, lists := entityFrontmatter(string(raw))
		if fields["kind"] != cardshape.EntityProfileKind {
			continue
		}
		candidates := append([]string{fields["title"]}, lists["aliases"]...)
		if _, id, ok := strings.Cut(fields["entity_id"], ":"); ok && fields["entity_type"] != "person" {
			candidates = append(candidates, id)
		}
		for _, c := range candidates {
			n := foldName(c)
			if n == "" {
				continue
			}
			if prev, seen := names[n]; seen && prev != p {
				clash[n] = true
			}
			names[n] = p
		}
	}
	for n := range clash {
		delete(names, n)
	}
	x.entityNames = names
	return names
}

// entityFrontmatter reads the scalar fields and flow lists of a page's block.
func entityFrontmatter(text string) (map[string]string, map[string][]string) {
	fields, lists := map[string]string{}, map[string][]string{}
	if !strings.HasPrefix(text, "---\n") {
		return fields, lists
	}
	end := strings.Index(text[4:], "\n---")
	if end < 0 {
		return fields, lists
	}
	for _, line := range strings.Split(text[4:4+end], "\n") {
		key, value, ok := strings.Cut(line, ":")
		if !ok || strings.HasPrefix(line, " ") {
			continue
		}
		key, value = strings.TrimSpace(key), strings.TrimSpace(value)
		if strings.HasPrefix(value, "[") && strings.HasSuffix(value, "]") {
			for _, item := range strings.Split(value[1:len(value)-1], ",") {
				if item = unquoteYAML(strings.TrimSpace(item)); item != "" {
					lists[key] = append(lists[key], item)
				}
			}
			continue
		}
		fields[key] = unquoteYAML(value)
	}
	return fields, lists
}

// unquoteYAML reads a double-quoted value the way the builder writes one (JSON
// quoting), and strips single quotes.
func unquoteYAML(v string) string {
	if len(v) >= 2 && v[0] == '"' && v[len(v)-1] == '"' {
		var s string
		if json.Unmarshal([]byte(v), &s) == nil {
			return s
		}
		return v[1 : len(v)-1]
	}
	return strings.Trim(v, "'")
}

// PutNamedEntityFirst puts the entity page a query names at the head of rows,
// moving it up when the search found it and adding it when it did not, and
// keeps at most k rows. A query that names no entity comes back untouched.
func (x *Index) PutNamedEntityFirst(query string, rows []Result, k int) []Result {
	name := QueryName(query)
	if name == "" {
		return rows
	}
	x.mu.Lock()
	path, ok := x.entityNamesLocked()[name]
	x.mu.Unlock()
	if !ok {
		return rows
	}
	for i, r := range rows {
		if r.Path == path {
			if i == 0 {
				return rows
			}
			out := append([]Result{r}, rows[:i]...)
			return append(out, rows[i+1:]...)
		}
	}
	page, err := x.resultFor(path)
	if err != nil {
		return rows
	}
	one := []Result{page}
	x.fillHeads(one)
	page = one[0]
	if len(rows) > 0 {
		page.Score = rows[0].Score
	}
	out := append([]Result{page}, rows...)
	if k > 0 && len(out) > k {
		out = out[:k]
	}
	return out
}

// resultFor is a row for one indexed note the ranking did not produce: the
// address and the stamps; the head is filled like any served row's.
func (x *Index) resultFor(path string) (Result, error) {
	x.mu.Lock()
	defer x.mu.Unlock()
	var r Result
	err := x.db.QueryRow(`SELECT id, path, flags, captured, captured_src, updated, created, project
		FROM docmeta WHERE path = ?`, path).Scan(&r.rowid, &r.Path, &r.Penalty, &r.Captured,
		&r.CapturedSource, &r.Updated, &r.Created, &r.project)
	if errors.Is(err, sql.ErrNoRows) {
		return r, err
	}
	return r, err
}
