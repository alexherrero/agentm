package dreaming

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Reconcile: the night follows what the operator moved.
//
// The operator works in the vault during the day, and the night may be running
// while they do. Whatever they move — a note into the archive, a project into
// `completed/`, a file between folders in Obsidian or in Finder — the night
// finds it and follows: the clocks and the counters are re-keyed to where the
// note is now, and the move is journaled as theirs.
//
// The pairing is by **body fingerprint**, not by name. A note that moved is the
// same bytes at a different path, and the sidecars record each note's body hash
// for exactly this. Frontmatter is deliberately not part of the hash —
// enrichment, the backfills and the operator all rewrite it, and a fingerprint
// that changed when a tag was added would pair nothing.
//
// Why not a lock that keeps the operator out while the night runs: the night is
// four hours in a window they may well be awake for, and a vault they cannot
// touch is not theirs.
//
// This step repairs the *engine's* record of where a note is. Links inside the
// vault are Obsidian's own business — its "automatically update internal links"
// setting is on — and the link repairs the migrations make are a separate,
// heavier tool; a night that rewrote links on every hand move would be editing
// the operator's prose while they were in it.

const JobReconcile = "reconcile"

// ReconcilePlan is what one pass paired and repaired.
type ReconcilePlan struct {
	Repaired []ReconcileRow `json:"repaired"`
	// Vanished is a path the engine still holds a record for and that is no
	// longer on disk, with no file carrying its body anywhere. Counted rather
	// than repaired: the note was deleted, not moved, and the clock drains with
	// the next re-key rather than being guessed at.
	Vanished int    `json:"vanished"`
	Skipped  string `json:"skipped,omitempty"`
}

// ReconcileRow is one move the night followed.
type ReconcileRow struct {
	From string `json:"from"`
	To   string `json:"to"`
}

// SidecarRekeyer is what Reconcile needs from the two sidecars: move one note's
// record from one path to another, and say whether it moved anything.
//
// An interface rather than a direct call because the sidecars are written in
// Python and read here; the Go side of the re-key lands with the daemon's own
// clock writes in plan 12, and this seam is where it will attach. Until then the
// pass reports the pairing it found and writes nothing, which is honest about
// what it can do rather than silent about it.
type SidecarRekeyer interface {
	Rekey(from, to string) (bool, error)
}

// PlanReconcile pairs every record the engine holds for a path that is gone
// with the file that now carries its body.
//
// `known` is the engine's own view — path to body fingerprint — which today is
// the lifecycle sidecar's entries. `fingerprints` is what is on disk now.
func PlanReconcile(known map[string]string, fingerprints map[string]string,
	rekey SidecarRekeyer, now time.Time) ReconcilePlan {
	var plan ReconcilePlan
	if len(known) == 0 {
		plan.Skipped = "the engine holds no fingerprints to pair with"
		return plan
	}
	// Where each body is now. A fingerprint seen at more than one path is not a
	// move, it is a copy, and pairing it would pick one at random.
	byPrint := map[string][]string{}
	for rel, print := range fingerprints {
		if print == "" {
			continue
		}
		byPrint[print] = append(byPrint[print], rel)
	}
	var gone []string
	for rel := range known {
		if _, stillThere := fingerprints[rel]; !stillThere {
			gone = append(gone, rel)
		}
	}
	sort.Strings(gone)
	for _, rel := range gone {
		print := known[rel]
		matches := byPrint[print]
		if print == "" || len(matches) != 1 {
			plan.Vanished++
			continue
		}
		to := matches[0]
		if _, alsoKnown := known[to]; alsoKnown {
			// The destination already has a record of its own: this is not a
			// move but two notes that happen to share a body.
			plan.Vanished++
			continue
		}
		if rekey != nil {
			if moved, err := rekey.Rekey(rel, to); err != nil || !moved {
				continue
			}
		}
		plan.Repaired = append(plan.Repaired, ReconcileRow{From: rel, To: to})
	}
	return plan
}

// KnownFingerprints is what the engine believes about where each note is: the
// lifecycle sidecar's entries, path to fingerprint.
//
// Only an entry that carries a fingerprint can be paired, which is every entry
// written since the re-keying and none written before it. A vault whose sidecar
// predates that has nothing here to pair with, and the pass says so rather than
// pairing on a name.
//
// Narrowed to the memory tree, because that is what `FingerprintsOnDisk` walks.
// The sidecar holds a clock for everything recall can serve — project records,
// the diagnostics, the operator's own spaces — and every one of those would
// read as a note that had left the corpus, because the pairing never looks
// where they live. Counting them as vanished would be a nightly report of a
// disappearance that never happened.
func KnownFingerprints(engineStateDir, root string) (map[string]string, error) {
	out := map[string]string{}
	scope := memoryScope(root)
	blob, err := os.ReadFile(filepath.Join(engineStateDir, ".lifecycle.json"))
	if err != nil {
		blob, err = os.ReadFile(filepath.Join(root, ".lifecycle.json"))
		if err != nil {
			return out, err
		}
	}
	var rec struct {
		Version int `json:"version"`
		Entries map[string]struct {
			Fingerprint string `json:"fingerprint"`
		} `json:"entries"`
	}
	if err := json.Unmarshal(blob, &rec); err != nil {
		return out, err
	}
	for rel, e := range rec.Entries {
		if e.Fingerprint != "" && scope(rel) {
			out[rel] = e.Fingerprint
		}
	}
	return out, nil
}

// FingerprintsOnDisk is every memory note's body hash, keyed the way the
// sidecars key an entry: by the note's path from the **vault** root.
//
// Not the memory-root-relative rel `MemoryNotes` returns, which is the base the
// rest of this package works in. The two are one segment apart —
// `memory/semantic/a.md` against `agent/memory/semantic/a.md` — and comparing
// them as though they were one made every note in the corpus read as a note the
// operator had moved, every night. The sidecar is the thing being paired
// against, so the sidecar's base is the one that decides; the Python arm's
// `vault_layout.vault_rel` says why that base is the vault root and not the
// memory root — from the memory root, `../projects/x.md` and `projects/x.md`
// are the same note.
func FingerprintsOnDisk(root string) (map[string]string, error) {
	rels, err := MemoryNotes(root)
	if err != nil {
		return nil, err
	}
	vault := vaultRootOf(root)
	out := make(map[string]string, len(rels))
	for _, rel := range rels {
		abs := filepath.Join(root, filepath.FromSlash(rel))
		raw, err := os.ReadFile(abs)
		if err != nil {
			continue
		}
		out[vaultRelOf(vault, abs, rel)] = BodyFingerprint(string(raw))
	}
	return out, nil
}

// vaultRelOf is `abs` from the vault root, falling back to `fallback` when the
// two are not nested — a flat export, where the two roots are one directory and
// the memory-root rel is already the vault-root rel.
func vaultRelOf(vaultRoot, abs, fallback string) string {
	rel, err := filepath.Rel(vaultRoot, abs)
	if err != nil || strings.HasPrefix(rel, "..") {
		return fallback
	}
	return filepath.ToSlash(rel)
}

// memoryScope answers whether a vault-relative path is inside the memory tree —
// the part of the vault `FingerprintsOnDisk` walks.
func memoryScope(root string) func(string) bool {
	prefix := vaultRelOf(vaultRootOf(root), root, "")
	live, archived := "memory/", ArchiveDir+"/memory/"
	if prefix != "" && prefix != "." {
		live, archived = prefix+"/"+live, prefix+"/"+archived
	}
	return func(rel string) bool {
		return strings.HasPrefix(rel, live) || strings.HasPrefix(rel, archived)
	}
}

// BodyFingerprint is the note's body hash, matching `lifecycle.fingerprint_of`
// in the Python arm byte for byte: the text after the frontmatter, trimmed,
// SHA-256, first sixteen hex characters.
//
// The two are asserted equal by a parity test rather than by inspection, because
// a fingerprint that disagreed across the two arms would pair nothing and say
// nothing about why.
func BodyFingerprint(text string) string {
	body := text
	if strings.HasPrefix(text, "---") {
		if parts := strings.SplitN(text, "\n---", 2); len(parts) == 2 {
			body = parts[1]
		}
	}
	return Hash([]byte(strings.TrimSpace(body)))[:16]
}
