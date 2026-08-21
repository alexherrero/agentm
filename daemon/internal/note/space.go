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
// space. Matched on the first path segment: a space is a top-level directory,
// and matching deeper would let a folder named `personal` anywhere in the tree
// silently demote itself.
func inDampenedSpace(rel string) bool {
	return inSpaceSet(dampened.Load(), rel)
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

// inSpaceSet is the shared first-segment match.
func inSpaceSet(p *[]string, rel string) bool {
	if p == nil || len(*p) == 0 {
		return false
	}
	rel = strings.TrimPrefix(strings.ReplaceAll(rel, "\\", "/"), "./")
	first := rel
	if i := strings.IndexByte(rel, '/'); i >= 0 {
		first = rel[:i]
	}
	first = strings.ToLower(first)
	for _, s := range *p {
		if first == s {
			return true
		}
	}
	return false
}
