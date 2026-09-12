package main

import (
	"context"
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

// count adds one written note's verdict. A card that sank is also below the
// floor; it is counted in both, and named, because the morning note lists
// what quietly sank.
func (v *enrichVerdicts) count(rel string, verdict enrich.FilingVerdict) {
	if verdict.Status == "active" {
		v.Active++
		return
	}
	v.BelowFloor++
	if verdict.Sank {
		v.Sank++
		v.SankNotes = append(v.SankNotes, rel)
	}
}

// enrichRun is one line of the record.
type enrichRun struct {
	At          time.Time `json:"at"`
	Model       string    `json:"model"`
	PassVersion string    `json:"pass_version"`
	Considered  int       `json:"considered"`
	Enriched    int       `json:"enriched"`
	Skipped     int       `json:"skipped"`
	Failed      int       `json:"failed"`
	// Refused is the part of Skipped the refusal record accounts for — cards a
	// post-gate rejected on an earlier night and this one declined for free.
	Refused int `json:"refused"`
	// RefusalsOpen is how many cards stood refused when the run finished, under
	// the pass and gates running now. In the record so the morning note reads
	// one number from one file: a refused set that is growing is the operator's
	// to see, and the alternative is teaching the note to open a second file and
	// re-derive what the run already knew.
	RefusalsOpen int                     `json:"refusals_open"`
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
		Refused: rep.Refused,
		Failed:  rep.Failed, NotesSent: rep.Calls, ModelCalls: rep.ModelCalls,
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

// refusalFor is the row a post-gate rejection leaves behind, or nothing when
// the run did not end in one.
//
// A named function rather than four lines inside the observer, because this is
// the half of the record that has to agree with the gate reading it back — the
// same keyer, the same pass version, the same rules hash, the same gates
// version. A row built from any other four would be written every night and
// matched by nothing, which looks exactly like a record that is working.
func refusalFor(cfg *config.Config, keyer *enrich.Fingerprint, req enrich.Request,
	out enrich.Outcome, reason string) (enrich.Refusal, bool) {
	if out.RefusedBy == "" {
		return enrich.Refusal{}, false
	}
	return enrich.Refusal{
		Rel: req.Rel, Gate: out.RefusedBy, Key: keyer.Key(req.Raw),
		Version: enrich.PassVersion, RulesHash: currentRulesHash(cfg),
		Gates: enrich.GatesVersion, Reason: reason,
	}, true
}

// enrichStateDir is where enrichment keeps its side records — the run record
// and the refusal record, which a person debugging a night reads together.
func enrichStateDir(cfg *config.Config) string {
	if cfg.EngineStateDir != "" {
		return cfg.EngineStateDir
	}
	return filepath.Dir(cfg.IndexPath)
}

// enrichRunsPath is where the record lives.
func enrichRunsPath(cfg *config.Config) string {
	return filepath.Join(enrichStateDir(cfg), "enrich-runs.jsonl")
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
// not cards, whose `session`, `day` and `touched` a rewrite would drop. Nor is
// the watchlist offered: it lives in the project space
// (`Projects/agentm/_watchlist/`), outside every class directory, and its
// `pending-review` entries are forward_learning.py's records. Neither may spend
// the night's budget.
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

// enrichNeighbours finds the notes the deep pass is shown beside a card: the
// nearest five by the index's own ranker, title and summary only.
//
// Lexical fusion over the card's title and tags — the shipped ranker's
// lexical arm, which needs no embedder in a one-shot process and tolerates a
// long title where an all-terms match would return nothing. The dense arm is
// the re-audit if `related` on enriched cards turns out thin.
//
// Three things are never offered, because the titles and summaries go into a
// model's prompt: the card itself, a derived class, and any note in a space no
// background model may read. A neighbour whose text carries a credential shape
// is skipped for the same reason the privacy gate refuses a card.
func enrichNeighbours(cfg *config.Config, idx *index.Index,
	mayRead func(string) bool) func(context.Context, enrich.Request) []enrich.Neighbour {
	privacy := enrich.DefaultPrivacy()
	return func(_ context.Context, req enrich.Request) []enrich.Neighbour {
		q := neighbourQuery(req.Raw)
		if q == "" {
			return nil
		}
		out, err := idx.Search(index.Query{Text: q, K: 20, Mode: index.ModeFusion})
		if err != nil {
			return nil
		}
		var ns []enrich.Neighbour
		for _, res := range out.Results {
			if res.Path == req.Rel || !strings.HasSuffix(res.Path, ".md") ||
				!mayRead(res.Path) || underDerivedClass(res.Path) {
				continue
			}
			raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(res.Path)))
			if err != nil {
				continue
			}
			n := enrich.Neighbour{
				ID:      strings.TrimSuffix(path.Base(res.Path), ".md"),
				Rel:     res.Path,
				Title:   enrich.FrontmatterValue(string(raw), "title"),
				Summary: enrich.FrontmatterValue(string(raw), "summary"),
			}
			if n.Title == "" {
				n.Title = n.ID
			}
			if n.Summary == "" {
				n.Summary = leadOf(string(raw), 240)
			}
			if !privacy.Clean(n.Title + "\n" + n.Summary) {
				continue
			}
			ns = append(ns, n)
			if len(ns) == enrich.MaxRelated {
				break
			}
		}
		return ns
	}
}

// neighbourQuery is a card's title and tags as search terms: lower-case words
// of three letters or more, stopwords out, at most eight. Keywords rather than
// the card's prose, because the ranker's cost tracks how common its terms are.
func neighbourQuery(raw string) string {
	text := enrich.FrontmatterValue(raw, "title") + " " +
		strings.Trim(enrich.FrontmatterValue(raw, "tags"), "[]")
	seen := map[string]bool{}
	var terms []string
	for _, w := range strings.FieldsFunc(strings.ToLower(text), func(r rune) bool {
		return !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9')
	}) {
		if len(w) < 3 || neighbourStopwords[w] || seen[w] {
			continue
		}
		seen[w] = true
		terms = append(terms, w)
		if len(terms) == 8 {
			break
		}
	}
	return strings.Join(terms, " ")
}

var neighbourStopwords = map[string]bool{
	"the": true, "and": true, "for": true, "with": true, "that": true, "this": true,
	"from": true, "into": true, "not": true, "are": true, "was": true, "its": true,
	"you": true, "your": true, "can": true, "but": true, "how": true, "what": true,
	"when": true, "why": true, "who": true, "one": true, "all": true, "has": true,
}

// underDerivedClass reports whether a path sits in a derived class directory.
func underDerivedClass(rel string) bool {
	for _, seg := range strings.Split(rel, "/") {
		if enrich.DerivedClasses[strings.ToLower(seg)] {
			return true
		}
	}
	return false
}

// leadOf is the first n characters of a note's prose, flattened — a stand-in
// summary for a neighbour that carries none.
func leadOf(raw string, n int) string {
	body := raw
	if strings.HasPrefix(raw, "---") {
		if i := strings.Index(raw[3:], "\n---"); i >= 0 {
			body = raw[3+i+4:]
		}
	}
	var keep []string
	for _, l := range strings.Split(body, "\n") {
		if t := strings.TrimSpace(l); t != "" && !strings.HasPrefix(t, "#") {
			keep = append(keep, t)
		}
	}
	s := strings.Join(keep, " ")
	if len(s) > n {
		s = s[:n]
		if i := strings.LastIndexByte(s, ' '); i > n/2 {
			s = s[:i]
		}
		s += "…"
	}
	return s
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
