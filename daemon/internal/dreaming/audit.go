package dreaming

import (
	"fmt"
	"math/rand"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// The report-only checks (task 5): the scheduled vocabulary audit, the
// class-distribution and volume trends with the thresholds the design names
// as re-audit triggers, and the sampled re-classification diff that runs
// whenever the filing pass version changes. None of these mutates a note;
// they ride the pass, land on the report, and the runner keeps the report.

const (
	DefaultReclassifySample = 30 // completeness.go's sample, same justification
	reclassifyKeySample     = "reclassify_sample"
	trendWindowDays         = 14
	volumeKeyCap            = "daily_write_cap"
	defaultVolumeCap        = 200
)

var kebabRe = regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)

// VocabFinding names one note whose vocabulary the contract does not know.
type VocabFinding struct {
	Rel   string `json:"rel"`
	Field string `json:"field"`
	Value string `json:"value"`
	Note  string `json:"note,omitempty"`
}

// VocabularyReport is the audit's answer.
type VocabularyReport struct {
	Considered   int            `json:"considered"`
	Unrecognized []VocabFinding `json:"unrecognized"`
	Malformed    []VocabFinding `json:"malformed"`
	Retired      []VocabFinding `json:"retired"`
}

// VocabularyAudit checks every memory's `type:` and every record's `kind:`
// against the contract's registers (the set-ratchet gate does the same on
// demand; this is the scheduled reading of it).
func VocabularyAudit(root string, r *rules.Rules) (VocabularyReport, error) {
	var rep VocabularyReport
	rels, err := MemoryNotes(root)
	if err != nil {
		return rep, err
	}
	for _, rel := range rels {
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		fm, _ := ParseFrontmatter(string(raw))
		rep.Considered++
		for _, field := range []string{"type", "kind"} {
			v := strings.TrimSpace(fm[field])
			if v == "" {
				continue
			}
			if !kebabRe.MatchString(v) {
				rep.Malformed = append(rep.Malformed, VocabFinding{Rel: rel, Field: field, Value: v, Note: "not kebab-case"})
				continue
			}
			if r == nil {
				continue
			}
			if field == "type" {
				if repl, dep := r.ReplacementFor(v); dep {
					rep.Retired = append(rep.Retired, VocabFinding{Rel: rel, Field: field, Value: v, Note: "retired; replacement " + repl})
				} else if !r.IsMemoryType(v) {
					rep.Unrecognized = append(rep.Unrecognized, VocabFinding{Rel: rel, Field: field, Value: v})
				}
			} else if !r.IsRecordKind(v) && !r.IsMemoryType(v) {
				rep.Unrecognized = append(rep.Unrecognized, VocabFinding{Rel: rel, Field: field, Value: v})
			}
		}
	}
	return rep, nil
}

// ClassCount is a class's population: flat notes and the ones in lanes.
type ClassCount struct {
	Flat  int `json:"flat"`
	Lanes int `json:"lanes"`
}

// TrendReport is the class and volume reading with its flags.
type TrendReport struct {
	Populations  map[string]ClassCount `json:"populations"`
	WritesByDay  [][2]any              `json:"writes_by_day"`
	Week         int                   `json:"week"`
	PreviousWeek int                   `json:"previous_week"`
	ChangePct    *int                  `json:"change_pct"`
	Peak         int                   `json:"peak"`
	Cap          int                   `json:"cap"`
	DaysAtCap    []string              `json:"days_at_cap"`
	Growth       map[string]int        `json:"growth_since_last_pass"`
	Flags        []string              `json:"flags"`
}

// Trends is corpus_scorecard.class_populations + volume_gate.trend, with
// the design's re-audit triggers turned into flags: writes doubling week
// over week, a day at or over the cap, a class growing by half since the
// last pass. `previous` is the last pass's flat counts.
func Trends(root string, r *rules.Rules, now time.Time, previous map[string]int) (TrendReport, error) {
	rep := TrendReport{Populations: map[string]ClassCount{}, Growth: map[string]int{}}
	rels, err := MemoryNotes(root)
	if err != nil {
		return rep, err
	}
	today := time.Date(now.UTC().Year(), now.UTC().Month(), now.UTC().Day(), 0, 0, 0, 0, time.UTC)
	window := make([]string, trendWindowDays)
	counts := map[string]int{}
	for i := 0; i < trendWindowDays; i++ {
		d := today.AddDate(0, 0, -(trendWindowDays - 1 - i)).Format("2006-01-02")
		window[i] = d
		counts[d] = 0
	}
	for _, cls := range classDirs {
		rep.Populations[cls] = ClassCount{}
	}
	for _, rel := range rels {
		cls := classOf(rel)
		c := rep.Populations[cls]
		if strings.Count(rel, "/") == 2 {
			c.Flat++
		} else {
			c.Lanes++
		}
		rep.Populations[cls] = c
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		fm, _ := ParseFrontmatter(string(raw))
		if day := anchorOf(fm); day != "" {
			if _, in := counts[day]; in {
				counts[day]++
			}
		}
	}
	for i, d := range window {
		n := counts[d]
		rep.WritesByDay = append(rep.WritesByDay, [2]any{d, n})
		if i >= trendWindowDays/2 {
			rep.Week += n
		} else {
			rep.PreviousWeek += n
		}
		if n > rep.Peak {
			rep.Peak = n
		}
	}
	rep.Cap = defaultVolumeCap
	if r != nil && r.Thresholds != nil {
		if v, ok := r.Thresholds[volumeKeyCap]; ok {
			rep.Cap = int(v)
		}
	}
	if rep.PreviousWeek > 0 {
		pct := int(float64(rep.Week-rep.PreviousWeek) * 100 / float64(rep.PreviousWeek))
		rep.ChangePct = &pct
		if pct >= 100 {
			rep.Flags = append(rep.Flags, fmt.Sprintf("writes doubled week over week (%d vs %d, +%d%%)", rep.Week, rep.PreviousWeek, pct))
		}
	}
	for _, d := range window {
		if rep.Cap > 0 && counts[d] >= rep.Cap {
			rep.DaysAtCap = append(rep.DaysAtCap, d)
			rep.Flags = append(rep.Flags, fmt.Sprintf("%s wrote %d memories, at or over the cap of %d", d, counts[d], rep.Cap))
		}
	}
	var classes []string
	for cls := range rep.Populations {
		classes = append(classes, cls)
	}
	sort.Strings(classes)
	for _, cls := range classes {
		if prev, ok := previous[cls]; ok {
			delta := rep.Populations[cls].Flat - prev
			rep.Growth[cls] = delta
			if prev >= 10 && delta*2 >= prev {
				rep.Flags = append(rep.Flags, fmt.Sprintf("%s grew by %d since the last pass (was %d)", cls, delta, prev))
			}
		}
	}
	return rep, nil
}

// Flat returns the flat counts per class, the shape the state keeps.
func (t TrendReport) Flat() map[string]int {
	out := map[string]int{}
	for cls, c := range t.Populations {
		out[cls] = c.Flat
	}
	return out
}

// Mismatch is one sampled note whose stamped placement the contract would
// not repeat.
type Mismatch struct {
	Rel    string `json:"rel"`
	Type   string `json:"type"`
	Class  string `json:"class"`
	Routed string `json:"routed"`
	Note   string `json:"note"`
}

// ReclassifyReport is the sampled diff.
type ReclassifyReport struct {
	Version    string     `json:"version"`
	Previous   string     `json:"previous,omitempty"`
	Ran        bool       `json:"ran"`
	Reason     string     `json:"reason"`
	Available  int        `json:"available"`
	Sampled    int        `json:"sampled"`
	Seed       int64      `json:"seed"`
	Mismatches []Mismatch `json:"mismatches"`
}

// PassVersion is the filing pass version a note is judged under: the
// enrichment pass (which moves with its prompt) and the contract's hash.
func PassVersion(r *rules.Rules) string {
	v := enrich.PassVersion
	if r != nil {
		v += "+rules/" + r.Hash
	}
	return v
}

// Reclassify re-derives, for a deterministic sample of typed memories, the
// class the contract routes their type to, and diffs it against where they
// sit — only when the pass version changed since the last run (or when
// forced). The sample is completeness.go's shape: newest first, shuffled
// under a seed, truncated.
func Reclassify(root string, r *rules.Rules, current, previous string, sample int, seed int64, force bool) (ReclassifyReport, error) {
	rep := ReclassifyReport{Version: current, Previous: previous, Seed: seed}
	switch {
	case force:
		rep.Reason = "forced"
	case previous == "":
		rep.Reason = "first pass under a recorded version"
	case previous != current:
		rep.Reason = "the filing pass version changed"
	default:
		rep.Reason = "the filing pass version is unchanged"
		return rep, nil
	}
	if r == nil {
		rep.Reason += "; no contract to route with"
		return rep, nil
	}
	rep.Ran = true
	if sample <= 0 {
		sample = DefaultReclassifySample
	}
	rels, err := MemoryNotes(root)
	if err != nil {
		return rep, err
	}
	type cand struct {
		rel, typ, anchor string
	}
	var cands []cand
	for _, rel := range rels {
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		fm, _ := ParseFrontmatter(string(raw))
		if fm["kind"] != "" || strings.TrimSpace(fm["type"]) == "" {
			continue
		}
		cands = append(cands, cand{rel, strings.TrimSpace(fm["type"]), anchorOf(fm)})
	}
	rep.Available = len(cands)
	sort.SliceStable(cands, func(i, j int) bool {
		if cands[i].anchor != cands[j].anchor {
			return cands[i].anchor > cands[j].anchor
		}
		return cands[i].rel < cands[j].rel
	})
	if seed == 0 {
		seed = int64(len(cands))*1000003 + 17
		rep.Seed = seed
	}
	rng := rand.New(rand.NewSource(seed))
	rng.Shuffle(len(cands), func(i, j int) { cands[i], cands[j] = cands[j], cands[i] })
	if len(cands) > sample {
		cands = cands[:sample]
	}
	rep.Sampled = len(cands)
	for _, c := range cands {
		t := c.typ
		if repl, dep := r.ReplacementFor(t); dep && repl != "" {
			t = repl
		}
		routed, ok := r.ClassFor(t)
		switch {
		case !ok:
			rep.Mismatches = append(rep.Mismatches, Mismatch{Rel: c.rel, Type: c.typ, Class: classOf(c.rel), Routed: "", Note: "type the contract does not route"})
		case routed != classOf(c.rel):
			rep.Mismatches = append(rep.Mismatches, Mismatch{Rel: c.rel, Type: c.typ, Class: classOf(c.rel), Routed: routed, Note: "sits in another class"})
		}
	}
	sort.Slice(rep.Mismatches, func(i, j int) bool { return rep.Mismatches[i].Rel < rep.Mismatches[j].Rel })
	return rep, nil
}

// ReclassifySample reads the sample size from the contract.
func ReclassifySample(r *rules.Rules) int {
	if r != nil && r.Thresholds != nil {
		if v, ok := r.Thresholds[reclassifyKeySample]; ok && v > 0 {
			return int(v)
		}
	}
	return DefaultReclassifySample
}
