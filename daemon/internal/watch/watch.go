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
	"errors"
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

	mu              sync.Mutex
	watchedDirs     int
	watchFailures   int
	lastReconcile   index.ReconcileReport
	lastReconcileAt time.Time
	lastEventAt     time.Time

	// selfWritten holds paths the daemon wrote itself, so their commits are
	// attributed to what the daemon was doing rather than mistaken for someone
	// else's edit.
	selfWritten map[string]selfWrite
}

type selfWrite struct {
	origin vcs.Origin
	when   time.Time
}

func New(cfg *config.Config, idx *index.Index, repo *vcs.Repo, log *slog.Logger) *Watcher {
	return &Watcher{
		cfg: cfg, idx: idx, repo: repo, log: log,
		selfWritten: map[string]selfWrite{},
	}
}

// MarkSelfWritten records that the daemon itself just wrote this path as an
// ordinary capture.
func (w *Watcher) MarkSelfWritten(rel string) {
	w.MarkOrigin(rel, vcs.OriginCapture)
}

// MarkOrigin records that the daemon itself just touched this path, and what it
// was doing. A later mark for the same path wins, which is what lets the
// self-probe re-attribute the note it just captured over the MCP surface.
func (w *Watcher) MarkOrigin(rel string, origin vcs.Origin) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.selfWritten[rel] = selfWrite{origin: origin, when: time.Now()}
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

// LastReconcileAt is when the last full pass finished, zero if none has. It is
// the index's freshness signal: the notifier is an accelerator, so what tells
// you the index still tracks the vault is whether the pass that guarantees it
// is still running.
func (w *Watcher) LastReconcileAt() time.Time {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.lastReconcileAt
}

// LastReconcileErrors is how many paths the last pass could not index.
func (w *Watcher) LastReconcileErrors() int {
	w.mu.Lock()
	defer w.mu.Unlock()
	return len(w.lastReconcile.Errors)
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
	w.lastReconcileAt = time.Now()
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
		// Ask git rather than replay the report. The reconcile pass reports a
		// vanished path exactly once — it drops the row on the first pass that
		// misses the file — while git keeps reporting a deleted tracked file until
		// the deletion is recorded, so this is the more durable of the two signals.
		// It is also the floor for anything the notifier never wakes on, which is
		// how a tracked file under a dot directory (`.obsidian/app.json`) reaches
		// a commit at all.
		w.commitDirty()
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
			rel, indexable, wake := w.relevant(ev)
			if !wake {
				continue
			}
			w.mu.Lock()
			w.lastEventAt = time.Now()
			w.mu.Unlock()
			if indexable && rel != "" {
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
			// Deletions are recorded here rather than when they are first seen: a
			// path missing once is a path the sync client may simply be moving into
			// place. The pass above has already looked at the vault, so this is the
			// second look the quarantine is waiting for.
			w.sweepDeletions()
			// A new subtree can appear between passes; pick up watches for it.
			if fsw != nil {
				w.addDirs(w.cfg.VaultPath)
			}
		}
	}
}

// relevant classifies an event, and picks up watches for directories that appear.
//
// Two different questions, deliberately not the same one. `index` is whether the
// path belongs in FTS5, which only markdown does — putting a PNG through
// note.Parse would be a worse bug than the one this split fixes. `wake` is
// whether the event should start a debounce window, after which the committer
// asks git what is actually dirty.
//
// Before the split, a single `.md` test answered both, which is how the daemon
// came to commit markdown only while `agentmd gate corpus-write` refused on
// anything git reported. Everything that was not markdown fell in the gap:
// written, never committed, permanently dirty, gate shut. The gate has no
// override, so a config edit could hold it closed indefinitely.
func (w *Watcher) relevant(ev fsnotify.Event) (rel string, index bool, wake bool) {
	r, err := filepath.Rel(w.cfg.VaultPath, ev.Name)
	if err != nil {
		return "", false, false
	}
	r = filepath.ToSlash(r)
	if r == "." || r == ".." || strings.HasPrefix(r, "../") {
		return "", false, false
	}
	if hasDotSegment(r) {
		// Still dropped outright, and for the reason hasDotSegment documents:
		// Drive's staging churn arrives here in the thousands, and waking on it
		// would let a long sync keep resetting the debounce and starve the very
		// commit this change exists to make. Files under a dot directory that git
		// does track — `.obsidian/app.json` — are picked up by the reconcile
		// tick's commit instead, which is a floor rather than a race.
		return "", false, false
	}
	if ev.Has(fsnotify.Create) {
		if info, err := os.Stat(ev.Name); err == nil && info.IsDir() {
			w.addDirs(ev.Name)
			return "", false, true
		}
	}
	if ev.Op == fsnotify.Chmod {
		return "", false, false
	}
	if !strings.HasSuffix(r, ".md") {
		// Not indexed, but it is a real change to a tracked tree — wake the
		// committer and let git decide whether it matters. `.gitignore` is the
		// policy surface; the daemon holds no second opinion about which files
		// belong in the history.
		return "", false, true
	}
	return r, true, true
}

// hasDotSegment reports whether any component of a vault-relative path starts
// with a dot.
//
// Checking only the base name is not enough, and the gap is not theoretical.
// Google Drive stages every upload through a `.tmp.driveupload` directory beside
// the file, so its churn arrives as ordinary-looking `something.md` events one
// level down — a base-name check waves all of them through. The reconcile pass
// already skips dot directories during its walk (index.Reconcile), so those
// paths never reached the index; the notifier had no equivalent, and during the
// git-transport cutover's bulk upload it fed the commit path more than 1,400
// files that existed for less than a debounce window.
//
// The notifier sees them at all because the kqueue backend adds watches for
// directories created inside a watched directory, whatever addDirs chose to walk.
func hasDotSegment(rel string) bool {
	for _, seg := range strings.Split(rel, "/") {
		if strings.HasPrefix(seg, ".") {
			return true
		}
	}
	return false
}

// process re-indexes a batch, then commits whatever the worktree is carrying.
//
// The batch is what woke us, not what gets committed. An empty batch is still a
// reason to commit: a non-markdown change wakes the window without ever entering
// the index, and that is exactly the class of file that used to sit dirty
// forever.
func (w *Watcher) process(batch []string) {
	for _, rel := range batch {
		if err := w.idx.IndexFile(rel); err != nil {
			w.log.Warn("indexing failed", "path", rel, "err", err)
		}
	}
	w.commitDirty()
}

// commitDirty commits what git reports, rather than what the indexer accepted.
//
// Asking git is the whole point. Deriving the commit list from the event batch
// re-creates the same drift one filter further down: an event can be missed
// while the change still needs committing, and a file the index rejects is
// still a file the gate can see. `Dirty()` is also what `gate corpus-write`
// asks, so the two ends of this cannot disagree by construction.
//
// Cheap enough to call per debounce window: ~0.16s on a 10,663-file vault,
// including process start, measured on the machine this ships to.
func (w *Watcher) commitDirty() {
	dirty, err := w.repo.Dirty()
	if err != nil {
		if !errors.Is(err, vcs.ErrNoRepo) {
			w.log.Warn("could not read worktree status", "err", err)
		}
		return
	}
	committable := w.committable(dirty)
	w.warnOnLargeAdditions(committable)
	w.commitPaths(committable)
}

// largeFileWarnBytes is where an addition stops looking like vault content.
// Notes are kilobytes and the heaviest existing attachments are a few megabytes,
// so this only fires on something that arrived by accident or by a mistaken drag.
const largeFileWarnBytes = 50 << 20

// warnOnLargeAdditions says so when a big file is about to enter history, and
// commits it anyway.
//
// The alternative — skipping it — was considered and rejected, because a skipped
// file stays dirty and a dirty worktree shuts `agentmd gate corpus-write`. That
// is precisely the defect this committer change exists to remove, so a size
// guard would have reintroduced it under a new name for a rarer input.
//
// Committing is also the recoverable direction. A large blob in history is
// undone by `.gitignore` plus `git rm --cached`, using the same control the
// operator already edits; a gate held shut by a file the daemon refuses to
// touch has no such lever. The warning exists so the choice is visible at the
// time rather than discovered later in a repository that got big.
func (w *Watcher) warnOnLargeAdditions(paths []string) {
	for _, rel := range paths {
		info, err := os.Stat(filepath.Join(w.cfg.VaultPath, filepath.FromSlash(rel)))
		if err != nil || info.IsDir() || info.Size() < largeFileWarnBytes {
			continue
		}
		w.log.Warn("committing a large file into vault history; history has no undo "+
			"short of a rewrite, so gitignore it and `git rm --cached` if it does "+
			"not belong", "path", rel, "mb", info.Size()>>20)
	}
}

// committable drops the one class of dirty path the daemon will not volunteer to
// commit: a file under a dot directory that git does not already track.
//
// `.gitignore` is the policy surface for everything else, and in this vault it
// already lists the sync client's staging directories. This rule is what makes
// that a second line of defence rather than the only one. Drive stages every
// upload through a `.tmp.driveupload` directory beside the file, and during the
// git-transport cutover that churn peaked above 1,400 files; a vault whose
// ignore list is missing or wrong would otherwise write all of it into history,
// permanently, and history is the one thing here with no undo.
//
// Trackedness is the test rather than a list of directory names, because it
// draws the line exactly where intent already lives. `.obsidian/app.json` is
// tracked because someone chose to version it, so the daemon maintains it.
// `.tmp.driveupload/3700.md` is untracked because nobody chose anything — it is
// transport, not content. The cost, accepted: a genuinely new file under a dot
// directory has to be committed by hand once, after which the daemon keeps it up
// to date. That is the right way round, since the alternative is the daemon
// deciding on its own that a newly-appeared hidden file belongs in history.
func (w *Watcher) committable(dirty []string) []string {
	out := dirty[:0:0]
	for _, rel := range dirty {
		if hasDotSegment(rel) && !w.repo.Tracked(rel) {
			continue
		}
		out = append(out, rel)
	}
	return out
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

// sweepDeletions asks the repository to settle its quarantined absences, and
// reports what it recorded. A deletion reaching history is worth the same line
// as any other commit — it is the one that cannot be recovered by looking again.
func (w *Watcher) sweepDeletions() {
	commits, err := w.repo.SweepDeletions()
	for _, c := range commits {
		w.log.Info("committed confirmed deletions",
			"origin", c.Origin, "paths", len(c.Paths), "commit", c.Hash[:8])
	}
	if err != nil {
		w.log.Warn("confirming deletions failed", "err", err)
	}
}

// attribute decides how a change is recorded. A path the daemon wrote itself
// within the debounce window is its own capture; a path inside the phone's sync
// set is the phone's; anything else is a local edit.
func (w *Watcher) attribute(rel string) vcs.Origin {
	w.mu.Lock()
	mark, self := w.selfWritten[rel]
	if self {
		if time.Since(mark.when) < 30*time.Second {
			delete(w.selfWritten, rel)
			w.mu.Unlock()
			return mark.origin
		}
		delete(w.selfWritten, rel)
	}
	// Opportunistic sweep so a capture that never produced an event cannot pin
	// entries here forever.
	for p, m := range w.selfWritten {
		if time.Since(m.when) > 5*time.Minute {
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
