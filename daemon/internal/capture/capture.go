// Package capture implements the one operation that must never fail for an
// interesting reason.
//
// Capture is: write the file, upsert the index, done. No model call, no network,
// works with the laptop offline. This is what makes ambient capture safe at any
// volume — the mechanism that makes something exist and findable never waits on
// judgment. Filing (which space, what it links to, whether it duplicates
// something) is a separate asynchronous pass, and its lag is a staleness cost
// rather than a loss, because principle 3's round trip is already satisfied the
// moment capture finishes.
//
// Collapsing the two is what caused both of the previous system's failures: the
// version that filed synchronously blocked capture on network availability, and
// the version that filed nothing left 82% of what it captured invisible.
package capture

import (
	"errors"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync/atomic"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/extract"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Request is one capture.
type Request struct {
	Text  string `json:"text"`
	Title string `json:"title,omitempty"`
	Type  string `json:"type,omitempty"`
	// Status is accepted for callers that have not moved yet, and is no longer
	// authoritative. Status is derived from what the writer knew — see
	// knowingWriter — because a caller-asserted `active` is precisely the
	// Python lane's bug: it wrote `active` at low confidence, a state
	// enrichment's gate never reads, and the corpus ended up saying "judged"
	// about 74 notes nothing had judged. A supplied value that disagrees with
	// the derivation is reported back in Result.Note rather than obeyed or
	// swallowed.
	Status string `json:"status,omitempty"`
	// Summary is one line: what this is and when it applies. Written at
	// capture, refined by the deep pass.
	Summary string `json:"summary,omitempty"`
	// Why is the reasoning — what was happening when this was kept, and what
	// it decides later. Only a writer that knows may write it: the operator or
	// the model that was in the room. No pass, gate or fallback invents one,
	// because a reason guessed from the note reads exactly like a real one and
	// the field is only worth having if it is always the room's.
	//
	// It is also the judgment signal. A card with a `why` and a named type is
	// a card someone stood behind; a card without is a candidate the night
	// still has to judge.
	Why string `json:"why,omitempty"`
	// Importance is the operator-read 1-10 reading. A capture-time value is a
	// *proposal*: it is written to both `importance` and `importance_proposed`
	// so that a later edit to `importance` differs from the proposal, which is
	// what marks the value as the operator's and stops any pass overwriting it.
	Importance int `json:"importance,omitempty"`
	// Related are the notes this one sits beside — `supersedes` and
	// `superseded_by` live in their own fields. Rendered as wikilinks so
	// Obsidian resolves them from the properties panel.
	Related []string `json:"related,omitempty"`
	// Project and Task are the binding the card was captured under: the
	// project slug and the task's verb-slug. Stamped by every writer that
	// knows its binding, absent from every writer that does not — this door
	// takes them and never infers them.
	Project string `json:"project,omitempty"`
	Task    string `json:"task,omitempty"`
	// Instructions is the security-boundary field, moved here so the one front
	// door carries the one rule: this door stores exactly the string it is
	// handed and never inspects Text to derive one. The ingest sweep executes
	// a matching instruction under a fixed grammar, so a value derived from
	// note *content* would be an execution path for whatever that content
	// says. A caller that populates it from anything but the operator's own
	// capture-time text breaks the invariant at the call site.
	//
	// Deliberately absent from the published tool schema, for the same reason
	// Probe is: a model that can see the field will eventually fill it, and
	// the only safe writer is an operator-typed surface (the CLI's
	// --instructions, the clipper).
	Instructions string   `json:"instructions,omitempty"`
	Tags         []string `json:"tags,omitempty"`
	Aliases      []string `json:"aliases,omitempty"`
	// Source is the transport the material arrived by — one of the contract's
	// `sources` vocabulary. Where it came from goes in SourceID or SourceURL.
	Source string `json:"source,omitempty"`
	// SourceID is a registry identity (`<namespace>:<ref>`) and SourceURL the
	// address of a fetched page. Either one names the unit; `source:` names how
	// it arrived. Splitting them is the provenance ruling of 2026-09-06: one
	// field answering two questions let a URL sit where the trust tier looks.
	SourceID  string `json:"source_id,omitempty"`
	SourceURL string `json:"source_url,omitempty"`
	// SourceHash and SourceVersion are the rest of the provenance — what the
	// source contained when it was read, and the pass that read it. Written into
	// the note so the source registry can be rebuilt from the corpus rather than
	// being the only copy of what has already been mined.
	SourceHash    string `json:"source_hash,omitempty"`
	SourceVersion string `json:"source_version,omitempty"`
	Space         string `json:"space,omitempty"`

	// Probe marks the note as the daemon's synthetic self-probe.
	//
	// It is a wire field, because the probe deliberately captures over the same
	// HTTP surface every other client uses — an in-process shortcut would reach
	// past the wiring the probe exists to test. It is deliberately absent from
	// the published tool schema, so no model volunteers it: a probe note is
	// excluded from measurements and retired by the next run, and neither is
	// something a session should be able to ask for by accident.
	Probe bool `json:"probe,omitempty"`
}

// Result is what the caller gets back.
type Result struct {
	Path     string `json:"path"`
	Slug     string `json:"slug"`
	Type     string `json:"type"`
	Status   string `json:"status"`
	Captured string `json:"captured"`
	Indexed  bool   `json:"indexed"`
	// Note carries anything the caller should know that is not an error — a
	// defaulted type, a slug that had to be disambiguated.
	Note string `json:"note,omitempty"`
}

var slugScrubRe = regexp.MustCompile(`[^a-z0-9]+`)

// Altitude is the axis ranking dampens on: `canonical` states something durable —
// a convention, a decided rule, a reference fact — while `artifact` records a
// moment. Capture always writes the default, because a note earns `canonical`
// from a later judgment rather than by asserting it about itself.
const DefaultAltitude = "artifact"

// Statuses capture may land in. `unfiled` means no judgment has been made;
// `active` means one has. That is the whole vocabulary, and it means the same
// thing for every writer in the system.
//
// A note that lands `unfiled` is rank-penalized but fully indexed and
// searchable: there is no inbox, and rank-penalized is a very different
// condition from absent.
var validStatuses = map[string]bool{"unfiled": true, "active": true}

// MaxImportance is the top of the operator-read 1-10 reading.
const MaxImportance = 10

// knowingWriter reports whether this capture came from a writer that judged it
// — a named type and a `why`. Both, because either alone is not a judgment: a
// type is a filing decision, and a `why` on an untyped note says the writer
// knew what it meant but not where it goes.
//
// This is the one place status is decided. Every writer in the system now
// agrees on it, which is what the field being worth reading depends on.
func knowingWriter(noteType, why string) bool {
	return strings.TrimSpace(noteType) != "" && strings.TrimSpace(why) != ""
}

// Capturer writes captures into a vault and its index.
type Capturer struct {
	cfg *config.Config
	idx *index.Index

	// refused counts captures rejected because the caller named a type and there
	// was no contract to validate it against. Counted rather than only logged:
	// a broken contract refusing one client's every write is the quietest way
	// this system can be broken, and a number on the status surface is what
	// makes it a fact instead of a hunch.
	refused atomic.Int64
	// capped counts captures the volume gate turned away (filing v2, task 4).
	// Its own number: a flood being stopped and a contract being broken are
	// different facts, and the status surface should say which.
	capped atomic.Int64
}

// RefusedCaptures is how many captures the missing contract has cost since boot.
func (c *Capturer) RefusedCaptures() int64 { return c.refused.Load() }

// RefusedByVolume is how many captures the daily cap has turned away.
func (c *Capturer) RefusedByVolume() int64 { return c.capped.Load() }

// DefaultDailyWriteCap applies when the contract names no cap (an older
// contract, or none at all). The same number the Python writers default to.
const DefaultDailyWriteCap = 200

// dailyWriteCap is the contract's `thresholds.daily_write_cap`: 0 disables the
// gate, absence means the default. A halted contract still gates — a flood is
// a flood whether or not the rules parse.
func dailyWriteCap(contract *rules.Rules, contractErr error) int {
	if contractErr != nil || contract == nil {
		return DefaultDailyWriteCap
	}
	v, ok := contract.Thresholds["daily_write_cap"]
	if !ok {
		return DefaultDailyWriteCap
	}
	if v <= 0 {
		return 0
	}
	return int(v)
}

// trustTier is the write-time trust stamp (filing v2, task 5). A source that
// names one of the contract's transports takes that transport's tier; a
// source that is a URL — a page the sources pass mined — is external content
// and untrusted whatever it says; anything else earns no stamp rather than a
// guess.
// unit is what the note says it was distilled from, in the order a reader
// resolves it: the registry identity, the fetched page, then the legacy
// overloaded `source:`.
func (d noteData) unit() string {
	for _, v := range []string{d.SourceID, d.SourceURL, d.Source} {
		if v != "" {
			return v
		}
	}
	return ""
}

func trustTier(contract *rules.Rules, contractErr error, source string) string {
	if source == "" {
		return ""
	}
	if contractErr == nil && contract != nil {
		if tier, ok := contract.Sources[source]; ok {
			return tier
		}
	}
	lower := strings.ToLower(source)
	if strings.HasPrefix(lower, "http://") || strings.HasPrefix(lower, "https://") {
		return "untrusted"
	}
	return ""
}

func dayStart(t time.Time) time.Time {
	t = t.UTC()
	return time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)
}

func New(cfg *config.Config, idx *index.Index) *Capturer {
	return &Capturer{cfg: cfg, idx: idx}
}

// Do performs one capture. The file is written before the index is touched,
// because the file is truth and the index is a cache: if the process dies between
// the two, the reconcile pass picks the note up and nothing is lost. The reverse
// ordering could index a note that does not exist.
// tallyTemplate is the retired miner's tool-tally body — "The `Bash` tool was
// invoked 2592 times during this session." followed by an instruction to capture
// the sequence, which nothing ever did. 292 of them were purged as manifest A
// (agentm-vault, landing group 02). The miner is resolved against the session's
// working directory, so a worktree cut from an older base still runs the old
// one; the gate therefore sits at the door, where a stale writer cannot get
// past it. It matches the template and nothing wider: a door that guesses costs
// a real capture.
var tallyTemplate = regexp.MustCompile("The `[^`]+` tool was invoked [0-9]+ times during this session\\.")

func (c *Capturer) Do(req Request) (Result, error) {
	text := strings.TrimSpace(req.Text)
	if text == "" {
		return Result{}, errors.New("text is required — capture needs something to remember")
	}
	if tallyTemplate.MatchString(req.Title + "\n" + text) {
		c.refused.Add(1)
		return Result{}, errors.New("this body is the retired miner's tool-tally template, " +
			"which counts a tool without recording the sequence or when to use it. It is not " +
			"captured. If there is a workflow here, capture the steps")
	}

	var notes []string

	// The taxonomy comes from the filing contract, not from a list in this
	// binary — a type added to standards/storage-rules.md is accepted here on the
	// next capture, with no release in between.
	//
	// When the contract will not parse, the two halves of this diverge on purpose.
	// A caller who named a type is refused, because validating the claim is
	// exactly what is unavailable and writing it unvalidated is the improvising
	// the fail-closed rule exists to stop. A caller who named none is not: the
	// note lands untyped and `unfiled`, which is the state filing drains anyway,
	// and refusing it would lose a capture over a misplaced colon in a file the
	// capture never needed.
	contract, contractErr := c.cfg.Rules.Get()
	noteType := strings.ToLower(strings.TrimSpace(req.Type))
	// The write-time confidence stamp: a caller who named the type stands
	// behind it; a defaulted or untyped note is the contract's guess and says
	// so, which is what the needs-review reading selects on.
	filingConfidence := "high"
	if noteType == "" {
		filingConfidence = "low"
		if contractErr == nil {
			noteType = contract.DefaultType
			notes = append(notes, fmt.Sprintf(
				"type defaulted to %q; re-typing later is a frontmatter edit with no file move",
				noteType))
		} else {
			notes = append(notes, "filing is halted (the storage rules do not parse), so this "+
				"landed untyped; the next pass over `unfiled` types it")
		}
	} else if contractErr != nil {
		c.refused.Add(1)
		return Result{}, fmt.Errorf("cannot validate type %q — filing is halted: %w",
			noteType, contractErr)
	} else if !contract.IsMemoryType(noteType) {
		return Result{}, fmt.Errorf("type %q is not one of: %s",
			noteType, strings.Join(contract.TypesSorted(), ", "))
	}

	// Status is derived, never asserted. A card whose writer named its type
	// and said why lands `active`; everything else lands `unfiled` and waits
	// for the night to judge it.
	why := strings.TrimSpace(req.Why)
	// Against the type the *caller* named, not the one the contract defaulted
	// to a few lines up. A defaulted type is the contract's guess, and a guess
	// plus a reason is not a writer that knew where the note goes — which is
	// the same distinction `filing_confidence` is drawing.
	status := "unfiled"
	if knowingWriter(req.Type, why) {
		status = "active"
	}
	if asked := strings.ToLower(strings.TrimSpace(req.Status)); asked != "" {
		if !validStatuses[asked] {
			return Result{}, fmt.Errorf(
				`status %q is not one of: active (a card its writer judged), `+
					`unfiled (a candidate nothing has judged yet)`, asked)
		}
		if asked != status {
			notes = append(notes, fmt.Sprintf(
				"status is derived from what the writer knew, so this landed %q rather than "+
					"the %q you asked for; a card lands `active` when it names its type and "+
					"gives a `why`", status, asked))
		}
	}

	importance := req.Importance
	if importance != 0 && (importance < 1 || importance > MaxImportance) {
		return Result{}, fmt.Errorf(
			"importance %d is outside 1-%d; leave it unset rather than guessing",
			importance, MaxImportance)
	}

	title := strings.TrimSpace(req.Title)
	if title == "" {
		title = firstSentence(text)
	}

	spaceDir, err := c.cfg.SpaceDir(req.Space)
	if err != nil {
		return Result{}, err
	}

	captured := time.Now().UTC()
	// The volume gate (filing v2, task 4): the day's writes so far, counted from
	// the index, against the contract's cap. Refused loudly, with the count,
	// the cap and the edit that raises it — a flood is caught at this door
	// rather than discovered in the corpus.
	if cap := dailyWriteCap(contract, contractErr); cap > 0 {
		if n, err := c.idx.CapturedSince(dayStart(captured), spaceDir+"/"); err == nil && n >= cap {
			c.capped.Add(1)
			return Result{}, fmt.Errorf("capture refused: %d memories already written today and "+
				"the daily cap is %d — the volume gate (filing v2) stops a flood at the door; "+
				"if today is real, raise `thresholds.daily_write_cap` in standards/storage-rules.md", n, cap)
		}
	}
	// Class routing (filing v2, the write path). A note whose type the
	// contract knows lands in the class the contract routes that type to —
	// where the corpus migration put everything already home, and where the
	// retrieval gate and the scorecard read. The date shard remains only for
	// a note the contract cannot place (filing halted, the note untyped): it
	// has to land somewhere, and a year/month folder is at least an honest
	// "not yet filed" rather than a class it was never judged into.
	dir := spaceDir
	if class := classDir(contract, contractErr, noteType, spaceDir); class != "" {
		dir = class
	} else if c.cfg.Shard == "date" {
		dir = filepath.ToSlash(filepath.Join(spaceDir,
			captured.Format("2006"), captured.Format("01")))
	}

	base := slugify(title)
	if base == "" {
		base = slugify(text)
	}
	if base == "" {
		base = "memory"
	}
	if len(base) > 72 {
		base = strings.Trim(base[:72], "-")
	}

	rel, slug, err := c.reserve(dir, base)
	if err != nil {
		return Result{}, err
	}
	if slug != base {
		notes = append(notes, fmt.Sprintf("slug %q was taken; used %q", base, slug))
	}

	// Derived aliases, merged with whatever the caller supplied. Deterministic
	// regex over the note's own text — acronyms in both directions and compound
	// identifiers decomposed — so nothing here is invented, only surfaced in a
	// form the indexes can match. The caller's own aliases come first, because a
	// caller who named one meant it and the cap should never drop it in favour of
	// a fragment.
	aliases := mergeAliases(req.Aliases, extract.Aliases(title, text))

	body := renderNote(noteData{
		Type:             noteType,
		Altitude:         DefaultAltitude,
		Status:           status,
		Lifecycle:        "active",
		FilingConfidence: filingConfidence,
		Trust:            trustTier(contract, contractErr, strings.TrimSpace(req.Source)),
		Captured:         captured,
		Slug:             slug,
		Title:            title,
		Summary:          strings.TrimSpace(req.Summary),
		Why:              why,
		Importance:       importance,
		Related:          req.Related,
		Project:          strings.TrimSpace(req.Project),
		Task:             strings.TrimSpace(req.Task),
		Instructions:     strings.TrimSpace(req.Instructions),
		Tags:             req.Tags,
		Aliases:          aliases,
		Source:           strings.TrimSpace(req.Source),
		SourceID:         strings.TrimSpace(req.SourceID),
		SourceURL:        strings.TrimSpace(req.SourceURL),
		SourceHash:       strings.TrimSpace(req.SourceHash),
		SourceVersion:    strings.TrimSpace(req.SourceVersion),
		Probe:            req.Probe,
		Text:             text,
	})

	abs := filepath.Join(c.cfg.VaultPath, filepath.FromSlash(rel))
	if err := writeAtomic(abs, body); err != nil {
		return Result{}, fmt.Errorf("writing %s: %w", rel, err)
	}

	res := Result{
		Path:     rel,
		Slug:     slug,
		Type:     noteType,
		Status:   status,
		Captured: captured.Format(time.RFC3339),
		Note:     strings.Join(notes, "; "),
	}

	info, statErr := os.Stat(abs)
	var mtimeNS, size int64
	if statErr == nil {
		mtimeNS, size = info.ModTime().UnixNano(), info.Size()
	}
	parsed := note.Parse(rel, body, captured)
	if err := c.idx.Upsert(parsed, mtimeNS, size); err != nil {
		// The file is on disk, so the memory exists and the reconcile pass will
		// index it. Say so plainly rather than reporting a failure that would
		// invite the caller to retry and write a duplicate.
		res.Note = strings.TrimSpace(res.Note + fmt.Sprintf(
			"; written to disk but not yet indexed (%v); the next reconcile pass "+
				"will pick it up — do not re-capture", err))
		return res, nil
	}
	res.Indexed = true

	// Capture ends here, and nothing fires from this path. The eager trigger
	// spent a model call per note as it landed; it was never attached to a
	// running daemon and never fired once. Enrichment is a nightly batch now —
	// the same work in one place, under a budget, with a report — and the note
	// waits for it exactly as written here.
	return res, nil
}

// reserve picks a free path for `base` in `dir`, creating the directory. The
// suffix loop is what keeps two captures in the same minute from silently
// overwriting each other.
func (c *Capturer) reserve(dir, base string) (rel, slug string, err error) {
	absDir := filepath.Join(c.cfg.VaultPath, filepath.FromSlash(dir))
	if err := os.MkdirAll(absDir, 0o755); err != nil {
		return "", "", fmt.Errorf("creating %s: %w", dir, err)
	}
	for i := 1; i <= 500; i++ {
		slug = base
		if i > 1 {
			slug = fmt.Sprintf("%s-%d", base, i)
		}
		rel = filepath.ToSlash(filepath.Join(dir, slug+".md"))
		if _, err := os.Stat(filepath.Join(c.cfg.VaultPath, filepath.FromSlash(rel))); errors.Is(err, os.ErrNotExist) {
			return rel, slug, nil
		}
	}
	return "", "", fmt.Errorf("could not find a free slug for %q in %s", base, dir)
}

// writeAtomic writes via a temp file in the same directory and renames, so a
// reader — Obsidian, the watcher, a reconcile pass — never sees a half-written
// note.
func writeAtomic(abs, body string) error {
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(abs), ".agentmd-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)

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
	if err := os.Chmod(tmpName, 0o644); err != nil {
		return err
	}
	return os.Rename(tmpName, abs)
}

type noteData struct {
	Type     string
	Altitude string
	Status   string
	// Lifecycle and FilingConfidence are the write-time stamps the filing
	// contract added with the write path: `active` until a later note
	// supersedes this one, and how far the writer trusted its own typing.
	Lifecycle        string
	FilingConfidence string
	// Trust is how far to believe where the note came from — the contract's
	// tier for a named transport, `untrusted` for anything fetched by URL.
	Trust    string
	Captured time.Time
	Slug     string
	Title    string
	// Summary, Why, Importance, Related, Project and Task are the operator-read
	// half of the card. Why is written only when the writer knew one; the
	// renderer never manufactures any of them from the body.
	Summary    string
	Why        string
	Importance int
	Related    []string
	Project    string
	Task       string
	// Instructions is stored verbatim — see Request.Instructions for the rule
	// this field exists to keep in one place.
	Instructions string
	Tags         []string
	Aliases      []string
	Source       string
	// SourceID and SourceURL name the unit the note came from — a registry
	// identity or a fetched page's address. `Source` names the transport.
	SourceID  string
	SourceURL string
	// SourceHash and SourceVersion complete the provenance: what the source
	// contained when it was read, and the pass that read it. Cheap to write now
	// and impossible to reconstruct later, which is what makes the source
	// registry a cache rather than the only copy of what has been mined.
	SourceHash    string
	SourceVersion string
	Probe         bool
	Text          string
}

// renderNote writes the frontmatter contract from the memory design. `captured` is
// immutable and determines the shard; `source` is cheap to write now and
// expensive to reconstruct later, which is what makes "why do you believe this?"
// answerable.
func renderNote(d noteData) string {
	var b strings.Builder
	b.WriteString("---\n")
	fmt.Fprintf(&b, "type: %s\n", d.Type)
	fmt.Fprintf(&b, "status: %s\n", d.Status)
	if d.Lifecycle != "" {
		fmt.Fprintf(&b, "lifecycle: %s\n", d.Lifecycle)
	}
	// Written rather than left implied. `artifact` is what a note is until
	// something judges otherwise, and a field that is present and default is a
	// field a later pass can change in place — an absent one has to be
	// distinguished from a deliberate one first.
	fmt.Fprintf(&b, "altitude: %s\n", d.Altitude)
	// `created`, not `captured`. One field names the day a memory came into
	// existence, and the corpus carried two spellings of it — 30% one, 26% the
	// other. Readers stay tolerant of both while the backfill runs; this
	// writer emits only the one that survives.
	fmt.Fprintf(&b, "created: %s\n", d.Captured.Format(index.CapturedFormat()))
	fmt.Fprintf(&b, "updated: %s\n", d.Captured.Format(index.CapturedFormat()))
	fmt.Fprintf(&b, "slug: %s\n", d.Slug)
	if d.Title != "" {
		fmt.Fprintf(&b, "title: %s\n", yamlScalar(d.Title))
	}
	if d.Summary != "" {
		fmt.Fprintf(&b, "summary: %s\n", yamlScalar(d.Summary))
	}
	if d.Why != "" {
		fmt.Fprintf(&b, "why: %s\n", yamlScalar(d.Why))
	}
	// Both fields, from one value. `importance` is what you read and edit;
	// `importance_proposed` is the reading as proposed. Writing them equal at
	// capture is what makes a later edit legible as yours — the deep pass
	// leaves an `importance` that differs from the proposal alone.
	if d.Importance != 0 {
		fmt.Fprintf(&b, "importance: %d\n", d.Importance)
	}
	if len(d.Tags) > 0 {
		fmt.Fprintf(&b, "tags: [%s]\n", strings.Join(cleanList(d.Tags), ", "))
	}
	if len(d.Aliases) > 0 {
		fmt.Fprintf(&b, "aliases: [%s]\n", strings.Join(quoteList(d.Aliases), ", "))
	}
	// Wikilinks in a quoted flow list: `related: ["[[a]]", "[[b]]"]`. Quoted
	// because a bare `[[a]]` is a nested YAML sequence rather than a link, and
	// this file has to parse strictly; wrapped because a caller that passed a
	// slug meant the note, and a plain string is not a link Obsidian resolves.
	if len(d.Related) > 0 {
		fmt.Fprintf(&b, "related: [%s]\n", strings.Join(quoteList(wikilinks(d.Related)), ", "))
	}
	if d.Project != "" {
		fmt.Fprintf(&b, "project: %s\n", yamlScalar(d.Project))
	}
	if d.Task != "" {
		fmt.Fprintf(&b, "task: %s\n", yamlScalar(d.Task))
	}
	if d.Source != "" {
		fmt.Fprintf(&b, "source: %s\n", yamlScalar(d.Source))
	}
	if d.SourceID != "" {
		fmt.Fprintf(&b, "source_id: %s\n", yamlScalar(d.SourceID))
	}
	if d.SourceURL != "" {
		fmt.Fprintf(&b, "source_url: %s\n", yamlScalar(d.SourceURL))
	}
	// Only alongside a unit. A hash with nothing to hash names no unit, and a
	// rebuild reading one would recover a row keyed on nothing. The unit is
	// whichever field names it — the reference fields since the provenance
	// ruling, `source:` for a caller that has not moved yet.
	if d.unit() != "" && d.SourceHash != "" {
		fmt.Fprintf(&b, "source_hash: %s\n", yamlScalar(d.SourceHash))
	}
	if d.unit() != "" && d.SourceVersion != "" {
		fmt.Fprintf(&b, "source_version: %s\n", yamlScalar(d.SourceVersion))
	}
	if d.FilingConfidence != "" {
		fmt.Fprintf(&b, "filing_confidence: %s\n", d.FilingConfidence)
	}
	if d.Trust != "" {
		fmt.Fprintf(&b, "trust: %s\n", d.Trust)
	}
	if d.Instructions != "" {
		fmt.Fprintf(&b, "instructions: %s\n", yamlScalar(d.Instructions))
	}
	if d.Importance != 0 {
		fmt.Fprintf(&b, "importance_proposed: %d\n", d.Importance)
	}
	// The probe marker. Written as a frontmatter field rather than expressed by
	// where the note lives, because everything downstream that must not count a
	// synthetic note in a measurement reads frontmatter and none of it should
	// have to know a path convention.
	if d.Probe {
		fmt.Fprintf(&b, "%s: %s\n", note.ProbeMarker, note.ProbeMarkerValue)
	}
	b.WriteString("---\n\n")
	b.WriteString(d.Text)
	if !strings.HasSuffix(d.Text, "\n") {
		b.WriteString("\n")
	}
	return b.String()
}

func cleanList(in []string) []string {
	var out []string
	seen := map[string]bool{}
	for _, s := range in {
		s = strings.TrimSpace(s)
		if s == "" || seen[s] {
			continue
		}
		seen[s] = true
		out = append(out, s)
	}
	sort.Strings(out)
	return out
}

// wikilinks wraps each entry in `[[ ]]` unless it already is one, so a caller
// may pass either a slug or a link and the note ends up with a link either way.
// A `.md` suffix is dropped: the link target is the note's name, and Obsidian
// resolves `[[foo]]` where `[[foo.md]]` dangles.
func wikilinks(in []string) []string {
	out := make([]string, 0, len(in))
	for _, s := range in {
		s = strings.TrimSpace(s)
		if s == "" {
			continue
		}
		if !strings.HasPrefix(s, "[[") {
			s = "[[" + strings.TrimSuffix(s, ".md") + "]]"
		}
		out = append(out, s)
	}
	return out
}

func quoteList(in []string) []string {
	out := cleanList(in)
	for i, s := range out {
		out[i] = `"` + strings.ReplaceAll(s, `"`, `\"`) + `"`
	}
	return out
}

func yamlScalar(s string) string {
	s = strings.ReplaceAll(s, "\n", " ")
	if strings.ContainsAny(s, `:#[]{}&*!|>'"%@`+"`") || strings.TrimSpace(s) != s {
		return `"` + strings.ReplaceAll(s, `"`, `\"`) + `"`
	}
	return s
}

func slugify(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	s = slugScrubRe.ReplaceAllString(s, "-")
	return strings.Trim(s, "-")
}

// firstSentence is the fallback title. Atomic-by-concept capture means the text is
// usually one claim, so its opening clause is a serviceable name.
func firstSentence(text string) string {
	flat := strings.Join(strings.Fields(text), " ")
	if i := strings.IndexAny(flat, ".!?"); i > 0 && i < 90 {
		return flat[:i]
	}
	words := strings.Fields(flat)
	if len(words) > 12 {
		words = words[:12]
	}
	return strings.Join(words, " ")
}

// mergeAliases merges the caller's aliases with the derived ones, deduped
// case-insensitively and capped.
//
// The caller's come first, and that ordering is about *selection*, not display:
// the emitted line is sorted alphabetically by `cleanList` on the way out, which
// is what makes a frontmatter diff readable. What the ordering decides is who
// survives the cap. A caller who passed an alias meant it, and losing it to a
// decomposed fragment of some identifier would be the wrong trade every time.
func mergeAliases(supplied, derived []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(supplied)+len(derived))
	push := func(list []string) {
		for _, s := range list {
			s = strings.TrimSpace(s)
			if s == "" {
				continue
			}
			key := strings.ToLower(s)
			if seen[key] {
				continue
			}
			seen[key] = true
			out = append(out, s)
		}
	}
	push(supplied)
	push(derived)
	if len(out) > extract.MaxAliases {
		out = out[:extract.MaxAliases]
	}
	return out
}

// classDir is the vault-relative directory the contract routes a memory type
// to, or "" when there is nothing to route by: no contract, no type, or a
// type the routing table does not name.
//
// A routing value is written from one of three vantage points and all three
// have to resolve to the same directory, because the contract is one file and
// the space it describes moves. "memory/semantic" read against the space
// `memory` is already space-rooted; read against the space `Agent/memory` it
// is relative to the *memory root* — the space's parent — and joining it onto
// the space instead produced `Agent/memory/memory/semantic`, a second class
// tree one note deep that the index walked happily because it walks whatever
// is there. "semantic" is relative to the space itself.
//
// The order below is what distinguishes them: a value that already names the
// space wins outright, a value that resolves under the space from the memory
// root is read that way, and only a value that resolves nowhere near the
// space falls through to the space-relative join. Every branch returns a
// directory inside the space, which is the invariant worth having — a routing
// value can no longer place a note outside the space it was captured into.
func classDir(contract *rules.Rules, contractErr error, noteType, spaceDir string) string {
	if contractErr != nil || contract == nil || noteType == "" {
		return ""
	}
	class := strings.Trim(filepath.ToSlash(strings.TrimSpace(contract.Routing[noteType])), "/")
	if class == "" {
		return ""
	}
	space := strings.Trim(filepath.ToSlash(spaceDir), "/")
	if class == space || strings.HasPrefix(class, space+"/") {
		return class
	}
	// Relative to the memory root: the space's parent. Accepted only when it
	// lands back inside the space, so a value that means something else
	// entirely cannot silently escape.
	if root := path.Dir(space); root != "." && root != "/" {
		if cand := path.Join(root, class); cand == space || strings.HasPrefix(cand, space+"/") {
			return cand
		}
	}
	return path.Join(space, class)
}

// ClassDir is classDir, exported for the self-probe. The probe's job is to
// notice when capture files a note somewhere nobody reads, and it can only do
// that if it knows where the note belonged — asking the same resolver capture
// used, rather than naming a directory of its own that would drift from the
// contract the first time a routing value changed.
func ClassDir(contract *rules.Rules, contractErr error, noteType, spaceDir string) string {
	return classDir(contract, contractErr, noteType, spaceDir)
}
