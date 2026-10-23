// Package ideajudge is the night's idea judge (task 192, backlog #676): it reads
// the idea remarks the sessions recorded and decides, with the conversation
// around each in view, what each one was.
//
// At session end the miner only records. Each idea-shaped remark becomes an
// entry in its trace's `## Ideas` — the words as they were said, about 1,200
// characters of the conversation around them, and a pointer back
// (`episodic_trace.py`). This pass reads those entries, the cards already in
// `personal/ideas/` and the ideas waiting in the inbox, and asks the judge one
// question per remark:
//
//   - **steering** — it directed the session at hand ("we should also update
//     the tests"). Nothing is written; the verdict and its reason are recorded.
//   - **repeat** of a card — an idea already on the list. The remark is added
//     to that card under a dated `## Added by dreaming` heading, with its
//     source, and nothing above it changes.
//   - **new** — an idea worth keeping that is not on the list. It becomes a
//     card in `agent/inbox/`, `unfiled`, the words verbatim, with a proposed
//     title and `area:` for the operator to settle at review.
//
// The pass never creates a card in `personal/ideas/`: filing there is the
// operator's act (`inbox_review.file_one_idea`). It reads only the vault and
// the engine state, never a transcript, which may be gone by then. A remark an
// idea card already holds word for word is settled without a call: a repeat at
// home, or of an idea the operator dismissed. A remark whose card the operator
// or the night removed — the retired lane's filed ledger or the judge's own
// record names a card for these words, and no note carries its name — is
// dropped without a call. A verdict is remembered against the remark, so each
// is judged once; the same words said again the same night share it. The cap
// bounds the calls one night makes, and what it leaves is reported and judged
// next night.
package ideajudge

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode"

	"github.com/alexherrero/agentm/daemon/internal/cardshape"
	"github.com/alexherrero/agentm/daemon/internal/dreaming"
)

const (
	// Job names the pass in the journal and the tier table.
	Job = "judge-ideas"
	// DefaultCap bounds the remarks one night sends to the judge.
	DefaultCap = 20
	// AddedByDreaming heads what the night adds to an idea card.
	AddedByDreaming = "## Added by dreaming"
	// IdeasDirRel is the operator's idea cards, vault-relative.
	IdeasDirRel = dreaming.IdeasDirRel
)

// Verdicts.
const (
	Steering = "steering"
	Repeat   = "repeat"
	New      = "new"
	// Removed is a remark whose card was removed: dropped without a call.
	Removed = "removed"
	// Unjudged is a remark this run did not judge: a dry run, or past the cap.
	Unjudged = "unjudged"
)

// Remark is one entry of a trace's `## Ideas`, with the trace it came from.
type Remark struct {
	// Trace is the trace, relative to the memory root.
	Trace        string `json:"trace"`
	Session      string `json:"session,omitempty"`
	Day          string `json:"day,omitempty"`
	Remark       string `json:"remark"`
	Rule         string `json:"rule,omitempty"`
	FirstRequest bool   `json:"first_request,omitempty"`
	Transcript   string `json:"transcript,omitempty"`
	Message      string `json:"message,omitempty"`
	Context      string `json:"context,omitempty"`
	FiledKey     string `json:"filed_key,omitempty"`
}

// Key is the remark's identity: its session, its message and its words. A
// trace rewritten by a later write of the same session keeps the same keys.
func (r Remark) Key() string {
	sum := sha256.Sum256([]byte(r.Session + "|" + r.Message + "|" + fold(r.Remark)))
	return hex.EncodeToString(sum[:8])
}

// TraceStem is the trace's file name without `.md`.
func (r Remark) TraceStem() string { return strings.TrimSuffix(path.Base(r.Trace), ".md") }

// fold is `candidateKey`'s folding: letters and digits, lower-cased, every
// other run one space.
func fold(s string) string {
	var b strings.Builder
	space := false
	for _, r := range strings.ToLower(s) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			b.WriteRune(r)
			space = false
		} else if !space && b.Len() > 0 {
			b.WriteByte(' ')
			space = true
		}
	}
	return strings.TrimSpace(b.String())
}

// ideasSection is the body of a trace's `## Ideas`, to the next `## ` heading.
func ideasSection(body string) string {
	lines := strings.Split(strings.ReplaceAll(body, "\r\n", "\n"), "\n")
	var out []string
	in := false
	for _, l := range lines {
		if strings.HasPrefix(l, "## ") {
			in = strings.TrimSpace(strings.TrimPrefix(l, "## ")) == "Ideas"
			continue
		}
		if in {
			out = append(out, l)
		}
	}
	return strings.Join(out, "\n")
}

// ParseIdeas reads the entries of a `## Ideas` section: one JSON object a line,
// inside the fence `episodic_trace.render_ideas` writes. A line that is not one
// is skipped, as the Python reader skips it.
func ParseIdeas(section string) []Remark {
	var out []Remark
	sc := bufio.NewScanner(strings.NewReader(section))
	sc.Buffer(make([]byte, 0, 64*1024), 1<<20)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if !strings.HasPrefix(line, "{") {
			continue
		}
		var e struct {
			Remark       string `json:"remark"`
			Rule         string `json:"rule"`
			FirstRequest bool   `json:"first_request"`
			Transcript   string `json:"transcript"`
			Message      string `json:"message"`
			Context      string `json:"context"`
			FiledKey     string `json:"filed_key"`
		}
		if json.Unmarshal([]byte(line), &e) != nil || strings.TrimSpace(e.Remark) == "" {
			continue
		}
		out = append(out, Remark{Remark: strings.TrimSpace(e.Remark), Rule: e.Rule, FirstRequest: e.FirstRequest,
			Transcript: e.Transcript, Message: e.Message, Context: e.Context, FiledKey: e.FiledKey})
	}
	return out
}

// Gather reads every remark in the session traces under
// `<vault>/<memoryRoot>/memory/episodic/`, the oldest day first.
func Gather(vault, memoryRoot string) ([]Remark, error) {
	dir := filepath.Join(vault, filepath.FromSlash(memoryRoot), "memory", "episodic")
	entries, err := os.ReadDir(dir)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var out []Remark
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasSuffix(name, ".md") || strings.HasPrefix(name, ".") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			continue
		}
		fm, body := dreaming.ParseFrontmatter(string(raw))
		if fm["kind"] != "session-trace" {
			continue
		}
		day := fm["day"]
		if day == "" {
			day = fm["created"]
		}
		for _, r := range ParseIdeas(ideasSection(body)) {
			r.Trace = "memory/episodic/" + name
			r.Session, r.Day = fm["session"], day
			out = append(out, r)
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Day != out[j].Day {
			return out[i].Day < out[j].Day
		}
		return out[i].Trace < out[j].Trace
	})
	return out, nil
}

// Card is an idea card the judge can name: one of the operator's in
// `personal/ideas/`, or one waiting in the inbox.
type Card struct {
	// Rel is vault-relative.
	Rel   string `json:"rel"`
	Slug  string `json:"slug"`
	Title string `json:"title"`
	Area  string `json:"area,omitempty"`
	// Inbox marks a card waiting for review.
	Inbox bool `json:"inbox,omitempty"`
	// Dismissed marks an idea the operator dismissed: still named, so a repeat
	// of it is known, and never written to.
	Dismissed bool `json:"dismissed,omitempty"`
	// text is the card's file, for the words it already holds.
	text string
}

// holds reports whether text already carries the remark's words, spacing aside.
func holds(text, remark string) bool {
	words := strings.Join(strings.Fields(remark), " ")
	return words != "" && strings.Contains(strings.Join(strings.Fields(text), " "), words)
}

// heldBy is the card that already holds the remark's words: a live card first,
// then a dismissed one.
func heldBy(cards []Card, remark string) (Card, bool) {
	for _, dismissed := range []bool{false, true} {
		for _, c := range cards {
			if c.Dismissed == dismissed && holds(c.text, remark) {
				return c, true
			}
		}
	}
	return Card{}, false
}

// InboxRel is the drop folder, vault-relative.
func InboxRel(memoryRoot string) string {
	root := strings.Trim(filepath.ToSlash(strings.TrimSpace(memoryRoot)), "/")
	if root == "" {
		return "inbox"
	}
	return root + "/inbox"
}

// Cards is every idea card: `personal/ideas/` (a dismissed idea marked, so a
// repeat of it is known and nothing is written), then the inbox's `type: idea`
// cards.
func Cards(vault, memoryRoot string) ([]Card, error) {
	ideas, err := dreaming.ReadIdeaCards(vault)
	if err != nil {
		return nil, err
	}
	var out []Card
	for _, c := range ideas {
		raw, _ := os.ReadFile(filepath.Join(vault, filepath.FromSlash(c.Rel)))
		out = append(out, Card{Rel: c.Rel, Slug: c.Slug, Title: c.Title, Area: c.Area,
			Dismissed: c.Dismissed != "", text: string(raw)})
	}
	inbox := InboxRel(memoryRoot)
	entries, err := os.ReadDir(filepath.Join(vault, filepath.FromSlash(inbox)))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasSuffix(name, ".md") || strings.HasPrefix(name, ".") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(vault, filepath.FromSlash(inbox), name))
		if err != nil {
			continue
		}
		fm, _ := dreaming.ParseFrontmatter(string(raw))
		if strings.ToLower(strings.TrimSpace(fm["type"])) != "idea" {
			continue
		}
		slug := strings.TrimSuffix(name, ".md")
		title := strings.TrimSpace(fm["title"])
		if title == "" {
			title = slug
		}
		out = append(out, Card{Rel: inbox + "/" + name, Slug: slug, Title: title, Area: fm["area"], Inbox: true,
			text: string(raw)})
	}
	return out, nil
}

// --- the removed set ---------------------------------------------------------

// ReadLedger is every key the session-end lane recorded in its filed ledger
// (`reflect.FILED_LEDGER`, task 186). A missing ledger is empty.
func ReadLedger(path string) (map[string]bool, error) {
	out := map[string]bool{}
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return out, nil
	}
	if err != nil {
		return nil, err
	}
	for _, line := range strings.Split(string(raw), "\n") {
		var row struct {
			Key string `json:"key"`
		}
		if json.Unmarshal([]byte(line), &row) == nil && row.Key != "" {
			out[row.Key] = true
		}
	}
	return out, nil
}

// NoteStems is every note's file name, without `.md` and lower-cased, across
// the vault: a card is found wherever it was filed. Hidden folders are skipped.
func NoteStems(vault string) map[string]bool {
	out := map[string]bool{}
	_ = filepath.WalkDir(vault, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			if p != vault && strings.HasPrefix(d.Name(), ".") {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasSuffix(d.Name(), ".md") {
			out[strings.ToLower(strings.TrimSuffix(d.Name(), ".md"))] = true
		}
		return nil
	})
	return out
}

// WasRemoved reports a remark the lane once filed as a card that is gone now:
// its key is in the ledger and no note carries the key's name. The operator
// removed it, or the night did, and a remark they removed is not filed again.
func WasRemoved(r Remark, ledger, stems map[string]bool) bool {
	if r.FiledKey == "" || !ledger[r.FiledKey] {
		return false
	}
	slug, _, _ := strings.Cut(r.FiledKey, "|")
	return slug != "" && !stems[strings.ToLower(slug)]
}

// wasRemoved is a remark whose card is gone. Either a card the judge made for
// these words on an earlier night (`filed`, by folded words) is carried by no
// note now and no card holds the words whole — the card the judge made held
// them, so one kept under another name still does — or WasRemoved, unless an idea card
// holds a long run of the remark's words: the retired lane's card held only
// the window around its trigger, so one kept under another name goes to the
// judge, which can name it.
func wasRemoved(r Remark, ledger map[string]bool, filed map[string]string, stems map[string]bool, cards []Card) bool {
	if rel := filed[fold(r.Remark)]; rel != "" && !stems[strings.ToLower(strings.TrimSuffix(path.Base(rel), ".md"))] {
		// Gone by its name. Kept, filed or renamed, it still holds the words
		// whole, letters and digits aside from case and punctuation.
		for _, c := range cards {
			if strings.Contains(" "+fold(c.text)+" ", " "+fold(r.Remark)+" ") {
				return false
			}
		}
		return true
	}
	if !WasRemoved(r, ledger, stems) {
		return false
	}
	for _, c := range cards {
		if holdsRun(c.text, r.Remark) {
			return false
		}
	}
	return true
}

// runWords is how many of a remark's words in a row a card must hold to count
// as carrying it: past any stock phrase ("we should also look into the way
// we" is eight), well inside the lane's window of about 160 characters.
const runWords = 12

// holdsRun reports whether text holds runWords of the remark's words in a row
// (all of them, when it has fewer), letters and digits only.
func holdsRun(text, remark string) bool {
	words := strings.Fields(fold(remark))
	if len(words) == 0 {
		return false
	}
	have := " " + fold(text) + " "
	n := runWords
	if len(words) < n {
		n = len(words)
	}
	for i := 0; i+n <= len(words); i++ {
		if strings.Contains(have, " "+strings.Join(words[i:i+n], " ")+" ") {
			return true
		}
	}
	return false
}

// --- the question ------------------------------------------------------------

// SystemPrompt frames the judge.
const SystemPrompt = "You read one remark the operator made to an AI coding agent during a work session, with " +
	"the conversation around it, and decide what it was. The operator keeps a list of ideas: things they might " +
	"build, write or try one day. Most remarks that sound like ideas are steering the session at hand (\"we " +
	"should also update the tests\", \"later we could rename this\"), and those belong to that session only. A " +
	"remark that restates a pasted brief or a standing rule is steering too. Answer with JSON only."

// Prompt is the question put to the judge about one remark.
func Prompt(r Remark, cards []Card) string {
	var b strings.Builder
	b.WriteString("## The remark\n\n" + r.Remark + "\n")
	if r.FirstRequest {
		b.WriteString("\n(It is in the session's first request, which may be a brief pasted to start the work.)\n")
	}
	b.WriteString("\n## The conversation around it\n\n")
	ctx := strings.TrimSpace(r.Context)
	if ctx == "" {
		ctx = "(none recorded)"
	}
	b.WriteString(ctx + "\n\n## The ideas already on the list\n\n")
	if len(cards) == 0 {
		b.WriteString("(none)\n")
	}
	for _, c := range cards {
		line := "- " + c.Slug + " — " + c.Title
		if c.Area != "" {
			line += " [" + c.Area + "]"
		}
		if c.Inbox {
			line += " (waiting for review)"
		}
		if c.Dismissed {
			line += " (dismissed by the operator)"
		}
		b.WriteString(line + "\n")
	}
	b.WriteString("\nDecide: \"steering\" when the remark directs the work of this session, or restates a brief " +
		"or a rule; \"repeat\" when it is an idea already on the list, dismissed or not (name its slug); \"new\" when it is an idea " +
		"worth keeping that is not on the list. Reply with exactly one JSON object: " +
		`{"verdict": "steering" | "repeat" | "new", "slug": "<the list's slug, for repeat>", ` +
		`"title": "<a short title, for new>", "area": "<one lower-case word, for new>", "reason": "<one sentence>"}.`)
	return b.String()
}

// Verdict is the judge's answer about one remark.
type Verdict struct {
	Verdict string `json:"verdict"`
	Slug    string `json:"slug,omitempty"`
	Title   string `json:"title,omitempty"`
	Area    string `json:"area,omitempty"`
	Reason  string `json:"reason,omitempty"`
}

// ParseVerdict reads the judge's verdict out of its reply: the first of the
// reply's top-level JSON objects that is one. A `[[link]]` or an array around
// it, or a note after it holding a brace, does not spoil it; an object nested
// in another, or a brace inside a string, is never taken for it.
func ParseVerdict(out string) (Verdict, error) {
	err := errors.New("no JSON object in the judge's reply")
	for _, obj := range topLevelObjects(out) {
		var v Verdict
		if uerr := json.Unmarshal([]byte(obj), &v); uerr != nil {
			err = uerr
			continue
		}
		v.Verdict = strings.ToLower(strings.TrimSpace(v.Verdict))
		switch v.Verdict {
		case Steering, Repeat, New:
			v.Slug, v.Title, v.Area = strings.TrimSpace(v.Slug), strings.TrimSpace(v.Title), strings.TrimSpace(v.Area)
			return v, nil
		}
		err = fmt.Errorf("verdict %q is none of steering, repeat, new", v.Verdict)
	}
	return Verdict{}, err
}

// topLevelObjects is each `{…}` of s that sits outside any other, its braces
// balanced and its strings respected. Only a brace that opens like an object
// (`{"` or `{}`) starts one, so a brace in prose never swallows the verdict
// after it; a brace that never closes is skipped and the scan goes on after it.
func topLevelObjects(s string) []string {
	var out []string
	for i := 0; i < len(s); i++ {
		if s[i] != '{' || !opensObject(s[i+1:]) {
			continue
		}
		depth, inString, escaped, end := 0, false, false, -1
		for j := i; j < len(s) && end < 0; j++ {
			c := s[j]
			switch {
			case inString && escaped:
				escaped = false
			case inString && c == '\\':
				escaped = true
			case inString && c == '"':
				inString = false
			case inString:
			case c == '"':
				inString = true
			case c == '{':
				depth++
			case c == '}':
				depth--
				if depth == 0 {
					end = j
				}
			}
		}
		if end < 0 {
			continue
		}
		out = append(out, s[i:end+1])
		i = end
	}
	return out
}

// opensObject reports whether the text after a `{` begins as a JSON object's
// body does: a key, or the brace that closes an empty one.
func opensObject(rest string) bool {
	rest = strings.TrimLeft(rest, " \t\r\n")
	return strings.HasPrefix(rest, `"`) || strings.HasPrefix(rest, "}")
}

// --- the writes --------------------------------------------------------------

var areaRe = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,39}$`)

// sourceLine is an addition's dated, sourced heading line.
func sourceLine(r Remark, today string) string {
	src := "[[" + r.TraceStem() + "]]"
	if r.Message != "" {
		m := r.Message
		if len(m) > 8 {
			m = m[:8]
		}
		src += " · message " + m
	}
	return "**" + today + " · " + src + "**"
}

// WithAddition is a card's text with the remark added under `## Added by
// dreaming`: appended at the end of that section when the card has one, else
// as a new section at the end of the card. Nothing already on the card moves or
// changes. "" when the card already holds the words.
func WithAddition(current string, r Remark, today string) string {
	if strings.TrimSpace(r.Remark) == "" || holds(current, r.Remark) {
		return ""
	}
	entry := sourceLine(r, today) + "\n\n" + strings.TrimSpace(r.Remark) + "\n"
	at := strings.Index(current, "\n"+AddedByDreaming+"\n")
	if at < 0 {
		return strings.TrimRight(current, "\n") + "\n\n" + AddedByDreaming + "\n\n" + entry
	}
	start := at + 1 + len(AddedByDreaming) + 1
	end := len(current)
	if i := strings.Index(current[start:], "\n## "); i >= 0 {
		end = start + i + 1
	}
	head, tail := current[:end], current[end:]
	if !strings.HasSuffix(head, "\n") {
		head += "\n"
	}
	if !strings.HasSuffix(head, "\n\n") {
		head += "\n"
	}
	out := head + entry
	if tail != "" {
		out += "\n" + tail
	}
	return out
}

func yamlQuote(s string) string {
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", " ").Replace(s) + `"`
}

// slugWords is a text's kebab words.
func slugWords(s string) []string {
	f := fold(s)
	if f == "" {
		return nil
	}
	return strings.Fields(f)
}

// InboxCard is a new idea as a card in the inbox: `type: idea`, `unfiled`, the
// remark verbatim as its body, the judge's title and area as a proposal, and
// the trace it came from in `derived_from` — with the traces of `also`, the
// same words said again in other sessions.
func InboxCard(r Remark, v Verdict, slug, today string, also ...Remark) string {
	title := v.Title
	if title == "" {
		title = r.Remark
	}
	if rs := []rune(title); len(rs) > 100 {
		title = strings.TrimSpace(string(rs[:100])) + "…"
	}
	var b strings.Builder
	b.WriteString("---\n")
	b.WriteString("title: " + yamlQuote(title) + "\n")
	b.WriteString("type: idea\n")
	if areaRe.MatchString(v.Area) {
		b.WriteString("area: " + v.Area + "\n")
	}
	b.WriteString("status: unfiled\n")
	b.WriteString("lifecycle: active\n")
	b.WriteString("filing_confidence: low\n")
	b.WriteString("source: conversation\n")
	b.WriteString("trust: trusted\n")
	b.WriteString("created: " + today + "\n")
	b.WriteString("updated: " + today + "\n")
	if r.Session != "" {
		b.WriteString("session: " + yamlQuote(r.Session) + "\n")
	}
	var from []string
	seen := map[string]bool{}
	for _, x := range append([]Remark{r}, also...) {
		if stem := x.TraceStem(); !seen[stem] {
			seen[stem] = true
			from = append(from, "\"[["+stem+"]]\"")
		}
	}
	b.WriteString("derived_from: [" + strings.Join(from, ", ") + "]\n")
	b.WriteString("slug: " + slug + "\n")
	b.WriteString("---\n\n")
	b.WriteString(strings.TrimSpace(r.Remark) + "\n")
	return cardshape.Reorder(b.String())
}

// freeSlug is a name for a new inbox card no note in the inbox or the ideas
// folder holds: the title's first words, grown by the remark's next meaningful
// words, else ending in six characters of the remark's hash.
func freeSlug(title, remark string, taken func(string) bool) string {
	words := slugWords(title)
	if len(words) == 0 {
		words = slugWords(remark)
	}
	if len(words) > 8 {
		words = words[:8]
	}
	// No longer than a filed card's name may be (`card_shape.SLUG_MAX`), so
	// filing the card keeps the name the judge remembers it by.
	for len(words) > 1 && len(strings.Join(words, "-")) > slugMax {
		words = words[:len(words)-1]
	}
	base := strings.Join(words, "-")
	if len(base) > slugMax {
		base = base[:slugMax]
	}
	if base == "" {
		base = "idea"
	}
	if !taken(base) {
		return base
	}
	held := map[string]bool{}
	for _, w := range words {
		held[w] = true
	}
	grown := base
	for _, w := range slugWords(remark) {
		if held[w] || !cardshape.Meaningful(w) {
			continue
		}
		held[w] = true
		grown += "-" + w
		if len(grown) > slugMax {
			break
		}
		if !taken(grown) {
			return grown
		}
	}
	sum := sha256.Sum256([]byte(remark))
	if len(base) > slugMax-7 {
		base = strings.TrimRight(base[:slugMax-7], "-")
	}
	return base + "-" + hex.EncodeToString(sum[:3])
}

// slugMax is the longest name a card is filed under (`card_shape.SLUG_MAX`).
const slugMax = 72

// --- one night ---------------------------------------------------------------

// State is what the judge remembers between nights, by remark key.
type State struct {
	Verdicts map[string]Judged `json:"verdicts"`
	// Filed is each inbox card the judge made, vault-relative, by the folded
	// words it holds: when no note carries its name any more, the operator
	// removed it, and the same words are not carded again.
	Filed map[string]string `json:"filed,omitempty"`
}

// Judged is one remark's verdict, and whether its write landed.
type Judged struct {
	Verdict
	JudgedAt time.Time `json:"judged_at"`
	// Rel is the card the verdict wrote, vault-relative, once it has.
	Rel       string    `json:"rel,omitempty"`
	AppliedAt time.Time `json:"applied_at,omitempty"`
	// Home marks a repeat whose words the card already held: settled, and
	// nothing was written for it.
	Home bool `json:"home,omitempty"`
	// Dismissed marks a repeat of an idea the operator dismissed: settled,
	// and nothing is written to it.
	Dismissed bool `json:"dismissed,omitempty"`
}

// Row is one remark in a run's report.
type Row struct {
	Key     string `json:"key"`
	Trace   string `json:"trace"`
	Remark  string `json:"remark"`
	Verdict string `json:"verdict"`
	Slug    string `json:"slug,omitempty"`
	Reason  string `json:"reason,omitempty"`
	Cached  bool   `json:"cached,omitempty"`
	// Rel is the card this verdict writes (or wrote), vault-relative.
	Rel string `json:"rel,omitempty"`
	// Applied is a verdict whose write landed, this run or an earlier one.
	Applied bool `json:"applied,omitempty"`
	// Home is a repeat whose words the card already held, so nothing was
	// written for it.
	Home bool `json:"home,omitempty"`
	// Dismissed is a repeat of an idea the operator dismissed: nothing is
	// written.
	Dismissed bool `json:"dismissed,omitempty"`
}

// Options carry one night's inputs.
type Options struct {
	Vault, MemoryRoot string
	Cap               int
	Now               time.Time
	// Call asks the judge; nil asks nothing (a dry run).
	Call   func(prompt string) (string, error)
	Ledger map[string]bool
}

// Plan is one night's verdicts and the writes they make.
type Plan struct {
	Remarks  int      `json:"remarks"`
	Calls    int      `json:"calls"`
	Cached   int      `json:"cached"`
	Removed  int      `json:"removed"`
	Deferred int      `json:"deferred"`
	Rows     []Row    `json:"rows"`
	Errors   []string `json:"errors,omitempty"`
	// Intents are the writes, memory-root-relative, for dreaming.ApplyIntents.
	Intents []dreaming.Intent `json:"-"`
	// Writes names, per intent, the remark keys it carries.
	Writes [][]string `json:"-"`
}

// Count is how many rows hold a verdict.
func (p Plan) Count(verdict string) int {
	n := 0
	for _, r := range p.Rows {
		if r.Verdict == verdict {
			n++
		}
	}
	return n
}

// memRel is a vault-relative path as the journal keys it, relative to the
// memory root.
func memRel(memoryRoot, vaultRel string) string {
	root := strings.Trim(filepath.ToSlash(memoryRoot), "/")
	if root == "" {
		return vaultRel
	}
	if strings.HasPrefix(vaultRel, root+"/") {
		return strings.TrimPrefix(vaultRel, root+"/")
	}
	up := strings.Repeat("../", strings.Count(root, "/")+1)
	return up + vaultRel
}

// Decide judges the night's remarks and plans their writes. It writes nothing:
// the caller applies the plan's intents, or reports them. `st` is updated with
// every new verdict, so a remark judged in a reporting night is not judged
// again when the writes are switched on.
func Decide(opt Options, st *State) (Plan, error) {
	var plan Plan
	if st.Verdicts == nil {
		st.Verdicts = map[string]Judged{}
	}
	cap := opt.Cap
	if cap <= 0 {
		cap = DefaultCap
	}
	now := opt.Now
	if now.IsZero() {
		now = time.Now().UTC()
	}
	today := now.UTC().Format("2006-01-02")
	remarks, err := Gather(opt.Vault, opt.MemoryRoot)
	if err != nil {
		return plan, err
	}
	cards, err := Cards(opt.Vault, opt.MemoryRoot)
	if err != nil {
		return plan, err
	}
	bySlug := map[string]Card{}
	for _, c := range cards {
		bySlug[c.Slug] = c
	}
	stems := NoteStems(opt.Vault)
	plan.Remarks = len(remarks)

	seen := map[string]bool{}
	// tonight is the verdict an earlier remark of the same words holds this run,
	// judged now or remembered and not yet written: the same words said in two
	// sessions are one question and one card.
	tonight := map[string]Judged{}
	type pending struct {
		key string
		r   Remark
		v   Judged
	}
	var toWrite []pending
	for _, r := range remarks {
		key := r.Key()
		if seen[key] {
			continue
		}
		seen[key] = true
		row := Row{Key: key, Trace: r.Trace, Remark: r.Remark, Verdict: Unjudged}
		words := fold(r.Remark)
		j, cached := st.Verdicts[key]
		if cached && j.Verdict.Verdict == Repeat && j.AppliedAt.IsZero() && !j.Home && !j.Dismissed {
			if _, ok := bySlug[j.Slug]; !ok {
				// The card a remembered repeat named is gone since — renamed, or
				// removed — so the remark is judged again against today's list.
				delete(st.Verdicts, key)
				j, cached = Judged{}, false
			}
		}
		prior, again := tonight[words]
		held, isHeld := heldBy(cards, r.Remark)
		switch {
		case cached:
			row.Cached = true
			plan.Cached++
		case isHeld:
			// A card already holds these words: a repeat at home, settled
			// without a call. A dismissed card holding them settles it the same.
			j = Judged{Verdict: Verdict{Verdict: Repeat, Slug: held.Slug,
				Reason: "the card already holds these words"}, JudgedAt: now, Rel: held.Rel, Home: true,
				Dismissed: held.Dismissed}
			st.Verdicts[key] = j
		case wasRemoved(r, opt.Ledger, st.Filed, stems, cards):
			j = Judged{Verdict: Verdict{Verdict: Removed,
				Reason: "a card filed for these words was removed; not filed again"}, JudgedAt: now}
			st.Verdicts[key] = j
			plan.Removed++
		case again && words != "":
			j = Judged{Verdict: prior.Verdict, JudgedAt: now}
			st.Verdicts[key] = j
		case opt.Call == nil:
		case plan.Calls >= cap:
			plan.Deferred++
		default:
			plan.Calls++
			out, err := opt.Call(Prompt(r, cards))
			if err == nil {
				var v Verdict
				if v, err = ParseVerdict(out); err == nil {
					// A repeat of a card that is not on the list is a new idea:
					// the inbox is where an idea the judge could not place waits.
					if v.Verdict == Repeat {
						if _, ok := bySlug[v.Slug]; !ok {
							v.Reason = strings.TrimSpace(v.Reason + " (the judge named " + v.Slug +
								", which is not on the list; kept as a new idea)")
							v.Verdict, v.Slug = New, ""
						}
					}
					j = Judged{Verdict: v, JudgedAt: now}
					st.Verdicts[key] = j
				}
			}
			if err != nil {
				plan.Errors = append(plan.Errors, fmt.Sprintf("%s (%s): %v", r.Trace, key, err))
			}
		}
		if j.Verdict.Verdict != "" {
			row.Verdict, row.Slug, row.Reason, row.Rel = j.Verdict.Verdict, j.Slug, j.Reason, j.Rel
			row.Home, row.Dismissed = j.Home, j.Dismissed
			row.Applied = !j.AppliedAt.IsZero() && !j.Home
		}
		plan.Rows = append(plan.Rows, row)
		if (row.Verdict == Repeat || row.Verdict == New) && !row.Applied && !row.Home && !row.Dismissed {
			toWrite = append(toWrite, pending{key: key, r: r, v: j})
			if _, ok := tonight[words]; !ok {
				tonight[words] = j
			}
		}
	}

	// The writes. A card several remarks repeat is one intent carrying them
	// all, in order, so each later addition is planned against the earlier;
	// a new idea several remarks say in the same words is one card.
	rowAt := map[string]int{}
	for i, row := range plan.Rows {
		rowAt[row.Key] = i
	}
	type edit struct {
		before, after string
		// keys are the remarks this write adds; home, those the card held.
		keys, home []string
	}
	edits := map[string]*edit{}
	var editOrder []string
	type fresh struct {
		v    Verdict
		rs   []Remark
		keys []string
	}
	var news []*fresh
	newByWords := map[string]*fresh{}
	for _, p := range toWrite {
		i := rowAt[p.key]
		switch p.v.Verdict.Verdict {
		case Repeat:
			card, ok := bySlug[p.v.Slug]
			if !ok {
				plan.Errors = append(plan.Errors, fmt.Sprintf("%s (%s): no card %q", p.r.Trace, p.key, p.v.Slug))
				continue
			}
			plan.Rows[i].Rel = card.Rel
			if card.Dismissed {
				// A repeat of an idea the operator dismissed: settled, and the
				// card is not written to.
				plan.Rows[i].Dismissed = true
				j := st.Verdicts[p.key]
				j.Rel, j.Dismissed = card.Rel, true
				st.Verdicts[p.key] = j
				continue
			}
			e := edits[card.Rel]
			if e == nil {
				raw, err := os.ReadFile(filepath.Join(opt.Vault, filepath.FromSlash(card.Rel)))
				if err != nil {
					plan.Errors = append(plan.Errors, fmt.Sprintf("%s: %v", card.Rel, err))
					continue
				}
				e = &edit{before: string(raw), after: string(raw)}
				edits[card.Rel] = e
				editOrder = append(editOrder, card.Rel)
			}
			switch next := WithAddition(e.after, p.r, today); {
			case next != "":
				e.after = next
				e.keys = append(e.keys, p.key)
			case holds(e.before, p.r.Remark):
				e.home = append(e.home, p.key)
			default:
				// Words an earlier remark adds tonight: they land with that write.
				e.keys = append(e.keys, p.key)
			}
		case New:
			words := fold(p.r.Remark)
			if f := newByWords[words]; f != nil {
				f.rs, f.keys = append(f.rs, p.r), append(f.keys, p.key)
				continue
			}
			f := &fresh{v: p.v.Verdict, rs: []Remark{p.r}, keys: []string{p.key}}
			newByWords[words] = f
			news = append(news, f)
		}
	}
	reserved := map[string]bool{}
	inbox := InboxRel(opt.MemoryRoot)
	for _, f := range news {
		r := f.rs[0]
		slug := freeSlug(f.v.Title, r.Remark, func(s string) bool {
			return reserved[s] || stems[s] || bySlug[s].Rel != ""
		})
		reserved[slug] = true
		rel := inbox + "/" + slug + ".md"
		for _, k := range f.keys {
			plan.Rows[rowAt[k]].Rel = rel
		}
		plan.Intents = append(plan.Intents, dreaming.Intent{Job: Job, Rel: memRel(opt.MemoryRoot, rel),
			After:   []byte(InboxCard(r, f.v, slug, today, f.rs[1:]...)),
			Summary: "a new idea for your review, from " + r.TraceStem()})
		plan.Writes = append(plan.Writes, f.keys)
	}
	for _, rel := range editOrder {
		e := edits[rel]
		// What the card already held is settled, and nothing is written for it.
		for _, k := range e.home {
			plan.Rows[rowAt[k]].Home = true
			j := st.Verdicts[k]
			j.Rel, j.Home = rel, true
			st.Verdicts[k] = j
		}
		if len(e.keys) == 0 {
			continue
		}
		plan.Intents = append(plan.Intents, dreaming.Intent{Job: Job, Rel: memRel(opt.MemoryRoot, rel),
			Before: []byte(e.before), After: []byte(e.after),
			Summary: fmt.Sprintf("%d repeat(s) of this idea, added under %s", len(e.keys), AddedByDreaming)})
		plan.Writes = append(plan.Writes, e.keys)
	}
	return plan, nil
}

// MarkApplied records the writes that landed: every remark an applied intent
// carried is home, at the card it wrote, and a new card is remembered by the
// words it holds. It returns the cards written, each once, vault-relative.
func MarkApplied(plan *Plan, st *State, appliedRels []string, now time.Time) []string {
	landed := map[string]bool{}
	for _, rel := range appliedRels {
		landed[rel] = true
	}
	rowAt := map[string]int{}
	for i, row := range plan.Rows {
		rowAt[row.Key] = i
	}
	if st.Filed == nil {
		st.Filed = map[string]string{}
	}
	var written []string
	seen := map[string]bool{}
	for i, in := range plan.Intents {
		if !landed[in.Rel] {
			continue
		}
		for _, k := range plan.Writes[i] {
			row := plan.Rows[rowAt[k]]
			j := st.Verdicts[k]
			j.Rel, j.AppliedAt = row.Rel, now
			st.Verdicts[k] = j
			plan.Rows[rowAt[k]].Applied = true
			if row.Verdict == New {
				st.Filed[fold(row.Remark)] = row.Rel
			}
			if !seen[row.Rel] {
				seen[row.Rel] = true
				written = append(written, row.Rel)
			}
		}
	}
	return written
}
