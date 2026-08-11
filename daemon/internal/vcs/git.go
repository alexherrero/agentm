// Package vcs is the only thing in the system that runs git.
//
// A private git repository is sync, backup, history, and undo in one — principle
// 2. The daemon commits every change it sees with attribution, so `git log` is
// the record of what changed the vault and why, and a bad write is one revert
// away.
//
// Two facts about the current machine shape this package. The vault still lives
// on a cloud-sync mount and is not yet a git repository; moving it to local disk
// inside one is a later step, explicitly deferred. And the daemon must never
// create that repository on its own initiative — initializing git under 8,864
// files on a synced mount is exactly the unilateral migration the build sequence
// says not to perform. So a missing repository is a capability the daemon reports
// as unavailable, loudly and on every status surface, rather than a condition it
// silently fixes or silently ignores. Principle 4: a missing capability degrades
// visibly, never silently.
package vcs

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/object"
)

// ErrNoRepo means the vault root is not a git repository. It is a degraded
// capability, not a failure to fix by creating one.
var ErrNoRepo = errors.New("vault root is not a git repository")

// Origin is where a change came from. It is the attribution recorded in the
// commit, and the reason the phone's edits are distinguishable from the operator's
// own and from the daemon's.
type Origin string

const (
	// OriginCapture is the daemon writing a memory through memory_capture.
	OriginCapture Origin = "capture"
	// OriginPhone is an edit that arrived over the phone's sync set.
	OriginPhone Origin = "phone"
	// OriginLocal is a change made on this machine by something other than the
	// daemon — the operator in Obsidian, or in an editor.
	OriginLocal Origin = "local-edit"
	// OriginProbe is the daily self-probe writing or retiring its synthetic
	// note. It is distinguished from an ordinary capture so the one log that is
	// supposed to be readable does not report a memory being saved and deleted
	// every day — those two commits are the daemon proving it works.
	OriginProbe Origin = "self-probe"
)

// DefaultDeletionGrace is how long a path must stay missing before its removal
// is recorded in history.
//
// The vault lives on a cloud-sync mount, and Google Drive replaces a file by
// staging the new copy elsewhere and moving it into place — which makes the
// file briefly unstattable at its own path while nothing about it has actually
// changed. On 2026-08-10, immediately after the git-transport cutover, that
// window was read as two deletions and committed as such; the notes were on
// disk the whole time, and the history (and the backup pushed from it)
// described a vault missing notes that existed.
//
// So an absence is confirmed, never believed on sight. Every other direction
// this daemon can get wrong heals on the next pass — a note wrongly re-indexed
// is re-indexed correctly a minute later. A deletion recorded in history is the
// one that does not.
const DefaultDeletionGrace = 90 * time.Second

// Repo is the vault's git repository, or a loud absence of one.
type Repo struct {
	root      string
	repo      *git.Repository
	mu        sync.Mutex
	available bool
	reason    string

	// precompose folds paths to NFC before they are compared or staged, which is
	// what git does on macOS and go-git does nowhere. See normalize.go.
	precompose bool

	author object.Signature

	// grace and pendingDeletes are the deletion quarantine. A path observed
	// missing is held here rather than staged, and only becomes a removal once
	// the absence has survived a second look at least `grace` later.
	grace          time.Duration
	pendingDeletes map[string]pendingDelete
}

// pendingDelete is one absence waiting to be confirmed or withdrawn.
type pendingDelete struct {
	// origin is the attribution from when the absence was first seen, so a
	// deletion confirmed several minutes later is still recorded as the thing
	// that caused it rather than as whatever happened to run the sweep.
	origin      Origin
	firstAbsent time.Time
}

// Open opens the vault's repository. A missing repository is not an error — the
// returned Repo reports itself unavailable and every Commit becomes a no-op that
// says why.
func Open(vaultRoot string) *Repo {
	r := &Repo{
		root: vaultRoot,
		author: object.Signature{
			Name:  "agentm daemon",
			Email: "agentm@localhost",
		},
		grace:          DefaultDeletionGrace,
		pendingDeletes: map[string]pendingDelete{},
	}
	// DetectDotGit stays off on purpose: it would walk upward and happily adopt
	// some unrelated ancestor repository, committing vault contents into it.
	repo, err := git.PlainOpen(vaultRoot)
	switch {
	case err == nil:
		r.repo, r.available = repo, true
		r.precompose = resolvePrecompose(repo)
	case errors.Is(err, git.ErrRepositoryNotExists):
		r.reason = fmt.Sprintf(
			"%v (%s) — the git-transport migration has not run; changes are indexed "+
				"and logged but not committed, and there is no undo until it does",
			ErrNoRepo, vaultRoot)
	default:
		r.reason = fmt.Sprintf("git unavailable at %s: %v", vaultRoot, err)
	}
	return r
}

// Available reports whether commits are actually happening.
func (r *Repo) Available() bool { return r.available }

// SetDeletionGrace sets how long an absence must persist before it is recorded
// as a deletion. Zero still requires two separate observations; it just does not
// require time to pass between them.
func (r *Repo) SetDeletionGrace(d time.Duration) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if d >= 0 {
		r.grace = d
	}
}

// Status is the human-readable state, for the status surface.
func (r *Repo) Status() string {
	if r.available {
		return "committing to " + filepath.Join(r.root, ".git")
	}
	return r.reason
}

// Head is the commit the vault currently sits on, which is the point a
// corpus-wide job would be reverted to. Empty with no error means a repository
// with no commits yet.
func (r *Repo) Head() (string, error) {
	if !r.available {
		return "", ErrNoRepo
	}
	ref, err := r.repo.Head()
	if err != nil {
		if errors.Is(err, plumbing.ErrReferenceNotFound) {
			return "", nil
		}
		return "", err
	}
	return ref.Hash().String(), nil
}

// Dirty reports the vault-relative paths with uncommitted changes.
//
// This is the second half of "is there an undo". A repository gives you one
// only if the state before the job is a state you can name: with unrelated
// edits already sitting in the worktree, reverting the job and reverting the
// operator's afternoon are the same command.
//
// It hashes every tracked file, so it is seconds rather than milliseconds on a
// vault this size. That is the right price for a check that runs once before a
// job that rewrites thousands of notes.
//
// Paths that differ from the index only by Unicode normalization are folded
// away, because git folds them and this must agree with git or the gate never
// opens. normalize.go has the reasoning.
func (r *Repo) Dirty() ([]string, error) {
	if !r.available {
		return nil, ErrNoRepo
	}
	r.mu.Lock()
	defer r.mu.Unlock()

	wt, err := r.repo.Worktree()
	if err != nil {
		return nil, fmt.Errorf("worktree: %w", err)
	}
	status, err := wt.Status()
	if err != nil {
		return nil, fmt.Errorf("status: %w", err)
	}
	// The fold is what keeps a decomposed filename from reading as a deletion
	// plus an untracked addition that no commit can clear. See normalize.go.
	return r.foldStatus(status)
}

// Tracked reports whether git already has an index entry for this path.
//
// The caller that needs this is the committer's dot-directory rule: a file under
// a dot directory that git already tracks is one the operator deliberately
// versioned (`.obsidian/app.json`), while an untracked file appearing there is
// the sync client's transport churn (`.tmp.driveupload/…`), which peaked above
// 1,400 files during the git-transport cutover and must never enter history.
// Trackedness is the distinction; a hardcoded list of sync-client directory
// names would go stale the first time a client renamed one.
//
// Checked against the composed spelling, since that is what the index holds.
func (r *Repo) Tracked(rel string) bool {
	if !r.available {
		return false
	}
	r.mu.Lock()
	defer r.mu.Unlock()

	idx, err := r.repo.Storer.Index()
	if err != nil {
		// Unknown is not "untracked": refusing to commit a real file because the
		// index momentarily would not open is the wrong way to be wrong, and the
		// caller only consults this for dot-directory paths anyway.
		return false
	}
	if _, err := idx.Entry(r.composed(rel)); err == nil {
		return true
	}
	return false
}

// Commit stages the given vault-relative paths and records one commit attributed
// to `origin`.
//
// A path that no longer exists is not staged as a deletion here. The first time
// it is seen missing it goes into the quarantine and nothing is recorded; it
// becomes a removal only once SweepDeletions has watched the absence persist.
// See DefaultDeletionGrace for why that asymmetry is deliberate.
//
// Returns the commit hash, or an empty string when there was nothing to commit or
// git is unavailable.
func (r *Repo) Commit(origin Origin, paths []string) (string, error) {
	if !r.available {
		return "", nil
	}
	if len(paths) == 0 {
		return "", nil
	}

	r.mu.Lock()
	defer r.mu.Unlock()

	wt, err := r.repo.Worktree()
	if err != nil {
		return "", fmt.Errorf("worktree: %w", err)
	}

	sort.Strings(paths)
	var staged []string
	var problems []string
	for _, rel := range paths {
		// The watcher reports the name the filesystem gave it, which on macOS is
		// the decomposed one, while the index holds the composed one. Staged
		// verbatim, the decomposed name becomes a second index entry beside the
		// composed one and the vault carries a single note under two spellings.
		// Only the index key is composed below; every filesystem call keeps the
		// name that was actually observed. See normalize.go.
		name := r.composed(rel)

		abs := filepath.Join(r.root, filepath.FromSlash(rel))
		_, statErr := os.Stat(abs)
		switch {
		case statErr == nil:
			// Present. Whatever we previously suspected about this path is over —
			// an absence that ends is a sync round-trip, not a deletion.
			delete(r.pendingDeletes, rel)
			// Add reads the file the watcher actually saw. Git folds the pathspec
			// first and then opens the folded name, which works only because a
			// Mac's local volume treats the two spellings as one file — and this
			// vault is on a synced mount that may not.
			if _, err := wt.Add(rel); err != nil {
				problems = append(problems, fmt.Sprintf("%s: %v", rel, err))
				continue
			}
			if name != rel {
				if err := r.recomposeIndexEntry(rel, name); err != nil {
					problems = append(problems, fmt.Sprintf("%s: %v", rel, err))
					continue
				}
			}
			staged = append(staged, name)

		case errors.Is(statErr, os.ErrNotExist):
			if !r.absenceConfirmed(rel, origin) {
				continue
			}
			if r.stageDeletion(rel) {
				staged = append(staged, name)
			}

		default:
			// Not ENOENT: an unreadable parent, an I/O error from the mount, a
			// permission the sync client took away for a moment. None of that is
			// evidence the file is gone, and unknown must never be treated as gone.
			// The path stays tracked and the next reconcile pass looks again.
			problems = append(problems, fmt.Sprintf("%s: %v", rel, statErr))
		}
	}

	hash, err := r.commitStaged(wt, origin, paths, staged)
	if err != nil {
		return "", err
	}
	if len(problems) == 0 {
		return hash, nil
	}
	if hash != "" {
		return hash, fmt.Errorf("committed with problems: %s", strings.Join(problems, "; "))
	}
	return "", fmt.Errorf("nothing was committed: %s", strings.Join(problems, "; "))
}

// SweepCommit is one deletion commit the sweep recorded.
type SweepCommit struct {
	Origin Origin
	Paths  []string
	Hash   string
}

// SweepDeletions records the removals whose absence has now persisted, and
// withdraws the ones that turned out to be a file in transit.
//
// This is the other half of the quarantine, and it is not optional. The
// reconcile pass reports a vanished path exactly once — it drops the row from
// the index on the first pass that misses the file, so a second pass has
// nothing left to re-offer. Holding a deletion inside Commit alone would
// therefore lose real deletions rather than merely delay them. The daemon has
// to re-check its own quarantine, which is what this does, once per reconcile
// tick.
func (r *Repo) SweepDeletions() ([]SweepCommit, error) {
	if !r.available {
		return nil, nil
	}

	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.pendingDeletes) == 0 {
		return nil, nil
	}

	wt, err := r.repo.Worktree()
	if err != nil {
		return nil, fmt.Errorf("worktree: %w", err)
	}

	byOrigin := map[Origin][]string{}
	for rel, pend := range r.pendingDeletes {
		if time.Since(pend.firstAbsent) < r.grace {
			continue
		}
		abs := filepath.Join(r.root, filepath.FromSlash(rel))
		_, statErr := os.Stat(abs)
		switch {
		case statErr == nil:
			// It came back. The sync was replacing the file at its own path; the
			// reconcile pass has already re-indexed it and there is nothing to record.
			delete(r.pendingDeletes, rel)
		case errors.Is(statErr, os.ErrNotExist):
			delete(r.pendingDeletes, rel)
			// The quarantine is keyed by the name the filesystem reported, which
			// is what os.Stat above has to be asked. What gets recorded is the
			// name the index spells it — see normalize.go.
			if r.stageDeletion(rel) {
				byOrigin[pend.origin] = append(byOrigin[pend.origin], r.composed(rel))
			}
		default:
			// Still unknown. Leave it quarantined rather than guessing; the next
			// sweep asks again.
		}
	}
	if len(byOrigin) == 0 {
		return nil, nil
	}

	origins := make([]Origin, 0, len(byOrigin))
	for origin := range byOrigin {
		origins = append(origins, origin)
	}
	sort.Slice(origins, func(i, j int) bool { return origins[i] < origins[j] })

	var out []SweepCommit
	var problems []string
	for _, origin := range origins {
		paths := byOrigin[origin]
		sort.Strings(paths)
		hash, err := r.commitStaged(wt, origin, paths, paths)
		if err != nil {
			problems = append(problems, fmt.Sprintf("%s: %v", origin, err))
			continue
		}
		if hash != "" {
			out = append(out, SweepCommit{Origin: origin, Paths: paths, Hash: hash})
		}
	}
	if len(problems) > 0 {
		return out, fmt.Errorf("confirmed deletions: %s", strings.Join(problems, "; "))
	}
	return out, nil
}

// absenceConfirmed reports whether this path has now been seen missing twice,
// far enough apart. The first sighting only opens the quarantine entry.
//
// Caller holds r.mu.
func (r *Repo) absenceConfirmed(rel string, origin Origin) bool {
	prev, quarantined := r.pendingDeletes[rel]
	if quarantined && time.Since(prev.firstAbsent) >= r.grace {
		delete(r.pendingDeletes, rel)
		return true
	}
	if !quarantined {
		r.pendingDeletes[rel] = pendingDelete{origin: origin, firstAbsent: time.Now()}
	}
	return false
}

// stageDeletion records a removal in the git index without touching the file,
// reporting whether there was a tracked entry to remove at all.
//
// It edits the index directly rather than calling Worktree.Remove, which deletes
// the file from the working tree as well (doRemoveFile: deleteFromIndex, then
// deleteFromFilesystem). On a path that is genuinely gone that second step is a
// no-op — but "genuinely gone" is precisely what a cloud-sync mount lies about,
// and a Remove landing in the instant the file is back would delete the
// operator's note rather than merely misrecord it. Keeping the blast radius
// inside the index keeps the worst case recoverable.
//
// A missing index entry is the ordinary case for a temp file that came and went,
// and is not worth reporting.
//
// The entry is looked up under the composed spelling first, because that is the
// one the index holds for an accented name; a removal asked for under the
// decomposed name the filesystem reported would find nothing and the deletion
// would be dropped in silence. The raw name is tried second for an index that
// was written before any of this existed.
//
// Caller holds r.mu.
func (r *Repo) stageDeletion(rel string) bool {
	idx, err := r.repo.Storer.Index()
	if err != nil {
		return false
	}
	if _, err := idx.Remove(r.composed(rel)); err != nil {
		if _, err := idx.Remove(rel); err != nil {
			return false
		}
	}
	return r.repo.Storer.SetIndex(idx) == nil
}

// commitStaged records the staged paths as one commit, or reports that staging
// them changed nothing.
//
// Caller holds r.mu.
func (r *Repo) commitStaged(wt *git.Worktree, origin Origin, msgPaths, staged []string) (string, error) {
	if len(staged) == 0 {
		return "", nil
	}
	status, err := wt.Status()
	if err == nil && !changesTree(status, staged) {
		// The watcher can see an event for a write that did not change content —
		// a touch, or a sync round-trip. An empty commit would be noise in the
		// one log that is supposed to be readable.
		return "", nil
	}

	// The message names the paths as the index spells them, so the log reads the
	// way `git log` would render it rather than in whatever form the filesystem
	// happened to report.
	hash, err := wt.Commit(commitMessage(origin, r.composedAll(msgPaths)), &git.CommitOptions{
		Author:    r.signature(),
		Committer: r.signature(),
	})
	if err != nil {
		return "", fmt.Errorf("commit: %w", err)
	}
	return hash.String(), nil
}

// changesTree reports whether staging these paths actually changed what the next
// commit would record.
//
// The obvious check — Status().IsClean() — is wrong here, and wrong in a way
// that only shows up on a cloud-sync mount. IsClean() counts untracked files,
// which do not affect the tree at all. During Drive's bulk upload the vault is
// continuously full of untracked staging files, so IsClean() was false the whole
// time while a batch of unchanged notes staged nothing. Every batch then reached
// a commit with an empty tree diff, which go-git rejects with "cannot create
// empty commit: clean working tree" — a warning per batch, over 1,400 of them,
// for a condition that was never an error. Asking only about the paths we staged
// answers the question that was actually being asked.
func changesTree(status git.Status, staged []string) bool {
	for _, rel := range staged {
		if st, ok := status[rel]; ok && st.Staging != git.Unmodified {
			return true
		}
	}
	return false
}

func (r *Repo) signature() *object.Signature {
	s := r.author
	s.When = time.Now()
	return &s
}

// commitMessage names what changed and where it came from. The origin line is the
// attribution: an edit that arrived from the phone is marked as such, which is
// what makes the phone's writes distinguishable from the operator's own once
// Syncthing is carrying them.
func commitMessage(origin Origin, paths []string) string {
	subject := ""
	switch origin {
	case OriginCapture:
		subject = "memory: capture"
	case OriginPhone:
		subject = "vault: edits from phone"
	case OriginProbe:
		subject = "memory: self-probe"
	default:
		subject = "vault: local edits"
	}
	if len(paths) == 1 {
		subject += " — " + paths[0]
	} else {
		subject += fmt.Sprintf(" — %d files", len(paths))
	}
	if len(subject) > 72 && len(paths) == 1 {
		subject = subject[:69] + "…"
	}

	var b strings.Builder
	b.WriteString(subject)
	b.WriteString("\n\norigin: ")
	b.WriteString(string(origin))
	b.WriteString("\n")
	if len(paths) > 1 {
		b.WriteString("\n")
		limit := len(paths)
		if limit > 50 {
			limit = 50
		}
		for _, p := range paths[:limit] {
			fmt.Fprintf(&b, "- %s\n", p)
		}
		if len(paths) > limit {
			fmt.Fprintf(&b, "- … and %d more\n", len(paths)-limit)
		}
	}
	return b.String()
}
