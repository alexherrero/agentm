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
	"github.com/alexherrero/agentm/daemon/internal/note"
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
	// Records are project records the night merged into (agentm-vault plan 09).
	// Not filed, so counted apart from active and below the floor.
	Records int `json:"records"`
	// Ideas are idea cards the night thought about (agentm-vault part 13). The
	// operator filed them and the pass writes no verdict on them, so they are
	// not "filed active" by this run and are counted apart, like the records.
	Ideas int `json:"ideas"`
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
	// ByJob is the night's spend by the tier table's job name — the deep
	// pass's `classify-unfiled` beside the light pass's `summarize` — which is
	// what the morning note's per-job spend lines read.
	ByJob      map[string]enrich.Usage `json:"by_job,omitempty"`
	TokenLines map[string]int64        `json:"token_lines"`
	CallGuard  int                     `json:"call_guard"`
	StoppedBy  string                  `json:"stopped_by,omitempty"`
	Cursor     string                  `json:"cursor,omitempty"`
	ElapsedSec float64                 `json:"elapsed_seconds"`
	Verdicts   enrichVerdicts          `json:"verdicts"`
	Errors     []string                `json:"errors,omitempty"`
}

func newEnrichRun(rep enrich.BatchReport, v enrichVerdicts, model string,
	b enrich.Budget) enrichRun {
	return enrichRun{
		At: time.Now().UTC(), Model: model, PassVersion: enrich.PassVersion,
		Considered: rep.Considered, Enriched: rep.Enriched, Skipped: rep.Skipped,
		Refused: rep.Refused,
		Failed:  rep.Failed, NotesSent: rep.Calls, ModelCalls: rep.ModelCalls,
		Tokens: rep.Tokens, TotalCostUSD: rep.TotalCostUSD, Usage: rep.Usage,
		ByJob:      rep.ByJob,
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

// enrichRecordQueue is every indexed project record, in path order: a project's
// charter and the notes under its decisions/, designs/ and research/
// (agentm-vault § Projects and tasks). Never a tracker, a plan or a progress
// log — enrich.IsProjectRecord is the one place that says so.
func enrichRecordQueue(idx *index.Index) ([]string, error) {
	all, err := idx.Paths()
	if err != nil {
		return nil, err
	}
	var out []string
	for _, p := range all {
		if enrich.IsProjectRecord(p) {
			out = append(out, p)
		}
	}
	sort.Strings(out)
	return out, nil
}

// renameAttempts and renameBackoff bound the retry below. Short on purpose: a
// reader that holds a card for longer than this is not reading it.
const (
	renameAttempts = 8
	renameBackoff  = 15 * time.Millisecond
)

// atomicWrite replaces a file's contents in one step: a temporary sibling,
// fsynced, then renamed over the target.
//
// The sibling is deliberate — a rename is only atomic within a filesystem, and
// a temp directory can be on another one. The fsync before the rename is what
// makes the rename mean something after a crash rather than just before one.
//
// **The rename is retried, and that is Windows.** On POSIX a rename over an
// open file always succeeds; the reader keeps the old inode and sees the old
// bytes whole, which is the property this function exists for. Windows has no
// such thing: `MoveFileEx` with `MOVEFILE_REPLACE_EXISTING` fails
// `ACCESS_DENIED` while any handle is open on the destination without
// `FILE_SHARE_DELETE`, and Go's own `os.Open` does not ask for it. So on
// Windows an ordinary reader — Obsidian, a recall, the indexer — makes the
// write fail where `os.WriteFile` would have succeeded, which would be a
// regression traded for the guarantee rather than added to it. A short bounded
// retry covers the handle a reader holds for the length of one read; past that
// the error is returned, and the applier records a failed write to the ledger
// against a journal entry it wrote *before* the attempt, so nothing is lost
// silently.
//
// The reader's half of the same Windows fact, recorded because somebody should
// find it written down before they find it in a log: a concurrent open can fail
// with a sharing violation for the instant of the replace. That is a loud
// failure a reader retries, not a half-read card, and it is still the better
// half of the trade against `os.WriteFile`'s truncate-and-fill.
func atomicWrite(dest, body string) error {
	dir := filepath.Dir(dest)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(dest)+".*.tmp")
	if err != nil {
		return err
	}
	name := tmp.Name()
	defer os.Remove(name) // a no-op once the rename has taken it
	if _, err := tmp.WriteString(body); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	// Best-effort: Windows has no POSIX mode, and `os.Chmod` there can only
	// move the read-only bit. A card the operator cannot open is not an
	// improvement on a torn one, but neither is refusing to write it.
	_ = os.Chmod(name, 0o644)
	for attempt := 0; ; attempt++ {
		err = os.Rename(name, dest)
		if err == nil || attempt == renameAttempts-1 {
			return err
		}
		time.Sleep(renameBackoff)
	}
}

// enrichInboxDir is the drop folder, as a path prefix: `<memory root>/inbox/`.
// A fourth standard child of `agent/`, not a class directory — which is why it
// needs its own walk rather than joining enrichQueueDirs.
func enrichInboxDir(cfg *config.Config) string {
	return path.Join(cfg.MemoryRoot, "inbox") + "/"
}

// enrichInboxQueue is every indexed note in the drop folder.
//
// Processing an inbox is deciding what to keep, and that decision is fast when
// the card already carries a summary, a `why`, an importance and its
// neighbours, and slow when it carries only what a phone had time to type. The
// job that writes those fields is this one, which is why the night reaches a
// folder the hourly sweep deliberately cannot (agentm-vault plan 16).
func enrichInboxQueue(cfg *config.Config, idx *index.Index) ([]string, error) {
	all, err := idx.Paths()
	if err != nil {
		return nil, err
	}
	dir := enrichInboxDir(cfg)
	var out []string
	for _, p := range all {
		if strings.HasPrefix(p, dir) && strings.HasSuffix(p, ".md") &&
			!strings.HasPrefix(path.Base(p), ".") {
			out = append(out, p)
		}
	}
	return out, nil
}

// enrichIdeasQueue is every indexed idea card: the flat folder at the vault
// root's `personal/ideas/`, and nothing else under `personal/`
// (agentm-vault part 13).
//
// An idea card is a card, so it joins the cards' tier rather than getting one of
// its own: the operator filed it, it is not waiting for a judgment the way an
// inbox card is, and it is not a project record. What is different about it is
// what the night may write, and that is the stamp's business (OperatorFiled),
// not the queue's.
func enrichIdeasQueue(idx *index.Index) ([]string, error) {
	all, err := idx.Paths()
	if err != nil {
		return nil, err
	}
	var out []string
	for _, p := range all {
		if enrich.IsIdeaCard(p) {
			out = append(out, p)
		}
	}
	return out, nil
}

// orderByAge sorts one tier oldest first, by the note's own age, with the path
// as the tie-break so the order is total and two runs agree.
//
// A missing age cannot happen — index.PathAges falls back to the file's mtime
// — but a path the snapshot does not hold sorts last rather than first, because
// "we do not know how long this has been waiting" is not a claim that it has
// been waiting the longest.
func orderByAge(paths []string, ages map[string]string) []string {
	out := append([]string(nil), paths...)
	sort.SliceStable(out, func(i, j int) bool {
		ai, oki := ages[out[i]]
		aj, okj := ages[out[j]]
		if oki != okj {
			return oki
		}
		if ai != aj {
			return ai < aj
		}
		return out[i] < out[j]
	})
	return out
}

// enrichServeOrder is the night's queue, in the order it is served: the inbox
// first, then the cards in the contract's class directories, then the project
// records — and each tier oldest first.
//
// **Tiers, where there were none.** The queue used to be the cards in path
// order followed by the records in path order, which meant a card's position
// was decided by its filename. Two things were wrong with that. The inbox is
// fed from a phone and its contents are the one population whose size the
// operator does not control, so a card waiting there for a judgment sat behind
// eight hundred notes that were not waiting for anything; and inside a tier,
// path order served `004-fix-…` ahead of a card that had been in the judgment
// queue for nine days.
//
// **One budget, with a priority order**, rather than a ceiling of its own: the
// second number would have to be kept in step with the first forever, and a
// night that runs long has one place to look.
//
// `records` is false for the ledger's eligible population, which counts cards
// — the inbox is one, a project record is not.
func enrichServeOrder(cfg *config.Config, idx *index.Index, records bool) ([]string, error) {
	ages, err := idx.PathAges()
	if err != nil {
		return nil, err
	}
	inbox, err := enrichInboxQueue(cfg, idx)
	if err != nil {
		return nil, err
	}
	dirs, err := enrichQueueDirs(cfg)
	if err != nil {
		return nil, err
	}
	cards, err := enrichQueue(idx, dirs)
	if err != nil {
		return nil, err
	}
	// The idea cards are cards: one tier with the class directories' cards,
	// served oldest first among them.
	ideas, err := enrichIdeasQueue(idx)
	if err != nil {
		return nil, err
	}
	cards = append(cards, ideas...)
	out := append(orderByAge(inbox, ages), orderByAge(cards, ages)...)
	if !records {
		return out, nil
	}
	recs, err := enrichRecordQueue(idx)
	if err != nil {
		return nil, err
	}
	return append(out, orderByAge(recs, ages)...), nil
}

// queueStart is the position a cursor resumes at: the one after the cursor's
// own. A cursor the queue no longer holds resumes at the first path that sorts
// after it, which is where the path-ordered pager this replaces would have
// started, and at the end of the queue when nothing sorts after it.
//
// A function of its own because two callers have to agree on it — the lister,
// which pages from here, and the dry run, which counts from here. The dry run
// used to compare paths instead (`rel <= cursor`), and a path comparison is not
// this: the records queue after the cards, so as soon as a card's path sorts
// after a record's, the two answer with different populations and the dry run
// sizes a night the run does not run.
func queueStart(queue []string, cursor string) int {
	if cursor == "" {
		return 0
	}
	for i, p := range queue {
		if p == cursor {
			return i + 1
		}
	}
	// A cursor the queue no longer holds. This used to resume at the first path
	// that sorted after it, which was meaningful while the queue was in path
	// order and stopped being so when it gained tiers and an oldest-first order
	// inside each (agentm-vault plan 16): "the first path lexically after the
	// one that vanished" now names an arbitrary position, several tiers from
	// where the run actually was.
	//
	// So: start at the top. Nothing is re-enriched by that — the fingerprint
	// gate refuses an unchanged note before any call exists — so a restart
	// costs a walk over the queue, not a night's budget.
	return 0
}

// queueAfter pages the queue by position: the `limit` paths after `cursor`, or
// from the start when the cursor is empty.
func queueAfter(queue []string, cursor string, limit int) []string {
	start := queueStart(queue, cursor)
	if limit < 0 {
		limit = 0
	}
	end := start + limit
	if end > len(queue) {
		end = len(queue)
	}
	return append([]string{}, queue[start:end]...)
}

// projectOfRequest is the project a note's neighbours are drawn from first: a
// record's by its path, a card's by its `project:` (agentm-vault § Projects and
// tasks: a card with project: is enriched with its project's records as
// neighbours first).
func projectOfRequest(req enrich.Request) string {
	if p := enrich.ProjectOf(req.Rel); p != "" {
		return p
	}
	return enrich.FrontmatterValue(req.Raw, "project")
}

// projectFirst puts the project's own records ahead of every other neighbour,
// keeping the ranker's order within each half, then caps the list.
func projectFirst(ns []enrich.Neighbour, project string, max int) []enrich.Neighbour {
	if project != "" {
		var own, rest []enrich.Neighbour
		for _, n := range ns {
			if strings.EqualFold(enrich.ProjectOf(n.Rel), project) {
				own = append(own, n)
			} else {
				rest = append(rest, n)
			}
		}
		ns = append(own, rest...)
	}
	if len(ns) > max {
		ns = ns[:max]
	}
	return ns
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
		// A background pass looking for neighbours, not anybody reading:
		// `measure` so a nightly enrichment does not hold every note it
		// considered at day zero.
		out, err := idx.Search(index.Query{Text: q, K: 40, Mode: index.ModeFusion,
			Surface: note.SurfaceMeasure})
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
		}
		// The whole window is kept until here so a project's records, wherever
		// they ranked in it, can lead before the list is cut to the nearest five.
		return projectFirst(ns, projectOfRequest(req), enrich.MaxRelated)
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
