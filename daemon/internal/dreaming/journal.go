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
	"runtime"
	"syscall"
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
	Create bool `json:"create,omitempty"`
	// Delete is set when the intent removes Rel. Removed is the bytes it takes,
	// base64, so the note can be put back from the journal alone.
	Delete     bool   `json:"delete,omitempty"`
	Removed    string `json:"removed,omitempty"`
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
	// rename, when set, is a test's stand-in for os.Rename in a move.
	rename func(oldpath, newpath string) error
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
// re-file), a creation (Before nil: Rel did not exist — the promotion), or a
// deletion (Delete: Rel, still at Before, is removed — a type's map whose type
// fell below the floor).
type Intent struct {
	Job     string
	Rel     string
	To      string
	Before  []byte
	After   []byte
	Delete  bool
	Summary string
	Meta    map[string]string
}

// ErrConflict is an intent whose target no longer hashes as the intent
// expected: neither before nor after. It is skipped, never forced.
var ErrConflict = errors.New("target changed since the intent was journaled")

// Resolve applies one journaled intent against the vault, idempotently:
// the target that already hashes as `after` is recorded applied (found on
// resume), the target that hashes as `before` is written now, anything else
// is a conflict. A move resolves on both paths: the source gone and the
// destination at `after` is applied; the source at `before` and no
// destination is applied now, by a rename; the source gone and the
// destination at `before` is a rename whose rewrite is still owed; the source
// at `before` and the destination at `after` is a copy-style move from before
// moves were renames, finished by removing the source; anything else is left
// alone. A creation resolves on the one path it makes, and a deletion on the
// one path it removes. Returns the outcome kind written to the journal.
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
	case e.Delete:
		cur, err := os.ReadFile(src)
		switch {
		case os.IsNotExist(err):
			return settle(KindApplied, "found applied on resume")
		case err == nil && Hash(cur) == e.BeforeHash:
			if err := os.Remove(src); err != nil {
				return "", err
			}
			return settle(KindApplied, "applied on resume")
		default:
			return settle(KindSkipped, ErrConflict.Error())
		}
	case e.To != "":
		dst := filepath.Join(vault, filepath.FromSlash(e.To))
		cur, srcErr := os.ReadFile(src)
		got, dstErr := os.ReadFile(dst)
		switch {
		case srcErr != nil && dstErr == nil && Hash(got) == e.AfterHash:
			return settle(KindApplied, "found applied on resume")
		case srcErr != nil && dstErr == nil && Hash(got) == e.BeforeHash:
			// The crash fell between the rename and the rewrite: the note is
			// at its new path with its old bytes. The rewrite is what is left.
			if err := writeAtomic(dst, after); err != nil {
				return "", err
			}
			return settle(KindApplied, "finished on resume: the note was renamed, not yet rewritten")
		case srcErr == nil && dstErr != nil && Hash(cur) == e.BeforeHash:
			note, err := j.moveNote(src, dst, cur, after)
			var refused *renameRefused
			if errors.As(err, &refused) {
				return settle(KindSkipped, refused.Error())
			}
			if errors.Is(err, ErrConflict) {
				return settle(KindSkipped, ErrConflict.Error())
			}
			if err != nil {
				return "", err
			}
			if note == "" {
				note = "applied on resume"
			} else {
				note = "applied on resume, " + note
			}
			return settle(KindApplied, note)
		case srcErr == nil && dstErr == nil && Hash(cur) == e.BeforeHash && Hash(got) == e.AfterHash:
			// A move journaled before moves were renames, crashed between its
			// two halves: the new copy is written and the old one not yet
			// removed. Removing it is the half that is left; reading this as
			// a conflict would leave both.
			if err := os.Remove(src); err != nil {
				return "", err
			}
			return settle(KindApplied, "finished on resume: the destination was written, the source not yet removed")
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

// governance writes the lifecycle journal line an applied intent owes when
// it moved a note along the axis (its Meta says from/to) — the lifecycle
// job's sinks and lifts, the copies job's supersessions — once.
func (j *Journal) governance(e Entry, now time.Time) error {
	if e.Meta == nil || e.Meta["to"] == "" || j.EngineStateDir == "" {
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
	create := in.Before == nil && !in.Delete
	intent := Entry{
		Kind: KindIntent, RunID: runID, TS: now, ID: id, Job: in.Job, Rel: in.Rel, To: in.To, Create: create,
		Delete: in.Delete, BeforeHash: Hash(in.Before), AfterHash: Hash(in.After),
		After: base64.StdEncoding.EncodeToString(in.After), Summary: in.Summary, Meta: in.Meta,
	}
	if in.Delete {
		intent.Removed = base64.StdEncoding.EncodeToString(in.Before)
	}
	if err := j.Append(intent); err != nil {
		return "", err
	}
	skipped := func(note string) (string, error) {
		return KindSkipped, j.Append(Entry{Kind: KindSkipped, RunID: runID, TS: now, ID: id, Job: in.Job, Rel: in.Rel, To: in.To, Note: note})
	}
	applied := func(note string) (string, error) {
		if err := j.governance(intent, now); err != nil {
			return "", err
		}
		if j.crashBeforeApplied != nil {
			return "", j.crashBeforeApplied()
		}
		return KindApplied, j.Append(Entry{Kind: KindApplied, RunID: runID, TS: now, ID: id, Job: in.Job, Rel: in.Rel, To: in.To, Note: note})
	}
	src := filepath.Join(vault, filepath.FromSlash(in.Rel))
	if in.Delete {
		cur, err := os.ReadFile(src)
		if os.IsNotExist(err) {
			return skipped("the note this intent would remove is already gone")
		}
		if err != nil {
			return "", err
		}
		if Hash(cur) != Hash(in.Before) {
			return skipped(ErrConflict.Error())
		}
		if err := os.Remove(src); err != nil {
			return "", err
		}
		return applied("")
	}
	if create {
		if _, err := os.Stat(src); err == nil {
			return skipped("a note already exists at the path this intent would create")
		}
		if err := writeAtomic(src, in.After); err != nil {
			return "", err
		}
		return applied("")
	}
	cur, err := os.ReadFile(src)
	if os.IsNotExist(err) {
		// The note this intent was planned against is no longer there. That is
		// a hand move, not a fault: the operator works in the vault while the
		// night runs, and a pass that aborted because a file went somewhere
		// else would take the rest of the night with it. Skipped, named, and
		// picked up by the reconcile step at the end of the same night.
		return skipped("the note this intent was planned against has moved or gone")
	}
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
		note, err := j.moveNote(src, dst, in.Before, in.After)
		var refused *renameRefused
		if errors.As(err, &refused) {
			return skipped(refused.Error())
		}
		if errors.Is(err, ErrConflict) {
			return skipped(ErrConflict.Error())
		}
		if err != nil {
			return "", err
		}
		return applied(note)
	}
	if err := writeAtomic(src, in.After); err != nil {
		return "", err
	}
	return applied("")
}

// renameFile is the rename a move makes; a Journal's own `rename` field
// stands in for it in tests.
func (j *Journal) renameFile(oldpath, newpath string) error {
	if j.rename != nil {
		return j.rename(oldpath, newpath)
	}
	return os.Rename(oldpath, newpath)
}

// renameRefused is a rename the filesystem would not make, other than one
// across devices: on Windows, a note another process holds open. The move is
// skipped with the source where it was, and a later night plans it again.
type renameRefused struct{ err error }

func (r *renameRefused) Error() string { return "the note could not be renamed: " + r.err.Error() }

// moveNote moves a note from src to dst by renaming it, so the file keeps its
// identity: its inode on disk, and through a sync client such as Google
// Drive, its id in the cloud. Writing a copy at dst and deleting src is the
// same move to the vault but a new file to everything that syncs it. When the
// move also changes the note (its links repaired), the renamed file is
// rewritten in place, which keeps the identity too.
//
// before is the source's bytes as hash-checked by the caller. If they change
// between that check and the rename, the rename is undone and ErrConflict
// returned, so the move is skipped with the edit where it was made. A rename
// across devices falls back to the copy and says so in the returned note; any
// other refused rename is a *renameRefused, the source untouched.
func (j *Journal) moveNote(src, dst string, before, after []byte) (string, error) {
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return "", err
	}
	if err := j.renameFile(src, dst); err != nil {
		if !crossDevice(err) {
			return "", &renameRefused{err: err}
		}
		if err := writeAtomic(dst, after); err != nil {
			return "", err
		}
		if err := os.Remove(src); err != nil {
			return "", err
		}
		return "moved by copy: the destination is on another device", nil
	}
	if Hash(after) == Hash(before) {
		return "", nil
	}
	cur, err := os.ReadFile(dst)
	if err != nil {
		return "", err
	}
	if Hash(cur) != Hash(before) {
		if err := j.renameFile(dst, src); err != nil {
			return "", err
		}
		return "", ErrConflict
	}
	return "", writeAtomic(dst, after)
}

// crossDevice reports a rename that failed only because src and dst are on
// different filesystems: EXDEV on Unix, ERROR_NOT_SAME_DEVICE (17) on Windows.
func crossDevice(err error) bool {
	if errors.Is(err, syscall.EXDEV) {
		return true
	}
	var errno syscall.Errno
	return runtime.GOOS == "windows" && errors.As(err, &errno) && errno == 17
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
