package note

import (
	"strings"
	"sync/atomic"
)

// Which spaces are dampened is the operator's call, and it is recorded in
// `standards/storage-rules.md` where the rest of the filing contract lives. The
// daemon reads it there and sets it here once at startup, and again whenever the
// health pass re-reads the rules.
//
// Held in an atomic rather than passed through `Parse`: four call sites parse
// notes, one of them the self-probe, which has no configuration and no business
// acquiring one. A single writer at boot and a lock-free read on the indexing
// path is the same shape the filing contract itself uses.
//
// **The multiplier is not configurable, and that is deliberate.** A 125-point
// sweep over [0.02, 1.0] per class produced four distinct outcomes, and every
// setting at or below 0.6 ranked identically. Strength is not a parameter; only
// whether a space is dampened at all. The rules file therefore names *which*
// spaces damp, not by how much — a number the operator could set and that
// provably changes nothing would be a config surface with nothing behind it.
var dampened atomic.Pointer[[]string]

// SetDampenedSpaces replaces the set, normalized to lower case. Called by the
// daemon from the resolved filing contract.
func SetDampenedSpaces(spaces []string) {
	dampened.Store(normSpaces(spaces))
}

// DampenedSpaces is what is currently set, for the status surface and for tests.
func DampenedSpaces() []string {
	if p := dampened.Load(); p != nil {
		return append([]string(nil), *p...)
	}
	return nil
}

// inDampenedSpace reports whether a vault-relative path sits in a dampened
// area. An entry of one segment is a whole space, matched on the first path
// segment — matching deeper would let a folder named `personal` anywhere in the
// tree silently demote itself. An entry with a slash in it is an area:
// `agent/diagnostics` matches that directory and everything under it, and
// nothing else, so the night's own paper can be quieted without quieting the
// memory classes beside it.
func inDampenedSpace(rel string) bool {
	return inSpaceSet(dampened.Load(), rel)
}

// The areas walled from recall entirely: never indexed, never embedded, never
// served. Set from the contract's `recall_exempt_areas` beside the other two.
//
// A third list rather than a flag on the first, because it answers a third
// question. Dampening asks whether a space should stay quiet, exemption asks
// whether the contract governs it, and this asks whether the corpus may hold it
// at all. The answer here is no, so the check runs before a file is read rather
// than after it is scored.
var recallExempt atomic.Pointer[[]string]

// SetRecallExemptAreas replaces the walled set, normalized to lower case.
func SetRecallExemptAreas(areas []string) {
	recallExempt.Store(normSpaces(areas))
}

// RecallExemptAreas is what is currently walled, for the status surface and for
// tests.
func RecallExemptAreas() []string {
	if p := recallExempt.Load(); p != nil {
		return append([]string(nil), *p...)
	}
	return nil
}

// InRecallExemptArea reports whether a vault-relative path is walled from the
// corpus. Every reader that could put a note in front of a model asks this.
func InRecallExemptArea(rel string) bool {
	return inSpaceSet(recallExempt.Load(), rel)
}

// Each project's activity, slug to multiplier, written by the night and read
// here at boot and on every contract re-read.
//
// `projects/` runs on no decay curve — a decision from June is not less true in
// December — so what ranks a record there is how much its project is being
// worked. The night computes the reading from ninety days of the project's own
// evidence; this is the ranking half of it.
//
// A project with no reading ranks at 1.0. Absent is not quiet: a project the
// night has not read yet is one nothing is known about, and the safe direction
// for a weight is to leave it alone.
var projectActivity atomic.Pointer[map[string]float64]

// SetProjectActivity replaces the readings.
func SetProjectActivity(readings map[string]float64) {
	if readings == nil {
		projectActivity.Store(nil)
		return
	}
	copied := make(map[string]float64, len(readings))
	for k, v := range readings {
		copied[strings.ToLower(strings.TrimSpace(k))] = v
	}
	projectActivity.Store(&copied)
}

// ProjectActivityOf is the multiplier a vault-relative path earns from the
// project it sits in, and 1.0 for a path in no project.
func ProjectActivityOf(rel string) float64 {
	p := projectActivity.Load()
	if p == nil || len(*p) == 0 {
		return 1.0
	}
	parts := segments(rel)
	// `projects/<slug>/…`, and the `../projects/<slug>/…` spelling a
	// memory-root-relative key takes for the sibling space.
	for i, seg := range parts {
		if seg != "projects" || i+1 >= len(parts) {
			continue
		}
		if v, ok := (*p)[parts[i+1]]; ok && v > 0 {
			return v
		}
		return 1.0
	}
	return 1.0
}

// The contract's `importance_dampen_at_or_below`: a note whose own importance
// sits at or under it ranks quietly. Zero means the contract named none, and
// nothing is dampened for importance at all — which is the state every vault
// was in before this line existed.
var importanceDampenMax atomic.Int64

// SetImportanceDampenMax sets the quiet threshold from the contract.
func SetImportanceDampenMax(max int) { importanceDampenMax.Store(int64(max)) }

// ImportanceDampenMax is what is currently set, for the status surface and tests.
func ImportanceDampenMax() int { return int(importanceDampenMax.Load()) }

// isLowImportance reads the threshold. A note with no importance never reaches
// here — absent is not low, and the caller checks that first.
func isLowImportance(importance int) bool {
	max := importanceDampenMax.Load()
	return max > 0 && int64(importance) <= max
}

// The spaces the filing contract does not govern. Nothing in them decays.
//
// Held separately from `dampened` because the two lists answer different
// questions and, in the shipped contract, name different directories. Dampening
// asks whether a space should stay quiet on an ordinary question; exemption asks
// whether the contract applies to it at all. A space can be either, both, or
// neither.
var decayExempt atomic.Pointer[[]string]

// SetDecayExemptSpaces replaces the set, normalized to lower case. Called by the
// daemon from the resolved filing contract, beside SetDampenedSpaces.
func SetDecayExemptSpaces(spaces []string) {
	decayExempt.Store(normSpaces(spaces))
}

// DecayExemptSpaces is what is currently set, for the status surface and tests.
func DecayExemptSpaces() []string {
	if p := decayExempt.Load(); p != nil {
		return append([]string(nil), *p...)
	}
	return nil
}

// inDecayExemptSpace reports whether a vault-relative path sits in a space the
// contract does not govern. First path segment, for the reason
// inDampenedSpace matches there: a space is a top-level directory.
func inDecayExemptSpace(rel string) bool {
	return inSpaceSet(decayExempt.Load(), rel)
}

// normSpaces trims, drops empties, and lowercases.
func normSpaces(spaces []string) *[]string {
	norm := make([]string, 0, len(spaces))
	for _, s := range spaces {
		s = strings.Trim(strings.TrimSpace(s), "/")
		if s != "" {
			norm = append(norm, strings.ToLower(s))
		}
	}
	return &norm
}

// inSpaceSet is the shared match: a one-segment entry names a space and is
// compared against the path's first segment; an entry with a slash names an
// area and is compared segment by segment against the path's leading segments.
//
// Segment-wise rather than by string prefix, so `personal/Homework` is not read
// as sitting inside `personal/Home`. A wall with a near-miss in it is worse than
// no wall, because it reads as one.
func inSpaceSet(p *[]string, rel string) bool {
	if p == nil || len(*p) == 0 {
		return false
	}
	parts := segments(rel)
	if len(parts) == 0 {
		return false
	}
	for _, s := range *p {
		want := segments(s)
		if len(want) == 0 || len(want) > len(parts) {
			continue
		}
		match := true
		for i, seg := range want {
			if parts[i] != seg {
				match = false
				break
			}
		}
		if match {
			return true
		}
	}
	return false
}

// segments splits a path into lower-cased, non-empty segments. Both sides of
// every comparison in this file run through it, so a set entry and a vault path
// are normalized the same way exactly once.
func segments(rel string) []string {
	rel = strings.TrimPrefix(strings.ReplaceAll(rel, "\\", "/"), "./")
	var out []string
	for _, p := range strings.Split(rel, "/") {
		if p = strings.TrimSpace(p); p != "" && p != "." {
			out = append(out, strings.ToLower(p))
		}
	}
	return out
}

// altitudeDampening gates the `artifact` class.
//
// Held here rather than checked at search time because the flag decides whether
// the *flag on the row* is written at all, and the row is written at index time.
// That makes turning it on a reindex rather than a restart, which is the same
// cost every other index-time class already carries.
var altitudeDampening atomic.Bool

// SetAltitudeDampening turns the `artifact` class on. Called by the daemon from
// its configuration; see config.AltitudeEnabled for why it ships off.
func SetAltitudeDampening(on bool) { altitudeDampening.Store(on) }

// AltitudeDampening reports the current setting, for the status surface and
// tests.
func AltitudeDampening() bool { return altitudeDampening.Load() }

// SyncArtifact reports whether a filename is written by a sync layer rather
// than by anything in this system.
//
// Google Drive plants a file named "Icon" followed by a carriage return in
// every folder it mirrors, and Finder leaves .DS_Store beside it. Neither is a
// note, and a directory holding only these is empty in every sense that
// matters — but both are files, so a walk that counts entries finds content
// where there is none. The 2026-09-06 layout survey found 310 icon files, one
// per folder, and the corpus migration's empty-directory cleanup had left five
// directories standing because each still "held" one.
//
// Matched exactly. The rule was written as a "Icon" prefix test, which also
// skips a note somebody might legitimately name Iconography.md.
func SyncArtifact(name string) bool {
	switch name {
	case "Icon\r", "Icon", ".DS_Store":
		return true
	}
	return false
}
