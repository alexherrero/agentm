// Package watch keeps the index in step with the vault.
//
// Two mechanisms, and only one of them is the guarantee. The filesystem notifier
// is an accelerator: it makes an edit visible in under a second when it fires.
// The periodic reconcile pass is the correctness guarantee, because the notifier
// cannot be trusted here — the vault currently lives on a cloud-sync mount, where
// events are dropped and coalesced, and on macOS each watched directory costs a
// file descriptor, so a large tree can exhaust the process limit and leave
// subtrees silently unwatched.
//
// An index whose correctness depends on how the notifier behaved that day is an
// index nobody can reason about. So the watcher reports how many directories it
// actually managed to watch, and the reconcile pass runs regardless.
package watch

import (
	"context"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/fsnotify/fsnotify"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/vcs"
)

// debounce is how long to wait for a burst of writes to settle. Obsidian and the
// sync client both write in several steps; committing after each one would make
// the history unreadable.
const debounce = 750 * time.Millisecond

// Watcher couples the vault to the index and to git.
type Watcher struct {
	cfg  *config.Config
	idx  *index.Index
	repo *vcs.Repo
	log  *slog.Logger

	fsw *fsnotify.Watcher

	mu            sync.Mutex
	watchedDirs   int
	watchFailures int
	lastReconcile index.ReconcileReport
	lastEventAt   time.Time

	// selfWritten holds paths the daemon wrote itself, so their commits are
	// attributed to capture rather than mistaken for someone else's edit.
	selfWritten map[string]time.Time
}

func New(cfg *config.Config, idx *index.Index, repo *vcs.Repo, log *slog.Logger) *Watcher {
	return &Watcher{
		cfg: cfg, idx: idx, repo: repo, log: log,
		selfWritten: map[string]time.Time{},
	}
}

// MarkSelfWritten records that the daemon itself just wrote this path.
func (w *Watcher) MarkSelfWritten(rel string) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.selfWritten[rel] = time.Now()
}

// Status is the watcher's account of itself.
type Status struct {
	WatchedDirs      int    `json:"watched_dirs"`
	WatchFailures    int    `json:"watch_failures"`
	NotifierHealthy  bool   `json:"notifier_healthy"`
	NotifierNote     string `json:"notifier_note,omitempty"`
	LastEvent        string `json:"last_event,omitempty"`
	ReconcileEvery   string `json:"reconcile_every"`
	LastReconcile    string `json:"last_reconcile,omitempty"`
	LastReconcileErr int    `json:"last_reconcile_errors"`
}

func (w *Watcher) Status() Status {
	w.mu.Lock()
	defer w.mu.Unlock()
	s := Status{
		WatchedDirs:      w.watchedDirs,
		WatchFailures:    w.watchFailures,
		NotifierHealthy:  w.fsw != nil && w.watchFailures == 0,
		ReconcileEvery:   w.cfg.ReconcileEvery.String(),
		LastReconcileErr: len(w.lastReconcile.Errors),
	}
	if w.fsw == nil {
		s.NotifierNote = "notifier unavailable; the reconcile pass is the only mechanism"
	} else if w.watchFailures > 0 {
		s.NotifierNote = fmt.Sprintf(
			"%d directories could not be watched (likely the file-descriptor limit); "+
				"those subtrees are covered only by the reconcile pass",
			w.watchFailures)
	}
	if !w.lastEventAt.IsZero() {
		s.LastEvent = w.lastEventAt.UTC().Format(time.RFC3339)
	}
	if w.lastReconcile.Elapsed > 0 {
		s.LastReconcile = fmt.Sprintf(
			"scanned %d, added %d, updated %d, removed %d in %s",
			w.lastReconcile.Scanned, w.lastReconcile.Added, w.lastReconcile.Updated,
			w.lastReconcile.Removed, w.lastReconcile.Elapsed.Round(time.Millisecond))
	}
	return s
}

// ReconcileNow runs one full pass, records the report, and commits what it found.
//
// Committing here rather than only on the notifier's path is not belt-and-braces.
// The notifier misses events — a directory created and filled in the same instant,
// a cloud-sync client writing a batch, a subtree that exceeded the file-descriptor
// limit — and every change it misses would otherwise be indexed and never
// committed, which is the half of the vault with no undo. `commit` is set to false
// only for the startup pass, where "changed" means "not in a fresh index" rather
// than "changed on disk".
func (w *Watcher) ReconcileNow() (index.ReconcileReport, error) {
	return w.reconcile(true)
}

// ReconcileInitial builds the index at startup without treating the whole vault as
// a change to commit.
func (w *Watcher) ReconcileInitial() (index.ReconcileReport, error) {
	return w.reconcile(false)
}

func (w *Watcher) reconcile(commit bool) (index.ReconcileReport, error) {
	rep, err := w.idx.Reconcile()
	w.mu.Lock()
	w.lastReconcile = rep
	w.mu.Unlock()
	if err != nil {
		return rep, err
	}
	if len(rep.Errors) > 0 {
		shown := rep.Errors
		if len(shown) > 5 {
			shown = shown[:5]
		}
		w.log.Warn("reconcile finished with errors",
			"errors", len(rep.Errors), "first", shown)
	}
	if commit {
		w.commitPaths(append(append([]string{}, rep.Changed...), rep.Gone...))
	}
	return rep, nil
}

// Run watches until the context is cancelled. It does not perform the initial
// reconcile — the caller does that before it starts serving, so the daemon never
// answers a search against an index it has not caught up yet.
func (w *Watcher) Run(ctx context.Context) error {
	fsw, err := fsnotify.NewWatcher()
	if err != nil {
		w.log.Warn("filesystem notifier unavailable; falling back to the reconcile pass alone",
			"err", err)
	} else {
		w.mu.Lock()
		w.fsw = fsw
		w.mu.Unlock()
		defer fsw.Close()
		w.addDirs(w.cfg.VaultPath)
		st := w.Status()
		if st.WatchFailures > 0 {
			w.log.Warn("some directories could not be watched",
				"watched", st.WatchedDirs, "failed", st.WatchFailures,
				"note", st.NotifierNote)
		} else {
			w.log.Info("watching vault", "dirs", st.WatchedDirs)
		}
	}

	ticker := time.NewTicker(w.cfg.ReconcileEvery)
	defer ticker.Stop()

	pending := map[string]bool{}
	var flush <-chan time.Time
	var timer *time.Timer

	events := make(chan fsnotify.Event)
	errs := make(chan error)
	if fsw != nil {
		events, errs = fsw.Events, fsw.Errors
	}

	for {
		select {
		case <-ctx.Done():
			return nil

		case ev := <-events:
			rel, interesting := w.relevant(ev)
			if !interesting {
				continue
			}
			w.mu.Lock()
			w.lastEventAt = time.Now()
			w.mu.Unlock()
			if rel != "" {
				pending[rel] = true
			}
			if timer == nil {
				timer = time.NewTimer(debounce)
				flush = timer.C
			} else {
				timer.Reset(debounce)
			}

		case err := <-errs:
			if err != nil {
				w.log.Warn("notifier error", "err", err)
			}

		case <-flush:
			timer, flush = nil, nil
			batch := make([]string, 0, len(pending))
			for rel := range pending {
				batch = append(batch, rel)
			}
			pending = map[string]bool{}
			w.process(batch)

		case <-ticker.C:
			if _, err := w.ReconcileNow(); err != nil {
				w.log.Error("reconcile failed", "err", err)
			}
			// A new subtree can appear between passes; pick up watches for it.
			if fsw != nil {
				w.addDirs(w.cfg.VaultPath)
			}
		}
	}
}

// relevant filters the event stream down to markdown changes, and picks up watches
// for directories that appear.
func (w *Watcher) relevant(ev fsnotify.Event) (rel string, ok bool) {
	name := filepath.Base(ev.Name)
	if strings.HasPrefix(name, ".") {
		return "", false
	}
	if ev.Has(fsnotify.Create) {
		if info, err := os.Stat(ev.Name); err == nil && info.IsDir() {
			w.addDirs(ev.Name)
			return "", true
		}
	}
	if !strings.HasSuffix(name, ".md") {
		return "", false
	}
	if ev.Op == fsnotify.Chmod {
		return "", false
	}
	r, err := filepath.Rel(w.cfg.VaultPath, ev.Name)
	if err != nil {
		return "", false
	}
	return filepath.ToSlash(r), true
}

// process re-indexes a batch and commits it, grouped by attribution.
func (w *Watcher) process(batch []string) {
	if len(batch) == 0 {
		return
	}
	indexed := make([]string, 0, len(batch))
	for _, rel := range batch {
		if err := w.idx.IndexFile(rel); err != nil {
			w.log.Warn("indexing failed", "path", rel, "err", err)
			continue
		}
		indexed = append(indexed, rel)
	}
	w.commitPaths(indexed)
}

// commitPaths groups paths by attribution and records one commit per origin. A
// batch can legitimately mix them — the operator editing in Obsidian while the
// phone syncs — and collapsing that into one commit would make the attribution a
// guess.
func (w *Watcher) commitPaths(paths []string) {
	if len(paths) == 0 {
		return
	}
	byOrigin := map[vcs.Origin][]string{}
	for _, rel := range paths {
		origin := w.attribute(rel)
		byOrigin[origin] = append(byOrigin[origin], rel)
	}
	for origin, paths := range byOrigin {
		hash, err := w.repo.Commit(origin, paths)
		if err != nil {
			w.log.Warn("commit failed", "origin", origin, "paths", len(paths), "err", err)
			continue
		}
		if hash != "" {
			w.log.Info("committed", "origin", origin, "paths", len(paths), "commit", hash[:8])
		} else if !w.repo.Available() {
			// Not silent: without git there is no undo, and that is worth one line
			// per batch rather than a note buried in a status endpoint.
			w.log.Info("change indexed, not committed",
				"origin", origin, "paths", len(paths), "reason", "git unavailable")
		}
	}
}

// attribute decides how a change is recorded. A path the daemon wrote itself
// within the debounce window is its own capture; a path inside the phone's sync
// set is the phone's; anything else is a local edit.
func (w *Watcher) attribute(rel string) vcs.Origin {
	w.mu.Lock()
	when, self := w.selfWritten[rel]
	if self {
		if time.Since(when) < 30*time.Second {
			delete(w.selfWritten, rel)
			w.mu.Unlock()
			return vcs.OriginCapture
		}
		delete(w.selfWritten, rel)
	}
	// Opportunistic sweep so a capture that never produced an event cannot pin
	// entries here forever.
	for p, t := range w.selfWritten {
		if time.Since(t) > 5*time.Minute {
			delete(w.selfWritten, p)
		}
	}
	w.mu.Unlock()

	if w.cfg.IsPhonePath(rel) {
		return vcs.OriginPhone
	}
	return vcs.OriginLocal
}

// addDirs registers every directory under root, counting what it could not watch
// rather than assuming success.
func (w *Watcher) addDirs(root string) {
	w.mu.Lock()
	fsw := w.fsw
	w.mu.Unlock()
	if fsw == nil {
		return
	}
	watched, failed := 0, 0
	_ = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if !d.IsDir() {
			return nil
		}
		if path != root && strings.HasPrefix(d.Name(), ".") {
			return fs.SkipDir
		}
		if err := fsw.Add(path); err != nil {
			failed++
			return nil
		}
		watched++
		return nil
	})
	w.mu.Lock()
	w.watchedDirs, w.watchFailures = watched, failed
	w.mu.Unlock()
}
