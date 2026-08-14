package main

import (
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/index"
)

// queryEmbedText is task 3.5's whole decision: what text does the dense arm
// embed. These tests are the "byte-identical when absent" proof the plan asks
// for, pinned as assertions rather than left as a claim in a commit message —
// task 1 proved its own non-regression by re-scoring the `and` arm at exactly
// 10.9% after the refactor; this is the same discipline at the function that
// makes the claim true by construction.

// No question supplied is the case that must not move. queryEmbedText must
// return the terms completely unchanged — not merely equal in value, but the
// same decision every measurement before this task made.
func TestQueryEmbedTextKeepsTermsWhenQuestionAbsent(t *testing.T) {
	got := queryEmbedText("gemini model always", "", 2048)
	if got != "gemini model always" {
		t.Fatalf("queryEmbedText = %q, want the terms unchanged", got)
	}
}

// A question that is present but blank after trimming must be treated the
// same as absent — an MCP caller or a shell wrapper can easily send "" or
// "   " rather than omitting the argument, and either must fall back to the
// terms rather than embedding whitespace.
func TestQueryEmbedTextKeepsTermsWhenQuestionIsWhitespaceOnly(t *testing.T) {
	got := queryEmbedText("gemini model always", "   ", 2048)
	if got != "gemini model always" {
		t.Fatalf("queryEmbedText = %q, want the terms (whitespace-only question treated as absent)", got)
	}
}

// A question that fits the window must be embedded in place of the terms,
// unchanged — this is the rung's whole mechanism, so it is asserted as an
// exact string rather than merely "not the terms."
func TestQueryEmbedTextPrefersQuestionWhenPresent(t *testing.T) {
	question := "Which small Gemini model was picked for always-on background work?"
	got := queryEmbedText("small gemini model picked always background", question, 2048)
	if got != question {
		t.Fatalf("queryEmbedText = %q, want the natural question %q", got, question)
	}
}

// A question longer than the window must come back truncated, at the exact
// budget TruncateQuery derives — called directly here as the independently
// hand-literal-tested function it is (vector_test.go's
// TestTruncateQueryCutsToHandDerivedBudget), not re-derived, so this test
// checks queryEmbedText's delegation rather than re-proving the arithmetic.
func TestQueryEmbedTextTruncatesLongQuestion(t *testing.T) {
	question := strings.Repeat("x", 200)
	const ctxTokens = 100 // budget = (100-64)*3 = 108, per TruncateQuery's own test
	got := queryEmbedText("short terms", question, ctxTokens)
	want := index.TruncateQuery(question, ctxTokens)
	if got != want {
		t.Fatalf("queryEmbedText did not delegate truncation: got %d bytes, want %d", len(got), len(want))
	}
	if len(got) != 108 {
		t.Fatalf("queryEmbedText truncated to %d bytes, want the hand-derived 108", len(got))
	}
	if got == "short terms" {
		t.Fatal("queryEmbedText fell back to the terms despite a (long) question being present")
	}
}

// A short question must never be truncated — TruncateQuery's own no-op path,
// exercised here through queryEmbedText so the whole chain is covered, not
// just the low-level helper.
func TestQueryEmbedTextDoesNotTruncateAShortQuestion(t *testing.T) {
	question := "when did the daemon ship"
	got := queryEmbedText("terms", question, 2048)
	if got != question {
		t.Fatalf("queryEmbedText = %q, want the short question unchanged", got)
	}
}
