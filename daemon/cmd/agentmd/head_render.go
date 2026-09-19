package main

import (
	"fmt"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/index"
)

// The card's readable head, rendered for a terminal.
//
// One renderer, because the design's claim is that the three surfaces print the
// same head — `memory_search` over MCP, `agentmd search` here, and
// `/memory search` in the shell. A hit that reads three different ways is three
// things a reader has to learn, and the whole point of the head is that it is
// the card in the card's own order.

// headline is the title, or the path's stem when a note carries none. Never
// empty: the first line of a hit has to say what the thing is.
func headline(r index.Result) string {
	if t := strings.TrimSpace(r.Title); t != "" {
		return t
	}
	stem := r.Path
	if i := strings.LastIndex(stem, "/"); i >= 0 {
		stem = stem[i+1:]
	}
	return strings.TrimSuffix(stem, ".md")
}

// kindLine is the one-line row under the title: what the note is, how much it
// is worth, and where it stands. Omitted entirely when a note carries none of
// them, rather than printed as a row of blanks.
func kindLine(r index.Result) string {
	var parts []string
	if r.Type != "" {
		parts = append(parts, r.Type)
	} else if r.Kind != "" {
		parts = append(parts, r.Kind)
	}
	if r.Importance > 0 {
		parts = append(parts, fmt.Sprintf("importance %d", r.Importance))
	}
	if r.Status != "" {
		parts = append(parts, r.Status)
	}
	// Only when it says something: `lifecycle: active` is the default every
	// filing stamps, and repeating it on every hit is noise that hides the
	// `dormant` the reader actually needs to see.
	if r.Lifecycle != "" && r.Lifecycle != "active" {
		parts = append(parts, r.Lifecycle)
	}
	if r.Project != "" {
		parts = append(parts, "project "+r.Project)
	}
	return strings.Join(parts, " · ")
}
