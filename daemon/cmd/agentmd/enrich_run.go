package main

import (
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
)

// The night's record of one enrichment run.
//
// One JSON line per run, appended to `enrich-runs.jsonl` in the engine state
// directory. The morning note reads its enrichment row and its spend line from
// here — "N judged · N filed active · N below the floor · N sank · calls ·
// tokens · model", and last night's tokens beside the seven-day total — so
// neither is re-derived from logs that say something slightly different.

// enrichVerdicts counts what the night decided about the notes it wrote.
type enrichVerdicts struct {
	Active     int `json:"filed_active"`
	BelowFloor int `json:"below_floor"`
	Sank       int `json:"sank"`
	// SankNotes names the notes that sank, for the morning note's list.
	SankNotes []string `json:"sank_notes,omitempty"`
}

// count reads one written note's verdict from its own frontmatter.
func (v *enrichVerdicts) count(next string) {
	if enrich.FrontmatterValue(next, "status") == "active" {
		v.Active++
		return
	}
	v.BelowFloor++
}

// enrichRun is one line of the record.
type enrichRun struct {
	At           time.Time               `json:"at"`
	Model        string                  `json:"model"`
	PassVersion  string                  `json:"pass_version"`
	Considered   int                     `json:"considered"`
	Enriched     int                     `json:"enriched"`
	Skipped      int                     `json:"skipped"`
	Failed       int                     `json:"failed"`
	NotesSent    int                     `json:"notes_sent"`
	ModelCalls   int                     `json:"model_calls"`
	Tokens       int64                   `json:"tokens"`
	TotalCostUSD float64                 `json:"total_cost_usd"`
	Usage        map[string]enrich.Usage `json:"usage,omitempty"`
	TokenLines   map[string]int64        `json:"token_lines"`
	CallGuard    int                     `json:"call_guard"`
	StoppedBy    string                  `json:"stopped_by,omitempty"`
	Cursor       string                  `json:"cursor,omitempty"`
	ElapsedSec   float64                 `json:"elapsed_seconds"`
	Verdicts     enrichVerdicts          `json:"verdicts"`
	Errors       []string                `json:"errors,omitempty"`
}

func newEnrichRun(rep enrich.BatchReport, v enrichVerdicts, model string,
	b enrich.Budget) enrichRun {
	return enrichRun{
		At: time.Now().UTC(), Model: model, PassVersion: enrich.PassVersion,
		Considered: rep.Considered, Enriched: rep.Enriched, Skipped: rep.Skipped,
		Failed: rep.Failed, NotesSent: rep.Calls, ModelCalls: rep.ModelCalls,
		Tokens: rep.Tokens, TotalCostUSD: rep.TotalCostUSD, Usage: rep.Usage,
		TokenLines: b.TokenLines, CallGuard: b.MaxCalls, StoppedBy: rep.StoppedBy,
		Cursor: rep.Cursor, ElapsedSec: rep.Elapsed.Seconds(), Verdicts: v,
		Errors: rep.Errors,
	}
}

// summary is the last line the command prints: the runner reads a job's cost
// from it.
func (r enrichRun) summary() map[string]any {
	out := map[string]any{
		"total_cost_usd": r.TotalCostUSD,
		"tokens":         r.Tokens,
		"model_calls":    r.ModelCalls,
		"enriched":       r.Enriched,
	}
	if r.StoppedBy != "" {
		out["stopped_by"] = r.StoppedBy
	}
	return out
}

// enrichRunsPath is where the record lives.
func enrichRunsPath(cfg *config.Config) string {
	dir := cfg.EngineStateDir
	if dir == "" {
		dir = filepath.Dir(cfg.IndexPath)
	}
	return filepath.Join(dir, "enrich-runs.jsonl")
}

// appendEnrichRun adds one run to the record.
func appendEnrichRun(cfg *config.Config, r enrichRun) error {
	p := enrichRunsPath(cfg)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	line, err := json.Marshal(r)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(p, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(append(line, '\n'))
	return err
}

// enrichQueueDirs is where the batch looks for cards: the directories the
// filing contract routes a memory type into, less the derived classes.
//
// Read from the contract rather than listed here. Today that is
// `memory/semantic` and `memory/procedural`. It is not `memory/episodic`,
// which no memory type routes to because it holds session traces — records,
// not cards, whose `session`, `day` and `touched` a rewrite would drop. And it
// is not `memory/_watchlist/`, whose `pending-review` entries are
// forward_learning.py's records. Neither may spend the night's budget.
func enrichQueueDirs(cfg *config.Config) ([]string, error) {
	loaded, err := cfg.Rules.Get()
	if err != nil {
		return nil, fmt.Errorf("enrich: the filing contract did not load, so the "+
			"batch cannot tell which directories hold cards: %w", err)
	}
	seen := map[string]bool{}
	var dirs []string
	for _, t := range loaded.MemoryTypes {
		class, ok := loaded.ClassFor(t)
		if !ok || enrich.DerivedClasses[class] || seen[class] {
			continue
		}
		seen[class] = true
		dirs = append(dirs, path.Join(cfg.MemoryRoot, "memory", class)+"/")
	}
	sort.Strings(dirs)
	if len(dirs) == 0 {
		return nil, fmt.Errorf("enrich: the filing contract routes no memory type " +
			"into a class directory, so the batch has nothing to walk")
	}
	return dirs, nil
}

// enrichQueue is every indexed note under the queue's directories, in path
// order — a snapshot taken once per run, so a note captured mid-night waits for
// the next night rather than moving the cursor under the run.
//
// Every card is offered and the gates decide. A note with no stamp, or a stamp
// from an older pass, is owed the deep pass; one whose body moved since a
// current stamp is owed the light pass; one that has not moved costs nothing,
// because the fingerprint gate refuses it before any call exists.
func enrichQueue(idx *index.Index, dirs []string) ([]string, error) {
	all, err := idx.Paths()
	if err != nil {
		return nil, err
	}
	var out []string
	for _, p := range all {
		if !strings.HasSuffix(p, ".md") || strings.HasPrefix(path.Base(p), ".") {
			continue
		}
		for _, d := range dirs {
			if strings.HasPrefix(p, d) {
				out = append(out, p)
				break
			}
		}
	}
	sort.Strings(out)
	return out, nil
}

// sampleQueue draws n paths from the queue at random, seeded, in path order.
func sampleQueue(queue []string, n int, seed int64) []string {
	if n >= len(queue) {
		return append([]string(nil), queue...)
	}
	r := rand.New(rand.NewSource(seed))
	picked := append([]string(nil), queue...)
	r.Shuffle(len(picked), func(i, j int) { picked[i], picked[j] = picked[j], picked[i] })
	picked = picked[:n]
	sort.Strings(picked)
	return picked
}

// lowerOnly applies a budget flag: zero keeps the operator's line, a smaller
// number lowers it, and a larger one is refused. The line is the operator's
// (agentm-vault § Dreaming, Q2), and nothing run by hand or by schedule is
// entitled to more than was said.
func lowerOnly(name string, flag, line int64) (int64, error) {
	if flag <= 0 {
		return line, nil
	}
	if flag > line {
		return 0, fmt.Errorf("--%s %d is over the operator's line of %d; a flag "+
			"may lower the nightly budget, not raise it", name, flag, line)
	}
	return flag, nil
}

func commasInt(n int64) string {
	s := fmt.Sprint(n)
	out := make([]byte, 0, len(s)+len(s)/3)
	for i := range s {
		if i > 0 && (len(s)-i)%3 == 0 {
			out = append(out, ',')
		}
		out = append(out, s[i])
	}
	return string(out)
}
