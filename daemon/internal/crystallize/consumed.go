package crystallize

// What a lesson has already consumed (task 190 step 6).
//
// A card that taught a lesson is stamped `consolidated_into` and is never read
// again. A tracker cannot take the stamp, and neither can a trace's candidate
// line, so the closed tasks that taught a lesson clustered again every week:
// stopped only by the lesson's subject, and minted again the week the operator
// deleted the lesson as wrong. The ledger remembers every source a written
// lesson rested on, keyed the way `consolidated_from` names it, and the lessons
// already on disk add their own `consolidated_from` to it, so a lesson written
// before the ledger existed counts too.

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/cardshape"
	"github.com/alexherrero/agentm/daemon/internal/fmlist"
)

// ConsumedName is the ledger's file in the engine's state directory.
const ConsumedName = "crystallize-consumed.json"

// consumed maps a source's key to the lessons it taught, by stem.
type consumed map[string][]string

type consumedFile struct {
	Version int                 `json:"version"`
	Sources map[string][]string `json:"sources"`
}

// sourceKey is how a source is named in `consolidated_from`, without the
// alias: `projects/x/tasks/y/tracker` for a tracker, a note's stem otherwise.
func sourceKey(link string) string {
	link = strings.TrimSpace(link)
	if i := strings.Index(link, "|"); i >= 0 {
		link = link[:i]
	}
	return strings.TrimSuffix(link, ".md")
}

// loadConsumed reads the ledger and every lesson's `consolidated_from`.
func loadConsumed(root, stateDir string) consumed {
	out := consumed{}
	if stateDir != "" {
		if raw, err := os.ReadFile(filepath.Join(stateDir, ConsumedName)); err == nil {
			var f consumedFile
			if json.Unmarshal(raw, &f) == nil {
				for k, lessons := range f.Sources {
					for _, l := range lessons {
						out.add(k, l)
					}
				}
			}
		}
	}
	dir := filepath.Join(root, filepath.FromSlash(Dir))
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") || strings.HasPrefix(e.Name(), "_") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			continue
		}
		stem := strings.TrimSuffix(e.Name(), ".md")
		for _, item := range fmlist.Items(string(raw), "consolidated_from") {
			for _, m := range stampLink.FindAllStringSubmatch(item, -1) {
				out.add(sourceKey(m[1]), stem)
			}
		}
	}
	return out
}

func (c consumed) add(key, lesson string) {
	if key == "" || containsString(c[key], lesson) {
		return
	}
	c[key] = append(c[key], lesson)
}

// all reports whether every source of a cluster has taught a lesson already.
func (c consumed) all(cl Cluster) bool {
	if len(cl.Sources) == 0 {
		return false
	}
	for _, s := range cl.Sources {
		if len(c[sourceKey(s.Link())]) == 0 {
			return false
		}
	}
	return true
}

// mostly is the lesson that more than half of a draft's sources already
// taught, or "".
func (c consumed) mostly(cl Cluster) string {
	counts := map[string]int{}
	for _, s := range cl.Sources {
		for _, l := range c[sourceKey(s.Link())] {
			counts[l]++
		}
	}
	best, n := "", 0
	for l, k := range counts {
		if k > n || (k == n && l < best) {
			best, n = l, k
		}
	}
	if 2*n > len(cl.Sources) {
		return best
	}
	return ""
}

// save writes the ledger, sorted, atomically.
func (c consumed) save(stateDir string) error {
	if stateDir == "" {
		return nil
	}
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		return err
	}
	f := consumedFile{Version: 1, Sources: map[string][]string{}}
	for k, lessons := range c {
		l := append([]string(nil), lessons...)
		sort.Strings(l)
		f.Sources[k] = l
	}
	blob, err := json.MarshalIndent(f, "", "  ")
	if err != nil {
		return err
	}
	p := filepath.Join(stateDir, ConsumedName)
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, append(blob, '\n'), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}

// Linked is a draft that restated a lesson and was not minted: its new cards
// name that lesson in `related` instead.
type Linked struct {
	Subject string   `json:"subject"`
	Lesson  string   `json:"lesson"`
	Sources []string `json:"sources"`
	Cards   []string `json:"cards,omitempty"`
}

// link names `lesson` in the `related` of each card the draft rests on that
// has not taught it, and returns the cards it wrote.
func link(cl Cluster, lesson string, c consumed) []string {
	var out []string
	for _, s := range cl.Sources {
		if s.Kind != KindCard || s.Path == "" || containsString(c[sourceKey(s.Link())], lesson) {
			continue
		}
		raw, err := os.ReadFile(s.Path)
		if err != nil {
			continue
		}
		after := addRelated(string(raw), lesson)
		if after == string(raw) {
			continue
		}
		if err := os.WriteFile(s.Path, []byte(after), 0o644); err != nil {
			continue
		}
		out = append(out, s.Rel)
	}
	return out
}

// addRelated adds `[[stem]]` to a note's `related`, keeping what it names.
func addRelated(text, stem string) string {
	want := fmt.Sprintf("[[%s]]", stem)
	items := fmlist.Items(text, "related")
	for _, it := range items {
		if strings.TrimSpace(it) == want {
			return text
		}
	}
	items = append(items, want)
	quoted := make([]string, len(items))
	for i, it := range items {
		quoted[i] = fmt.Sprintf("%q", it)
	}
	line := "related: [" + strings.Join(quoted, ", ") + "]"
	if out, ok := fmlist.Replace(text, "related", line); ok {
		return out
	}
	if !strings.HasPrefix(text, "---\n") {
		return text
	}
	end := strings.Index(text[4:], "\n---")
	if end < 0 {
		return text
	}
	end += 4
	return cardshape.Reorder(text[:end] + "\n" + line + text[end:])
}
