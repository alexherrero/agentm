package note

import "strings"

// Which surface served a recall, and whether that recall moves a note's clock.
//
// The clock is the aging axis: a note that nobody has recalled in a year sinks,
// and a genuine recall returns it to day zero. So "did this count as a recall"
// is a real question with two wrong answers. Say no when it was one and the
// note ages as if unread; say yes when it was not and the note stays young
// forever on nothing.
//
// The design's answer: a surface a person is reading through moves the clock,
// and a pass that is measuring the ranker does not. There is no third case, and
// there is deliberately no default that guesses — `SurfaceMeasure` has to be
// asked for, and asking for it is a statement.
const (
	// SurfaceCLI is `agentmd search` run by a person.
	SurfaceCLI = "cli"
	// SurfaceMCPPrefix opens the surface for a registered MCP client:
	// `mcp:claude-code`, `mcp:claude-desktop`. The suffix is the name the
	// client sent at `initialize`, lowercased.
	SurfaceMCPPrefix = "mcp:"
	// SurfaceSessionStart and SurfaceSubmit are the two hook arms. Written by
	// the Python side, named here so one file holds the whole vocabulary.
	SurfaceSessionStart = "session-start"
	SurfaceSubmit       = "prompt-submit"
	// SurfaceMeasure is every pass that searches in order to grade the search.
	//
	// The retrieval gate, the health scorecards, the `verify-*` scripts, the
	// probe and enrichment's neighbour search all run queries, and the gate
	// alone runs 64 of them a night against the same gold set. None of those is
	// anybody reading anything. Counted as recalls they would hold roughly
	// three hundred notes at day zero in perpetuity — the corpus would never
	// age, and the axis the whole lifecycle rests on would read as healthy
	// while measuring nothing.
	SurfaceMeasure = "measure"
)

// MovesClock reports whether a recall served on this surface is a genuine
// recall — one that resets the note's clock and earns a ledger row.
//
// Unknown surfaces move the clock. A surface nobody named is far more likely to
// be a new way a person reads their memory than a new way of grading it, and
// the failure directions are not symmetric: a real recall not counted ages a
// note the operator is actually using, where a measurement counted keeps a note
// young, which is visible in the morning note and costs nothing but rank.
func MovesClock(surface string) bool {
	return strings.TrimSpace(strings.ToLower(surface)) != SurfaceMeasure
}

// NormalizeSurface lowercases and trims a surface label, and answers
// `SurfaceCLI` for an empty one — the CLI is the only caller that can reach the
// search path without saying who it is.
func NormalizeSurface(s string) string {
	s = strings.TrimSpace(strings.ToLower(s))
	if s == "" {
		return SurfaceCLI
	}
	return s
}
