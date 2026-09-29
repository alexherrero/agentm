// Package restated finds convention, preference and workflow notes that state
// the same rule twice, and plans their merge (agentm-vault § Dreaming, amended
// 2026-09-28; task 178, step 8).
//
// The operator's ruling 6c merges restated rules automatically, keeping the
// newer wording. Calibration on the live corpus showed that embedding
// similarity alone cannot tell a rule restated from two neighbouring rules: the
// known pair (tests-are-sacred, never-edit-or-delete-a-failing-test) scored
// 0.794 and ranked ninth, below eight pairs of distinct rules, and word overlap
// separated nothing. So on the operator's ruling of 2026-09-28 the similarity
// only shortlists, and the strong tier judges each shortlisted pair: "the same
// rule restated" merges, anything else is listed for the operator. A verdict
// is remembered against both notes' bodies, so an unchanged pair is judged once.
//
// A merge is a supersede and nothing is deleted: the older note survives with
// its path, links and slug, takes the newer note's summary and body, keeps the
// operator's importance, and names the newer in `supersedes`; the newer note
// takes `lifecycle: superseded` and `superseded_by` naming the survivor. Git
// keeps the older wording.
package restated

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/cardshape"
)

// RuleTypes are the note types whose restatements merge.
var RuleTypes = map[string]bool{"convention": true, "preference": true, "workflow": true}

// DefaultReviewLine is the similarity a pair must reach to be shortlisted:
// the live corpus's known restated pair scored 0.794, and the line sits below
// it with room, since the judge, not the line, decides.
const DefaultReviewLine = 0.74

// DefaultCap bounds the pairs one run judges, a runaway guard on spend.
const DefaultCap = 30

// Note is one rule card as the pass reads it.
type Note struct {
	Rel        string // vault-relative, as the index keys it
	MemoryRel  string // relative to the memory root, as the journal keys it
	Type       string
	Created    string
	Title      string
	Summary    string
	Importance string
	Body       string
	Raw        string
}

// Fingerprint is the note's body hash: a verdict holds while both bodies do.
func (n Note) Fingerprint() string {
	sum := sha256.Sum256([]byte(strings.TrimSpace(n.Body)))
	return hex.EncodeToString(sum[:8])
}

// block is one top-level frontmatter key and its lines.
type block struct {
	key   string
	lines []string
}

func splitNote(raw string) ([]block, string, bool) {
	raw = strings.ReplaceAll(raw, "\r\n", "\n")
	if !strings.HasPrefix(raw, "---\n") {
		return nil, raw, false
	}
	end := strings.Index(raw[4:], "\n---\n")
	if end < 0 {
		return nil, raw, false
	}
	end += 4
	var blocks []block
	for _, line := range strings.Split(raw[4:end], "\n") {
		if line != "" && !strings.ContainsRune(" \t#-", rune(line[0])) {
			if k, _, ok := strings.Cut(line, ":"); ok {
				blocks = append(blocks, block{key: strings.TrimSpace(k), lines: []string{line}})
				continue
			}
		}
		if len(blocks) > 0 {
			blocks[len(blocks)-1].lines = append(blocks[len(blocks)-1].lines, line)
		}
	}
	return blocks, raw[end+5:], true
}

func value(blocks []block, key string) string {
	for _, b := range blocks {
		if b.key == key {
			_, v, _ := strings.Cut(b.lines[0], ":")
			v = strings.TrimSpace(v)
			if len(v) >= 2 && (v[0] == '"' && v[len(v)-1] == '"' || v[0] == '\'' && v[len(v)-1] == '\'') {
				v = v[1 : len(v)-1]
			}
			return v
		}
	}
	return ""
}

func line(blocks []block, key string) []string {
	for _, b := range blocks {
		if b.key == key {
			return b.lines
		}
	}
	return nil
}

func set(blocks []block, key, rendered string) []block {
	for i, b := range blocks {
		if b.key == key {
			blocks[i] = block{key: key, lines: []string{key + ": " + rendered}}
			return blocks
		}
	}
	return append(blocks, block{key: key, lines: []string{key + ": " + rendered}})
}

func setLines(blocks []block, key string, lines []string) []block {
	for i, b := range blocks {
		if b.key == key {
			blocks[i] = block{key: key, lines: lines}
			return blocks
		}
	}
	return append(blocks, block{key: key, lines: lines})
}

func join(blocks []block, body string) string {
	var out []string
	for _, b := range blocks {
		out = append(out, b.lines...)
	}
	return "---\n" + strings.Join(out, "\n") + "\n---\n" + body
}

// Gather reads the active rule cards under `<vault>/<memoryRoot>/memory/`'s
// semantic and procedural classes, where the contract routes the three types.
func Gather(vault, memoryRoot string) ([]Note, error) {
	var notes []Note
	for _, class := range []string{"semantic", "procedural"} {
		dir := filepath.Join(vault, filepath.FromSlash(memoryRoot), "memory", class)
		entries, err := os.ReadDir(dir)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			return nil, err
		}
		for _, e := range entries {
			name := e.Name()
			if e.IsDir() || !strings.HasSuffix(name, ".md") || strings.HasPrefix(name, "_") {
				continue
			}
			raw, err := os.ReadFile(filepath.Join(dir, name))
			if err != nil {
				continue
			}
			blocks, body, ok := splitNote(string(raw))
			if !ok || !RuleTypes[strings.ToLower(value(blocks, "type"))] || value(blocks, "kind") != "" {
				continue
			}
			switch strings.ToLower(value(blocks, "lifecycle")) {
			case "superseded", "archived":
				continue
			}
			if value(blocks, "superseded_by") != "" || strings.ToLower(value(blocks, "status")) == "superseded" {
				continue
			}
			memRel := path.Join("memory", class, name)
			notes = append(notes, Note{
				Rel: path.Join(filepath.ToSlash(memoryRoot), memRel), MemoryRel: memRel,
				Type: strings.ToLower(value(blocks, "type")), Created: value(blocks, "created"),
				Title: value(blocks, "title"), Summary: value(blocks, "summary"),
				Importance: value(blocks, "importance"), Body: body, Raw: string(raw),
			})
		}
	}
	sort.Slice(notes, func(i, j int) bool { return notes[i].Rel < notes[j].Rel })
	return notes, nil
}

// Pair is two rule cards and how alike their best chunks are.
type Pair struct {
	A, B       Note
	Similarity float64
}

// Key names the pair and both bodies, for the verdict cache.
func (p Pair) Key() string {
	return p.A.Rel + "|" + p.B.Rel + "|" + p.A.Fingerprint() + "|" + p.B.Fingerprint()
}

// Shortlist is every pair at or above line, the most alike first. A pair's
// similarity is the best dot product over the two notes' chunk vectors, the way
// dense search scores a note by its best chunk; the vectors are unit length.
func Shortlist(notes []Note, vecs map[string][][]float32, line float64) []Pair {
	var out []Pair
	for i := 0; i < len(notes); i++ {
		for j := i + 1; j < len(notes); j++ {
			a, b := vecs[notes[i].Rel], vecs[notes[j].Rel]
			best := -1.0
			for _, u := range a {
				for _, v := range b {
					if len(u) != len(v) {
						continue
					}
					var dot float64
					for k := range u {
						dot += float64(u[k]) * float64(v[k])
					}
					if dot > best {
						best = dot
					}
				}
			}
			if best >= line {
				out = append(out, Pair{A: notes[i], B: notes[j], Similarity: best})
			}
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Similarity != out[j].Similarity {
			return out[i].Similarity > out[j].Similarity
		}
		return out[i].A.Rel+out[i].B.Rel < out[j].A.Rel+out[j].B.Rel
	})
	return out
}

// SystemPrompt frames the judge.
const SystemPrompt = "You compare two notes from a personal knowledge vault. Each states a rule the " +
	"operator wants an AI coding agent to follow. Decide whether they are the same rule stated twice " +
	"(one would be redundant if the other were kept) or two different rules, even if the topics are close. " +
	"Answer with JSON only."

// Prompt is the question put to the judge about one pair.
func Prompt(p Pair) string {
	note := func(label string, n Note) string {
		body := strings.TrimSpace(n.Body)
		if len(body) > 4000 {
			body = body[:4000] + " …"
		}
		return fmt.Sprintf("## %s\n\ntitle: %s\ntype: %s\nsummary: %s\n\n%s\n", label, n.Title, n.Type, n.Summary, body)
	}
	return note("Note A", p.A) + "\n" + note("Note B", p.B) + "\n" +
		`Are A and B the same rule restated? Reply with exactly one JSON object: ` +
		`{"verdict": "same" | "different", "reason": "<one sentence>"}. ` +
		`"same" only when following one of them is following the other; a rule and a narrower or ` +
		`related rule are "different".`
}

// Verdict is the judge's answer.
type Verdict struct {
	Verdict string `json:"verdict"`
	Reason  string `json:"reason"`
}

// Same reports a "same" verdict.
func (v Verdict) Same() bool { return v.Verdict == "same" }

// ParseVerdict reads the judge's JSON object out of its reply.
func ParseVerdict(out string) (Verdict, error) {
	start, end := strings.Index(out, "{"), strings.LastIndex(out, "}")
	if start < 0 || end <= start {
		return Verdict{}, fmt.Errorf("no JSON object in the judge's reply")
	}
	var v Verdict
	if err := json.Unmarshal([]byte(out[start:end+1]), &v); err != nil {
		return Verdict{}, err
	}
	v.Verdict = strings.ToLower(strings.TrimSpace(v.Verdict))
	if v.Verdict != "same" && v.Verdict != "different" {
		return Verdict{}, fmt.Errorf("verdict %q is neither same nor different", v.Verdict)
	}
	return v, nil
}

// Order is the pair as a merge takes it: the older note survives. Older is the
// earlier `created`, then the earlier path.
func Order(p Pair) (older, newer Note) {
	a, b := p.A, p.B
	ca, cb := a.Created, b.Created
	if len(ca) > 10 {
		ca = ca[:10]
	}
	if len(cb) > 10 {
		cb = cb[:10]
	}
	if cb < ca || (cb == ca && b.Rel < a.Rel) {
		return b, a
	}
	return a, b
}

// Merge is the two notes' text after the merge: the survivor with the newer
// note's summary and body, its own importance (or the newer's when it has
// none), and the newer named in `supersedes`; the newer superseded by it.
func Merge(older, newer Note, today string) (survivor, superseded string, err error) {
	ob, _, ok := splitNote(older.Raw)
	nb, newerBody, ok2 := splitNote(newer.Raw)
	if !ok || !ok2 {
		return "", "", fmt.Errorf("a note without a frontmatter block")
	}
	if s := line(nb, "summary"); s != nil {
		ob = setLines(ob, "summary", s)
	}
	if line(ob, "importance") == nil {
		if imp := line(nb, "importance"); imp != nil {
			ob = setLines(ob, "importance", imp)
		}
	}
	if line(ob, "why") == nil {
		if why := line(nb, "why"); why != nil {
			ob = setLines(ob, "why", why)
		}
	}
	ob = set(ob, "supersedes", newer.MemoryRel)
	ob = set(ob, "updated", today)
	survivor = cardshape.Reorder(join(ob, newerBody))

	nb = set(nb, "lifecycle", "superseded")
	nb = set(nb, "lifecycle_since", today)
	nb = set(nb, "superseded_by", older.MemoryRel)
	_, olderBodyOfNewer, _ := splitNote(newer.Raw)
	superseded = cardshape.Reorder(join(nb, olderBodyOfNewer))
	return survivor, superseded, nil
}
