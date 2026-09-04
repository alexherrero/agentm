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
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync/atomic"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/extract"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Request is one capture.
type Request struct {
	Text    string   `json:"text"`
	Title   string   `json:"title,omitempty"`
	Type    string   `json:"type,omitempty"`
	Status  string   `json:"status,omitempty"`
	Tags    []string `json:"tags,omitempty"`
	Aliases []string `json:"aliases,omitempty"`
	Source  string   `json:"source,omitempty"`
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

// Statuses capture may land in. Deliberate capture lands `active` — a session the
// operator directed produces memories he already approved by asking for them, and
// routing those through triage would page him about a backlog that is not one.
// Unattended capture lands `unfiled`, which is rank-penalized but fully indexed
// and searchable: there is no inbox, and rank-penalized is a very different
// condition from absent.
var validStatuses = map[string]bool{"unfiled": true, "active": true}

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

	// enrich is the pass fired after the transaction commits, or nil when
	// enrichment is not configured. Held as a pointer the capture path only ever
	// *hands work to* — it never reads a result and never waits — because the
	// whole guarantee is that no amount of slowness here reaches a capture's
	// latency.
	enrich *enrich.Pass
}

// SetEnrichPass attaches the enrichment pass. Optional: a Capturer without one
// captures exactly as it always did, which is what every test and every
// one-shot command gets.
func (c *Capturer) SetEnrichPass(p *enrich.Pass) { c.enrich = p }

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
func (c *Capturer) Do(req Request) (Result, error) {
	text := strings.TrimSpace(req.Text)
	if text == "" {
		return Result{}, errors.New("text is required — capture needs something to remember")
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

	status := strings.ToLower(strings.TrimSpace(req.Status))
	if status == "" {
		status = "unfiled"
	}
	if !validStatuses[status] {
		return Result{}, fmt.Errorf(
			`status %q is not one of: active (a capture the operator asked for), `+
				`unfiled (anything unattended)`, status)
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
		Tags:             req.Tags,
		Aliases:          aliases,
		Source:           strings.TrimSpace(req.Source),
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

	// The transaction has committed. Enrichment starts now, out of band, and
	// this returns without waiting — see enrich.Pass.FireEager. A note whose
	// enrichment fails, is declined, or never starts stays exactly as written
	// here, `unfiled`, which is the state the nightly batch pass collects.
	if c.enrich != nil {
		c.enrich.FireEager(context.Background(), enrich.Request{
			Rel: rel, Raw: body, AskerPhrasing: req.Text,
		}, nil)
	}
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
	Tags     []string
	Aliases  []string
	Source   string
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
	fmt.Fprintf(&b, "captured: %s\n", d.Captured.Format(index.CapturedFormat()))
	fmt.Fprintf(&b, "updated: %s\n", d.Captured.Format(index.CapturedFormat()))
	fmt.Fprintf(&b, "slug: %s\n", d.Slug)
	if d.Title != "" {
		fmt.Fprintf(&b, "title: %s\n", yamlScalar(d.Title))
	}
	if len(d.Tags) > 0 {
		fmt.Fprintf(&b, "tags: [%s]\n", strings.Join(cleanList(d.Tags), ", "))
	}
	if len(d.Aliases) > 0 {
		fmt.Fprintf(&b, "aliases: [%s]\n", strings.Join(quoteList(d.Aliases), ", "))
	}
	if d.Source != "" {
		fmt.Fprintf(&b, "source: %s\n", yamlScalar(d.Source))
	}
	// Only alongside a source. A hash with nothing to hash names no unit, and
	// a rebuild reading one would recover a row keyed on nothing.
	if d.Source != "" && d.SourceHash != "" {
		fmt.Fprintf(&b, "source_hash: %s\n", yamlScalar(d.SourceHash))
	}
	if d.Source != "" && d.SourceVersion != "" {
		fmt.Fprintf(&b, "source_version: %s\n", yamlScalar(d.SourceVersion))
	}
	if d.FilingConfidence != "" {
		fmt.Fprintf(&b, "filing_confidence: %s\n", d.FilingConfidence)
	}
	if d.Trust != "" {
		fmt.Fprintf(&b, "trust: %s\n", d.Trust)
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
// type the routing table does not name. A routing value is accepted either
// relative to the vault ("memory/semantic") or to the space ("semantic").
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
	return filepath.ToSlash(filepath.Join(spaceDir, class))
}
