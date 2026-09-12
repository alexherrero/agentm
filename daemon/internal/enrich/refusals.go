package enrich

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

// The record of what the post-gates refused.
//
// A refusal is the one outcome that costs money and leaves nothing behind. A
// card that was enriched carries a stamp. A card a pre-gate declined never
// reached a model. A card whose call failed is owed a retry, because the model
// was the problem rather than the card. But a card whose response a post-gate
// rejected is left exactly as it stood — so the next run reads the same bytes,
// reaches the same conclusion, spends the same two calls, and is refused again.
//
// The night of 2026-09-11 did that to nineteen cards for about eight dollars,
// and the nightly job is enabled, so it would have done it again every night.
// Eligibility is decided by the `enriched_at` stamp, and a refused card never
// gets one; there is no state anywhere that says "we asked about this and the
// answer was no".
//
// This is that state. It is keyed the way the fingerprint gate is keyed — pass
// version, rules hash and body together — because a refusal answers the same
// question the fingerprint answers: has this exact card, under this exact pass,
// already been through this? The card comes back when its body changes or when
// the prompt does, which is when it is genuinely owed another look.
//
// # Why a file beside the run record rather than a ledger row
//
// The coverage ledger looks like the obvious home and is the wrong one. Its
// rebuild wipes a stage's rows and recovers them by walking the corpus, and a
// refusal has nothing in the corpus to recover from — that absence is the
// whole reason it needs recording. A refusal stage would be silently emptied
// by the first `ledger rebuild`, and the corpus would pay for every refusal a
// second time.
//
// # Why not the card's own frontmatter
//
// Because then everything that reads a card's frontmatter has to learn that
// `enriched_*` means the pass ran and produced this, while some new key means
// the pass ran and produced nothing. PassDepth decides deep from light purely
// on whether a stamp is there; a refusal stored beside it would have to be
// excluded by hand in every reader, and the first reader to forget would treat
// a refused card as an enriched one.

// GatesVersion identifies the post-gates that can refuse a response.
//
// Deliberately not part of PassVersion. PassVersion is written into every
// card's frontmatter and folded into the fingerprint key, so moving it re-owes
// the deep pass to the whole corpus. A refusal, though, is as much a fact
// about the gate that made it as about the card: sharpen the judge's question
// and yesterday's refusals stop being answers to today's. Keeping the two
// versions apart means that invalidates the refusals alone — one night
// re-judging the refused set, rather than a full-corpus re-enrichment.
//
// The judge's prompt is hashed in because it is a prompt and it changes like
// one. The alias gate is deterministic code rather than wording, and so is the
// composition of the judge's source, so a change to either is a change to the
// leading number, made by hand in the same commit.
var GatesVersion = "gates/1+faith/" + gatesHash()

func gatesHash() string {
	h := sha256.New()
	fmt.Fprint(h, faithfulnessPrompt)
	return hex.EncodeToString(h.Sum(nil))[:12]
}

// RefusalsName is the file the record lives in, beside the run record.
const RefusalsName = "enrich-refusals.jsonl"

// Refusal is one row: which card, under which key, refused by which gate, and
// what the gate said about it.
//
// The version and the rules hash are written out beside the key even though the
// key already folds them in. The key is a hash and answers only "same or not";
// a person opening this file to ask why a card stopped being offered needs to
// read which pass refused it, and a hash does not say.
type Refusal struct {
	Rel       string    `json:"rel"`
	Gate      string    `json:"gate"`
	Key       string    `json:"key"`
	Version   string    `json:"version"`
	RulesHash string    `json:"rules_hash"`
	Gates     string    `json:"gates"`
	Reason    string    `json:"reason"`
	At        time.Time `json:"at"`
}

// Refusals is the record, held in memory and appended to on disk.
//
// One standing row per card, because a card has one body at a time and so one
// refusal that could still be true. Rows under keys the card no longer has are
// answers to questions nobody will ask again, and Compact drops them.
type Refusals struct {
	mu   sync.Mutex
	path string
	rows map[string]Refusal
}

// NewRefusals reads the record in `dir`, or starts an empty one.
//
// A record that will not parse is not an error the run should die on. The safe
// direction is the expensive one: an unreadable record means nothing is known
// to be refused, so the cards are offered and paid for, which is exactly what
// happens today. The other direction would skip cards on the strength of a
// file it could not read.
func NewRefusals(dir string) (*Refusals, error) {
	r := &Refusals{path: filepath.Join(dir, RefusalsName), rows: map[string]Refusal{}}
	f, err := os.Open(r.path)
	if err != nil {
		if os.IsNotExist(err) {
			return r, nil
		}
		return r, err
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	for {
		var row Refusal
		if err := dec.Decode(&row); err != nil {
			// A truncated last line is what a machine dying mid-append leaves,
			// and every row before it is still good.
			break
		}
		if row.Rel == "" {
			continue
		}
		if prev, ok := r.rows[row.Rel]; ok && prev.At.After(row.At) {
			continue
		}
		r.rows[row.Rel] = row
	}
	return r, nil
}

// Path is where the record lives, for a person looking and for the status
// surface.
func (r *Refusals) Path() string { return r.path }

// Standing answers the pre-gate's question: is this card, at this key, under
// these gates, already refused?
//
// All three have to match. The key covers the pass version, the filing contract
// and the body; the gates version covers the wording of the gate that said no.
// A refusal survives only while every one of them still holds.
func (r *Refusals) Standing(rel, key, gates string) (Refusal, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	row, ok := r.rows[rel]
	if !ok || row.Key != key || row.Gates != gates {
		return Refusal{}, false
	}
	return row, true
}

// Record writes one refusal down, in memory and on disk.
//
// Appended and flushed rather than buffered until the end, for the reason the
// journal gives: the value of this file is being correct when something goes
// wrong, and that is precisely when a buffer is not written. A run killed
// halfway keeps what it learned up to the kill.
func (r *Refusals) Record(row Refusal) error {
	if row.At.IsZero() {
		row.At = time.Now().UTC()
	}
	row.At = row.At.UTC()
	blob, err := json.Marshal(row)
	if err != nil {
		return err
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.rows[row.Rel] = row
	if err := os.MkdirAll(filepath.Dir(r.path), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(r.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	if _, err := f.Write(append(blob, '\n')); err != nil {
		return err
	}
	return f.Sync()
}

// Resolve drops a card's standing refusal, because it has just been enriched.
//
// The row would stop matching on its own — enrichment rewrites the card, so
// its key moves — but only the next time anything computed that key. Dropping
// it here keeps Open honest in between.
func (r *Refusals) Resolve(rel string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	delete(r.rows, rel)
}

// Open is how many cards stand refused under the pass and gates running now.
//
// Rows at an older pass version or an older gates version are not counted:
// those cards will be offered again on their own, which is the mechanism
// working rather than a backlog. The number is as of the last time each card
// was looked at — a run the budget stopped early leaves the cards it never
// reached counted as they stood.
func (r *Refusals) Open(version, rulesHash, gates string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	n := 0
	for _, row := range r.rows {
		if row.Version == version && row.RulesHash == rulesHash && row.Gates == gates {
			n++
		}
	}
	return n
}

// Standings is every row the record holds, in path order, for a reader.
func (r *Refusals) Standings() []Refusal {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]Refusal, 0, len(r.rows))
	for _, row := range r.rows {
		out = append(out, row)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Rel < out[j].Rel })
	return out
}

// Compact rewrites the file as the one standing row per card.
//
// Appending is what makes a refusal survive a crash; compacting is what stops
// the file growing by the whole refused set every time the prompt changes.
// Written to a temporary file and renamed, so a crash during the rewrite leaves
// the previous record intact rather than half of a new one.
func (r *Refusals) Compact() error {
	rows := r.Standings()
	if err := os.MkdirAll(filepath.Dir(r.path), 0o755); err != nil {
		return err
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	tmp, err := os.CreateTemp(filepath.Dir(r.path), "."+RefusalsName+".*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	for _, row := range rows {
		blob, err := json.Marshal(row)
		if err != nil {
			tmp.Close()
			return err
		}
		if _, err := tmp.Write(append(blob, '\n')); err != nil {
			tmp.Close()
			return err
		}
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), r.path)
}

// --- the gate ---------------------------------------------------------------

// GateRefusal is the pre-gate's name, and what a skipped outcome reports.
const GateRefusal = "refusal"

// Refused is the pre-gate that reads the record.
//
// It runs after the fingerprint gate and before the budget: after the
// fingerprint because an unchanged enriched card is the commoner answer and
// the cheaper one, and before the budget because the budget counts a call the
// moment it agrees to one, so anything that can still decline has to have
// declined already.
//
// Key is supplied rather than computed here, and the caller passes the very
// same Fingerprint's method. "Keyed the way the fingerprint gate is keyed" is
// then a fact about the code rather than a claim in a comment: there is one
// implementation of what "the same card under the same pass" means, and both
// gates call it.
type Refused struct {
	// Key is the idempotency key for a body — Fingerprint.Key.
	Key func(body string) string
	// Standing answers whether this card at this key is already refused.
	Standing func(rel, key string) (Refusal, bool)
}

func (g *Refused) Name() string { return GateRefusal }

func (g *Refused) Check(_ context.Context, req Request, body string) error {
	if g.Key == nil || g.Standing == nil {
		return nil
	}
	row, ok := g.Standing(req.Rel, g.Key(body))
	if !ok {
		return nil
	}
	return fmt.Errorf("%w: the %s gate refused this card on %s and neither its "+
		"text nor the prompt has moved since, so asking again would buy the same "+
		"answer — %s", ErrNotEligible, row.Gate,
		row.At.UTC().Format("2006-01-02"), row.Reason)
}
