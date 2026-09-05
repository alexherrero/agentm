package dreaming

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// The first job the binary carries: the lifecycle axis's automatic lane,
// ported from lifecycle_transitions.policy_pass. A memory silent past the
// contract's `dormant_after_days` sinks to `dormant`; a dormant memory a
// genuine recall touched lifts back to `active`; a dormant memory past
// `archive_after_days` is named as an archive candidate and never moved —
// entering `archived` is the confirm surface's, by design. Pinned,
// superseded and archived never move; a decay-exempt note is skipped.
//
// Same words, same edit: the frontmatter change is a byte-exact port of the
// Python `set_lifecycle_text`, and every applied transition is appended to
// the same lifecycle journal the digest and scorecard read, so the two
// layers are one record while they overlap.

const (
	JobLifecycle          = "lifecycle"
	DefaultDormantAfter   = 365.0
	DefaultArchiveAfter   = 1825.0
	PreviewFraction       = 0.9
	DefaultDemotionCap    = 200
	LifecycleJournalName  = "lifecycle-journal.jsonl"
	lifecycleDefaultState = "active"
	lifecycleKeyDormant   = "dormant_after_days"
	lifecycleKeyArchive   = "archive_after_days"
)

var classDirs = []string{"semantic", "procedural", "episodic", "entities", "crystallized", "mocs"}

var slugRe = regexp.MustCompile(`(?m)^slug:[ \t]*(\S+)`)

// LifecyclePlan is what one pass would (or did) do.
type LifecyclePlan struct {
	Intents    []Intent `json:"-"`
	Demoted    []Move   `json:"demoted"`
	Revived    []Move   `json:"revived"`
	Candidates []Move   `json:"archive_candidates"`
	Previews   []Move   `json:"previews"`
	Capped     int      `json:"skipped_by_cap"`
	Considered int      `json:"considered"`
}

// Move names one note and how long it has been silent.
type Move struct {
	Rel  string  `json:"rel"`
	Days float64 `json:"days"`
}

// Thresholds reads the contract's two lifecycle thresholds, with the
// packaged defaults for a contract that predates them. A nil contract is
// the defaults.
func Thresholds(r *rules.Rules) (dormantAfter, archiveAfter float64) {
	dormantAfter, archiveAfter = DefaultDormantAfter, DefaultArchiveAfter
	if r == nil || r.Thresholds == nil {
		return
	}
	if v, ok := r.Thresholds[lifecycleKeyDormant]; ok && v > 0 {
		dormantAfter = v
	}
	if v, ok := r.Thresholds[lifecycleKeyArchive]; ok && v > 0 {
		archiveAfter = v
	}
	return
}

// MemoryNotes lists the memory-class notes under `root` (the memory root,
// the directory holding memory/), as paths relative to it, sorted.
func MemoryNotes(root string) ([]string, error) {
	var out []string
	for _, cls := range classDirs {
		dir := filepath.Join(root, "memory", cls)
		if st, err := os.Stat(dir); err != nil || !st.IsDir() {
			continue
		}
		err := filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
			if err != nil {
				return nil
			}
			if d.IsDir() || filepath.Ext(p) != ".md" || d.Name() == "_index.md" || strings.HasPrefix(d.Name(), "Icon") {
				return nil
			}
			rel, err := filepath.Rel(root, p)
			if err != nil {
				return nil
			}
			out = append(out, filepath.ToSlash(rel))
			return nil
		})
		if err != nil {
			return nil, err
		}
	}
	sort.Strings(out)
	return out, nil
}

// LifecycleOf reads a note's state; `active` when it carries none.
func LifecycleOf(n note.Note) string {
	if n.Lifecycle == "" {
		return lifecycleDefaultState
	}
	return n.Lifecycle
}

// SetLifecycle is the byte-exact port of lifecycle_transitions.set_lifecycle_text:
// the note with `lifecycle: <to>` and `lifecycle_since: <since>` — a
// line-surgical edit of the frontmatter, the body byte-identical, a note
// without frontmatter returned unchanged.
func SetLifecycle(text, to, since string) string {
	if !strings.HasPrefix(text, "---\n") {
		return text
	}
	lines := strings.Split(text, "\n")
	end := -1
	for i := 1; i < len(lines); i++ {
		if strings.TrimSpace(lines[i]) == "---" {
			end = i
			break
		}
	}
	if end < 0 {
		return text
	}
	seen := map[string]int{}
	for i := 1; i < end; i++ {
		k, _, ok := strings.Cut(lines[i], ":")
		if ok && len(lines[i]) > 0 && !strings.ContainsRune(" \t#-", rune(lines[i][0])) {
			key := strings.TrimSpace(k)
			if _, dup := seen[key]; !dup {
				seen[key] = i
			}
		}
	}
	var at int
	if i, ok := seen["lifecycle"]; ok {
		lines[i] = "lifecycle: " + to
		at = i + 1
	} else {
		lines = append(lines[:end], append([]string{"lifecycle: " + to}, lines[end:]...)...)
		at = end + 1
	}
	if i, ok := seen["lifecycle_since"]; ok {
		// A frontmatter key sits before the closing fence, so the insertion
		// above (at `end`) never shifts it.
		lines[i] = "lifecycle_since: " + since
	} else {
		lines = append(lines[:at], append([]string{"lifecycle_since: " + since}, lines[at:]...)...)
	}
	return strings.Join(lines, "\n")
}

// PlanLifecycle reads every memory under `root` and decides the pass. It
// writes nothing; the run applies the intents through the journal.
func PlanLifecycle(root string, r *rules.Rules, now time.Time, cap int) (LifecyclePlan, error) {
	var plan LifecyclePlan
	dormantAfter, archiveAfter := Thresholds(r)
	if cap <= 0 {
		cap = DefaultDemotionCap
	}
	rels, err := MemoryNotes(root)
	if err != nil {
		return plan, err
	}
	log := note.NewAccessLog(root)
	since := now.UTC().Format("2006-01-02")
	// Silence is counted in whole days from midnight, the way the Python
	// policy counts it (its anchors and its `now` are dates), so the two
	// layers agree on the figure and not just on the decision.
	dayNow := time.Date(now.UTC().Year(), now.UTC().Month(), now.UTC().Day(), 0, 0, 0, 0, time.UTC)
	for _, rel := range rels {
		p := filepath.Join(root, filepath.FromSlash(rel))
		raw, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		st, err := os.Stat(p)
		if err != nil {
			continue
		}
		text := string(raw)
		n := note.Parse(rel, text, st.ModTime())
		plan.Considered++
		state := LifecycleOf(n)
		if state == "pinned" || state == "superseded" || state == "archived" {
			continue
		}
		if note.IsDecayExempt(n.Flags) {
			continue
		}
		slug := strings.TrimSuffix(filepath.Base(rel), ".md")
		if m := slugRe.FindStringSubmatch(text); m != nil {
			slug = strings.Trim(m[1], `'"`)
		}
		captured := ""
		if !n.Captured.IsZero() {
			captured = n.Captured.UTC().Format(time.RFC3339)
		}
		days, ok := note.ElapsedDays(log, slug, n.Updated, n.Created, captured, n.CapturedSource, dayNow)
		if !ok {
			continue
		}
		switch {
		case state == lifecycleDefaultState && days > dormantAfter:
			if len(plan.Demoted) >= cap {
				plan.Capped++
				continue
			}
			after := SetLifecycle(text, "dormant", since)
			summary := fmt.Sprintf("silent %.0f days, past %s %.0f → dormant", days, lifecycleKeyDormant, dormantAfter)
			plan.Intents = append(plan.Intents, Intent{Job: JobLifecycle, Rel: rel, Before: raw, After: []byte(after), Summary: summary,
				Meta: map[string]string{"from": state, "to": "dormant", "reason": summary}})
			plan.Demoted = append(plan.Demoted, Move{rel, days})
		case state == "dormant" && days <= dormantAfter:
			after := SetLifecycle(text, lifecycleDefaultState, since)
			summary := fmt.Sprintf("recalled %.0f days ago, within %s → active", days, lifecycleKeyDormant)
			plan.Intents = append(plan.Intents, Intent{Job: JobLifecycle, Rel: rel, Before: raw, After: []byte(after), Summary: summary,
				Meta: map[string]string{"from": state, "to": lifecycleDefaultState, "reason": summary}})
			plan.Revived = append(plan.Revived, Move{rel, days})
		case state == "dormant" && days > archiveAfter:
			plan.Candidates = append(plan.Candidates, Move{rel, days})
		case state == "dormant" && days > archiveAfter*PreviewFraction:
			plan.Previews = append(plan.Previews, Move{rel, days})
		}
	}
	return plan, nil
}

// lifecycleLine is one line of the governance journal the Python layer
// keeps (lifecycle_transitions.journal_append): the same keys, in the same
// sorted order, so both layers write one record.
type lifecycleLine struct {
	Actor  string  `json:"actor"`
	From   string  `json:"from"`
	Reason string  `json:"reason"`
	Rel    string  `json:"rel"`
	RunID  *string `json:"run_id"`
	To     string  `json:"to"`
	TS     string  `json:"ts"`
}

// AppendLifecycleJournal records one applied transition in the governance
// journal under the engine state dir, as the Python policy would.
func AppendLifecycleJournal(engineStateDir, rel, from, to, reason, runID string, now time.Time) error {
	p := filepath.Join(engineStateDir, LifecycleJournalName)
	if err := os.MkdirAll(engineStateDir, 0o755); err != nil {
		return err
	}
	return appendLifecycleLine(p, rel, from, to, reason, runID, now)
}

// EnsureLifecycleJournal is AppendLifecycleJournal made idempotent: a line
// for the same run, note and state is written once, whichever of the pass
// and its resume gets there first.
func EnsureLifecycleJournal(engineStateDir, rel, from, to, reason, runID string, now time.Time) error {
	p := filepath.Join(engineStateDir, LifecycleJournalName)
	if blob, err := os.ReadFile(p); err == nil {
		for _, line := range strings.Split(string(blob), "\n") {
			var got lifecycleLine
			if json.Unmarshal([]byte(line), &got) != nil {
				continue
			}
			if got.Rel == rel && got.To == to && got.RunID != nil && *got.RunID == runID {
				return nil
			}
		}
	}
	if err := os.MkdirAll(engineStateDir, 0o755); err != nil {
		return err
	}
	return appendLifecycleLine(p, rel, from, to, reason, runID, now)
}

func appendLifecycleLine(p, rel, from, to, reason, runID string, now time.Time) error {
	line := lifecycleLine{Actor: "policy", From: from, Reason: reason, Rel: rel, To: to,
		TS: now.UTC().Format("2006-01-02T15:04:05+00:00")}
	if runID != "" {
		line.RunID = &runID
	}
	blob, err := json.Marshal(line)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(p, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(append(blob, '\n'))
	return err
}

// transitionOf reads the from/to states an intent encodes, for the
// governance journal line.
func transitionOf(in Intent) (from, to string) {
	from = LifecycleOf(note.Parse(in.Rel, string(in.Before), time.Time{}))
	to = LifecycleOf(note.Parse(in.Rel, string(in.After), time.Time{}))
	return
}
