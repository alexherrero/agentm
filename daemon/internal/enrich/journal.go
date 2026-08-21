package enrich

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
)

// FileJournal appends one JSON object per write.
//
// A file rather than a table in the index, because the index is a cache that is
// rebuilt from the files — and a record of what the files used to say cannot
// live in something regenerated from what they say now. Losing the index costs a
// reconcile; losing the journal would cost every undo.
//
// Appended rather than rewritten, and flushed on every entry. The whole value of
// this file is being correct at the moment something goes wrong, which is
// exactly when a buffer does not get flushed.
type FileJournal struct {
	mu   sync.Mutex
	path string
}

// NewFileJournal writes to `<dir>/enrichment-journal.jsonl`.
func NewFileJournal(dir string) *FileJournal {
	return &FileJournal{path: filepath.Join(dir, "enrichment-journal.jsonl")}
}

// Path is where entries land, for the status surface and for a human looking.
func (j *FileJournal) Path() string { return j.path }

func (j *FileJournal) Record(_ context.Context, e JournalEntry) error {
	blob, err := json.Marshal(e)
	if err != nil {
		return fmt.Errorf("encoding journal entry: %w", err)
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	if err := os.MkdirAll(filepath.Dir(j.path), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(j.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	if _, err := f.Write(append(blob, '\n')); err != nil {
		return err
	}
	// Flushed rather than left to the OS. An entry that is in a page cache when
	// the machine dies is an entry that was never written, and the write it
	// describes did happen.
	return f.Sync()
}
