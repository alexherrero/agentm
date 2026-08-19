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
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/note"
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
	Space   string   `json:"space,omitempty"`

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
	noteType := strings.ToLower(strings.TrimSpace(req.Type))
	if noteType == "" {
		if c.cfg.Rules != nil {
			noteType = c.cfg.Rules.DefaultType
			notes = append(notes, fmt.Sprintf(
				"type defaulted to %q; re-typing later is a frontmatter edit with no file move",
				noteType))
		} else {
			notes = append(notes, "filing is halted (the storage rules do not parse), so this "+
				"landed untyped; the next pass over `unfiled` types it")
		}
	} else if c.cfg.Rules == nil {
		return Result{}, fmt.Errorf("cannot validate type %q — filing is halted: %w",
			noteType, c.cfg.RulesErr)
	} else if !c.cfg.Rules.IsMemoryType(noteType) {
		return Result{}, fmt.Errorf("type %q is not one of: %s",
			noteType, strings.Join(c.cfg.Rules.TypesSorted(), ", "))
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
	dir := spaceDir
	if c.cfg.Shard == "date" {
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

	body := renderNote(noteData{
		Type:     noteType,
		Status:   status,
		Captured: captured,
		Slug:     slug,
		Title:    title,
		Tags:     req.Tags,
		Aliases:  req.Aliases,
		Source:   strings.TrimSpace(req.Source),
		Probe:    req.Probe,
		Text:     text,
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
	Status   string
	Captured time.Time
	Slug     string
	Title    string
	Tags     []string
	Aliases  []string
	Source   string
	Probe    bool
	Text     string
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
