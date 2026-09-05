package dreaming

import (
	"bufio"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// The journal is the pass's ground truth: one JSON object per line, appended
// and fsynced before the mutation it describes is made, never rewritten.
// A crash between the `intent` line and the write leaves an intent with no
// `applied` line; the next start replays exactly those, and the replay is
// safe because every intent carries the hashes of the note before and after
// — a target that already hashes as `after` was applied and is recorded as
// such, a target that still hashes as `before` is applied now, and anything
// else is a conflict that is skipped and reported, never guessed at.

// Entry kinds.
const (
	KindRunStart = "run-start"
	KindIntent   = "intent"
	KindApplied  = "applied"
	KindSkipped  = "skipped"
	KindRunDone  = "run-done"
)

// Entry is one journal line.
type Entry struct {
	Kind  string    `json:"kind"`
	RunID string    `json:"run_id"`
	TS    time.Time `json:"ts"`
	// Mode is `apply` or `report`, on run-start.
	Mode string `json:"mode,omitempty"`
	// ID keys an intent; its applied/skipped line carries the same ID.
	ID  string `json:"id,omitempty"`
	Job string `json:"job,omitempty"`
	// Rel is the vault-relative path the intent mutates.
	Rel        string `json:"rel,omitempty"`
	BeforeHash string `json:"before_hash,omitempty"`
	AfterHash  string `json:"after_hash,omitempty"`
	// After is the whole new content, base64 — small notes, exact replay.
	After   string `json:"after,omitempty"`
	Summary string `json:"summary,omitempty"`
	Note    string `json:"note,omitempty"`
	// Outcome summarizes a run on run-done.
	Outcome string `json:"outcome,omitempty"`
}

// Journal is an append-only file.
type Journal struct {
	Path string
}

// JournalPath is `<engine state dir>/dreaming/journal.jsonl`.
func JournalPath(engineStateDir string) string {
	return filepath.Join(Dir(engineStateDir), "journal.jsonl")
}

// OpenJournal creates the directory and returns the journal (the file is
// created on the first append).
func OpenJournal(engineStateDir string) (*Journal, error) {
	if err := os.MkdirAll(Dir(engineStateDir), 0o755); err != nil {
		return nil, err
	}
	return &Journal{Path: JournalPath(engineStateDir)}, nil
}

// Append writes one line and fsyncs it. The write that follows an intent
// must not begin until this returns: journal before write, always.
func (j *Journal) Append(e Entry) error {
	if e.TS.IsZero() {
		e.TS = time.Now().UTC()
	}
	blob, err := json.Marshal(e)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(j.Path, os.O_APPEND|os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	// A crash mid-append leaves a torn last line with no newline. Appending
	// straight after it would glue this entry onto the fragment and lose
	// both, so the tail is healed first: the fragment becomes its own
	// (unparseable, dropped) line and this entry starts clean.
	if st, err := f.Stat(); err == nil && st.Size() > 0 {
		last := make([]byte, 1)
		if _, err := f.ReadAt(last, st.Size()-1); err == nil && last[0] != '\n' {
			if _, err := f.Write([]byte{'\n'}); err != nil {
				return err
			}
		}
	}
	if _, err := f.Write(append(blob, '\n')); err != nil {
		return err
	}
	return f.Sync()
}

// Read returns every entry, oldest first. A missing file is an empty journal;
// a torn last line (a crash mid-append) is dropped, since nothing after a
// torn intent line was ever applied.
func (j *Journal) Read() ([]Entry, error) {
	f, err := os.Open(j.Path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	defer f.Close()
	var out []Entry
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 1<<20), 64<<20)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var e Entry
		if err := json.Unmarshal(line, &e); err != nil {
			continue // torn tail
		}
		out = append(out, e)
	}
	return out, sc.Err()
}

// Unfinished finds the last run that started and never finished, and the
// intents in it with no applied or skipped line.
func Unfinished(entries []Entry) (runID string, pending []Entry) {
	start := -1
	for i, e := range entries {
		switch e.Kind {
		case KindRunStart:
			start, runID = i, e.RunID
		case KindRunDone:
			if e.RunID == runID {
				start, runID = -1, ""
			}
		}
	}
	if start < 0 {
		return "", nil
	}
	settled := map[string]bool{}
	for _, e := range entries[start:] {
		if e.RunID == runID && (e.Kind == KindApplied || e.Kind == KindSkipped) {
			settled[e.ID] = true
		}
	}
	for _, e := range entries[start:] {
		if e.RunID == runID && e.Kind == KindIntent && !settled[e.ID] {
			pending = append(pending, e)
		}
	}
	return runID, pending
}

// Hash is the content hash the journal records: sha256 of the bytes, hex.
func Hash(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// Intent describes one mutation before it is made.
type Intent struct {
	Job     string
	Rel     string
	Before  []byte
	After   []byte
	Summary string
}

// ErrConflict is an intent whose target no longer hashes as the intent
// expected: neither before nor after. It is skipped, never forced.
var ErrConflict = errors.New("target changed since the intent was journaled")

// Resolve applies one journaled intent against the vault, idempotently:
// the target that already hashes as `after` is recorded applied (found on
// resume), the target that hashes as `before` is written now, anything else
// is a conflict. Returns the outcome kind written to the journal.
func (j *Journal) Resolve(vault string, e Entry, now time.Time) (string, error) {
	p := filepath.Join(vault, filepath.FromSlash(e.Rel))
	cur, err := os.ReadFile(p)
	if err != nil {
		note := fmt.Sprintf("target unreadable on resume: %v", err)
		return KindSkipped, j.Append(Entry{Kind: KindSkipped, RunID: e.RunID, TS: now, ID: e.ID, Job: e.Job, Rel: e.Rel, Note: note})
	}
	switch Hash(cur) {
	case e.AfterHash:
		return KindApplied, j.Append(Entry{Kind: KindApplied, RunID: e.RunID, TS: now, ID: e.ID, Job: e.Job, Rel: e.Rel, Note: "found applied on resume"})
	case e.BeforeHash:
		after, err := base64.StdEncoding.DecodeString(e.After)
		if err != nil {
			return KindSkipped, j.Append(Entry{Kind: KindSkipped, RunID: e.RunID, TS: now, ID: e.ID, Job: e.Job, Rel: e.Rel, Note: "journaled content undecodable: " + err.Error()})
		}
		if err := writeAtomic(p, after); err != nil {
			return "", err
		}
		return KindApplied, j.Append(Entry{Kind: KindApplied, RunID: e.RunID, TS: now, ID: e.ID, Job: e.Job, Rel: e.Rel, Note: "applied on resume"})
	default:
		return KindSkipped, j.Append(Entry{Kind: KindSkipped, RunID: e.RunID, TS: now, ID: e.ID, Job: e.Job, Rel: e.Rel, Note: ErrConflict.Error()})
	}
}

// Commit journals an intent, makes the write, and journals it applied —
// or journals it skipped when the target changed between the plan and now.
// The intent line is fsynced before the write begins; a crash in between is
// what Resolve exists for. Returns the outcome kind it journaled.
func (j *Journal) Commit(vault, runID string, id string, in Intent, now time.Time) (string, error) {
	if err := j.Append(Entry{
		Kind: KindIntent, RunID: runID, TS: now, ID: id, Job: in.Job, Rel: in.Rel,
		BeforeHash: Hash(in.Before), AfterHash: Hash(in.After),
		After: base64.StdEncoding.EncodeToString(in.After), Summary: in.Summary,
	}); err != nil {
		return "", err
	}
	p := filepath.Join(vault, filepath.FromSlash(in.Rel))
	cur, err := os.ReadFile(p)
	if err != nil {
		return "", err
	}
	if Hash(cur) != Hash(in.Before) {
		return KindSkipped, j.Append(Entry{Kind: KindSkipped, RunID: runID, TS: now, ID: id, Job: in.Job, Rel: in.Rel, Note: ErrConflict.Error()})
	}
	if err := writeAtomic(p, in.After); err != nil {
		return "", err
	}
	return KindApplied, j.Append(Entry{Kind: KindApplied, RunID: runID, TS: now, ID: id, Job: in.Job, Rel: in.Rel})
}

func writeAtomic(p string, content []byte) error {
	tmp := p + ".dreaming.tmp"
	if err := os.WriteFile(tmp, content, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}
