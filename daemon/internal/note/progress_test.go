package note

import (
	"bytes"
	"strings"
	"testing"
	"unicode/utf8"
)

func TestAProgressLogIsKnownByItsName(t *testing.T) {
	cases := map[string]bool{
		"Projects/agentm/_harness/progress.md":                    true,
		"Projects/agentm/_harness/progress-online-recall.md":      true,
		"Projects/agentm/tasks/measure-online-recall/progress.md": true,
		"Agent/memory/semantic/progress-report.md":                true,
		"Projects/agentm/_harness/PLAN-online-recall.md":          false,
		"Agent/memory/semantic/my-progress.md":                    false,
		"Projects/agentm/_harness/PROGRESS.md":                    false,
		"Projects/agentm/_harness/progress.txt":                   false,
	}
	for rel, want := range cases {
		if got := IsProgressLog(rel); got != want {
			t.Errorf("IsProgressLog(%q) = %v, want %v", rel, got, want)
		}
	}
}

func TestTheBoundIsThePythonArmsNumber(t *testing.T) {
	// recall.py: DEFAULT_TOKEN_BUDGET 20_000 * 4 + _ENTRY_READ_SLACK_CHARS 8192.
	if ProgressHeadBytes != 88192 {
		t.Errorf("ProgressHeadBytes = %d, want 88192", ProgressHeadBytes)
	}
}

func TestALogThatFitsIsReadWhole(t *testing.T) {
	raw := []byte("2026-09-12 one entry\n2026-09-13 another\n")
	if got := ProgressHead(raw); !bytes.Equal(got, raw) {
		t.Errorf("a short log was cut: %q", got)
	}
}

func TestTheHeadEndsOnALineBreak(t *testing.T) {
	raw := []byte(strings.Repeat("2026-09-12 a line of the log\n", 10000))
	head := ProgressHead(raw)
	if len(head) > ProgressHeadBytes {
		t.Fatalf("head is %d bytes, over the %d bound", len(head), ProgressHeadBytes)
	}
	if !bytes.HasPrefix(raw, head) || !bytes.HasSuffix(head, []byte("\n")) {
		t.Errorf("the head is not a prefix of the log ending on a line break")
	}
	if len(raw)-len(head) < len(raw)/2 {
		t.Errorf("the head kept %d of %d bytes; the bound did not apply", len(head), len(raw))
	}
}

func TestOneEnormousLineKeepsWholeRunes(t *testing.T) {
	raw := []byte(strings.Repeat("é", ProgressHeadBytes))
	head := ProgressHead(raw)
	if len(head) > ProgressHeadBytes || !utf8.Valid(head) {
		t.Errorf("head of %d bytes, valid UTF-8 %v", len(head), utf8.Valid(head))
	}
}
