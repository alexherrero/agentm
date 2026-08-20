package rules

import (
	"sync/atomic"
	"time"
)

// Holder is the live contract, re-readable without a restart.
//
// Two problems it solves, and they pull in opposite directions.
//
// A broken contract has to be *recoverable*. The whole promise of the
// arrangement is that filing behaviour changes by editing markdown — which is
// worth very little if fixing a typo means restarting the daemon. Without a
// re-read, "fix the block and the next cycle picks up where this one stopped" is
// false for the longest-running process in the system, and the halt goes from a
// pause to an outage.
//
// But capture must not pay for that. Capture is the one operation that must
// never fail for an interesting reason, and it runs to a sub-100ms budget with
// the laptop offline; re-reading and re-parsing a file on its path would put a
// filesystem stat between the operator and a saved memory. So the read side is
// an atomic pointer load and nothing else, and the refresh happens out of band —
// on the health evaluation, which already runs fresh on every status read for
// exactly this class of reason.
type Holder struct {
	vaultPath string
	current   atomic.Pointer[snapshot]
}

// snapshot is one resolution attempt: what it produced, and when.
type snapshot struct {
	rules *Rules
	err   error
	at    time.Time
}

// NewHolder resolves the contract once and holds the result, error included.
//
// A failure here is not returned, because a daemon that refuses to start over a
// misplaced colon takes the whole memory down to protect one field. The error is
// held and reported; `Get` is where a caller that actually needs the taxonomy
// finds out.
func NewHolder(vaultPath string, now time.Time) *Holder {
	h := &Holder{vaultPath: vaultPath}
	h.store(now)
	return h
}

func (h *Holder) store(now time.Time) *snapshot {
	loaded, err := Load(h.vaultPath)
	snap := &snapshot{rules: loaded, err: err, at: now}
	h.current.Store(snap)
	return snap
}

// Get returns the contract, or the reason there isn't one. Lock-free.
func (h *Holder) Get() (*Rules, error) {
	snap := h.current.Load()
	if snap == nil {
		return nil, errNotResolved
	}
	return snap.rules, snap.err
}

// Refresh re-reads the contract from disk and returns the new result.
//
// Called by the health evaluation rather than by a watcher: a watcher would have
// to know that `standards/storage-rules.md` is special among the vault's fifteen
// thousand files, and the health pass is already the thing that asks "is this
// still true" on a schedule.
func (h *Holder) Refresh(now time.Time) (*Rules, error) {
	snap := h.store(now)
	return snap.rules, snap.err
}

// ResolvedAt is when the held result was produced — the answer to "is this
// status telling me about now, or about a problem I already fixed."
func (h *Holder) ResolvedAt() time.Time {
	if snap := h.current.Load(); snap != nil {
		return snap.at
	}
	return time.Time{}
}

type resolveError struct{}

func (resolveError) Error() string {
	return "the filing contract has not been resolved yet"
}

var errNotResolved = resolveError{}
