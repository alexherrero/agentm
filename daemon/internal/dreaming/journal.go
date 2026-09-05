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
	Rel string `json:"rel,omitempty"`
	// To is set on a move: the note leaves Rel and lands at To with After.
	To string `json:"to,omitempty"`
	// Create is set when Rel did not exist before: the intent makes a note.
	Create     bool   `json:"create,omitempty"`
	BeforeHash string `json:"before_hash,omitempty"`
	AfterHash  string `json:"after_hash,omitempty"`
	// After is the whole new content, base64 — small notes, exact replay.
	After   string `json:"after,omitempty"`
	Summary string `json:"summary,omitempty"`
	Note    string `json:"note,omitempty"`
	// Meta is the job's own facts about the intent — for a lifecycle move
	// its from/to/reason — so a resume can write the governance line the
	// crashed pass did not get to.
	Meta map[string]string `json:"meta,omitempty"`
	// Outcome summarizes a run on run-done.
	Outcome string `json:"outcome,omitempty"`
}

// Journal is an append-only file.
type Journal struct {
	Path string
	// crashBeforeApplied, when set, is a test's stand-in for a kill between
	// the governance line and the applied line: Commit returns its error
	// instead of writing the applied line.
	crashBeforeApplied func() error
	// EngineStateDir is where the governance journal lives, for the lines a
	// resume owes.
	EngineStateDir string
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
	return &Journal{Path: JournalPath(engineStateDir), EngineStateDir: engineStateDir}, nil
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

// Intent describes one mutation before it is made: an edit of Rel in
// place (Before → After), a move (Rel leaves, To lands with After — the
// re-file), or a creation (Before nil: Rel did not exist — the promotion).
type Intent struct {
	Job     string
	Rel     string
	To      string
	Before  []byte
	After   []byte
	Summary string
	Meta    map[string]string
}

// ErrConflict is an intent whose target no longer hashes as the intent
// expected: neither before nor after. It is skipped, never forced.
var ErrConflict = errors.New("target changed since the intent was journaled")

// Resolve applies one journaled intent against the vault, idempotently:
// the target that already hashes as `after` is recorded applied (found on
// resume), the target that hashes as `before` is written now, anything else
// is a conflict. A move resolves on both paths (the source gone and the
// destination at `after` is applied; the source at `before` and no
// destination is applied now; anything else is left alone), a creation on
// the one path it makes. Returns the outcome kind written to the journal.
func (j *Journal) Resolve(vault string, e Entry, now time.Time) (string, error) {
	settle := func(kind, note string) (string, error) {
		if kind == KindApplied {
			// The governance line the crashed pass may not have written.
			// Idempotent: keyed by run, note and state, so a line it did
			// write is not written twice.
			if err := j.governance(e, now); err != nil {
				return "", err
			}
		}
		return kind, j.Append(Entry{Kind: kind, RunID: e.RunID, TS: now, ID: e.ID, Job: e.Job, Rel: e.Rel, To: e.To, Note: note})
	}
	after, err := base64.StdEncoding.DecodeString(e.After)
	if err != nil {
		return settle(KindSkipped, "journaled content undecodable: "+err.Error())
	}
	src := filepath.Join(vault, filepath.FromSlash(e.Rel))
	switch {
	case e.To != "":
		dst := filepath.Join(vault, filepath.FromSlash(e.To))
		cur, srcErr := os.ReadFile(src)
		got, dstErr := os.ReadFile(dst)
		switch {
		case srcErr != nil && dstErr == nil && Hash(got) == e.AfterHash:
			return settle(KindApplied, "found applied on resume")
		case srcErr == nil && dstErr != nil && Hash(cur) == e.BeforeHash:
			if err := writeAtomic(dst, after); err != nil {
				return "", err
			}
			if err := os.Remove(src); err != nil {
				return "", err
			}
			return settle(KindApplied, "applied on resume")
		default:
			return settle(KindSkipped, ErrConflict.Error())
		}
	case e.Create:
		got, err := os.ReadFile(src)
		switch {
		case err == nil && Hash(got) == e.AfterHash:
			return settle(KindApplied, "found applied on resume")
		case os.IsNotExist(err):
			if err := writeAtomic(src, after); err != nil {
				return "", err
			}
			return settle(KindApplied, "applied on resume")
		default:
			return settle(KindSkipped, ErrConflict.Error())
		}
	}
	cur, err := os.ReadFile(src)
	if err != nil {
		return settle(KindSkipped, fmt.Sprintf("target unreadable on resume: %v", err))
	}
	switch Hash(cur) {
	case e.AfterHash:
		return settle(KindApplied, "found applied on resume")
	case e.BeforeHash:
		if err := writeAtomic(src, after); err != nil {
			return "", err
		}
		return settle(KindApplied, "applied on resume")
	default:
		return settle(KindSkipped, ErrConflict.Error())
	}
}

// governance writes the lifecycle journal line an applied lifecycle intent
// owes, once.
func (j *Journal) governance(e Entry, now time.Time) error {
	if e.Job != JobLifecycle || e.Meta == nil || j.EngineStateDir == "" {
		return nil
	}
	return EnsureLifecycleJournal(j.EngineStateDir, e.Rel, e.Meta["from"], e.Meta["to"], e.Meta["reason"], e.RunID, now)
}

// Commit journals an intent, makes the write, and journals it applied —
// or journals it skipped when the target changed between the plan and now.
// The intent line is fsynced before the write begins; a crash in between is
// what Resolve exists for. Returns the outcome kind it journaled.
//
// The order after the write is governance line, then applied line. An
// applied record therefore implies everything it stands for is on disk:
// a crash before the governance line leaves the intent pending, and Resolve
// (which finds the note already at `after`) writes the line the pass owed.
// The other order left a window — applied fsynced, governance not yet
// written — that a resume, which only revisits pending intents, could
// never close.
func (j *Journal) Commit(vault, runID string, id string, in Intent, now time.Time) (string, error) {
	create := in.Before == nil
	intent := Entry{
		Kind: KindIntent, RunID: runID, TS: now, ID: id, Job: in.Job, Rel: in.Rel, To: in.To, Create: create,
		BeforeHash: Hash(in.Before), AfterHash: Hash(in.After),
		After: base64.StdEncoding.EncodeToString(in.After), Summary: in.Summary, Meta: in.Meta,
	}
	if err := j.Append(intent); err != nil {
		return "", err
	}
	skipped := func(note string) (string, error) {
		return KindSkipped, j.Append(Entry{Kind: KindSkipped, RunID: runID, TS: now, ID: id, Job: in.Job, Rel: in.Rel, To: in.To, Note: note})
	}
	applied := func() (string, error) {
		if err := j.governance(intent, now); err != nil {
			return "", err
		}
		if j.crashBeforeApplied != nil {
			return "", j.crashBeforeApplied()
		}
		return KindApplied, j.Append(Entry{Kind: KindApplied, RunID: runID, TS: now, ID: id, Job: in.Job, Rel: in.Rel, To: in.To})
	}
	src := filepath.Join(vault, filepath.FromSlash(in.Rel))
	if create {
		if _, err := os.Stat(src); err == nil {
			return skipped("a note already exists at the path this intent would create")
		}
		if err := writeAtomic(src, in.After); err != nil {
			return "", err
		}
		return applied()
	}
	cur, err := os.ReadFile(src)
	if err != nil {
		return "", err
	}
	if Hash(cur) != Hash(in.Before) {
		return skipped(ErrConflict.Error())
	}
	if in.To != "" {
		dst := filepath.Join(vault, filepath.FromSlash(in.To))
		if _, err := os.Stat(dst); err == nil {
			return skipped("the destination is taken")
		}
		if err := writeAtomic(dst, in.After); err != nil {
			return "", err
		}
		if err := os.Remove(src); err != nil {
			return "", err
		}
		return applied()
	}
	if err := writeAtomic(src, in.After); err != nil {
		return "", err
	}
	return applied()
}

func writeAtomic(p string, content []byte) error {
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	tmp := p + ".dreaming.tmp"
	if err := os.WriteFile(tmp, content, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}
