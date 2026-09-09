package capture

import (
	"strings"
	"testing"
)

// The retired miner's tally template must not get through the capture door.
// A worktree cut from an older base still runs the old miner, so the writer
// cannot be relied on to have stopped emitting it — the door is what holds.
func TestTallyTemplateIsRefusedAtTheDoor(t *testing.T) {
	body := "The `Bash` tool was invoked 2592 times during this session. " +
		"If this represents a repeatable workflow, capture the sequence + when to use it."
	if !tallyTemplate.MatchString(body) {
		t.Fatalf("the gate does not recognise the template it exists to refuse:\n%s", body)
	}
}

func TestTheGateRecognisesEveryToolAndCount(t *testing.T) {
	for _, s := range []string{
		"The `Read` tool was invoked 3 times during this session.",
		"The `mcp__foo__bar` tool was invoked 118 times during this session.",
		"prose before. The `Edit` tool was invoked 7 times during this session. prose after.",
	} {
		if !tallyTemplate.MatchString(s) {
			t.Errorf("not refused, but is the template: %q", s)
		}
	}
}

// A door that guesses costs a real capture. The gate matches the template and
// nothing wider — a note that legitimately discusses tool counts still lands.
func TestTheGateDoesNotRefuseARealNote(t *testing.T) {
	for _, s := range []string{
		"Run the Bash tool sparingly; it was slow in this session.",
		"The daemon was invoked 12 times during this session by the runner.",
		"Batch tool calls: three independent reads go in one message.",
		"The `Bash` tool was invoked during this session.",
	} {
		if tallyTemplate.MatchString(s) {
			t.Errorf("refused a real note: %q", s)
		}
	}
}

func TestTheRefusalIsCountedAndNothingIsWritten(t *testing.T) {
	c := &Capturer{}
	_, err := c.Do(Request{Text: "The `Bash` tool was invoked 41 times during this session."})
	if err == nil {
		t.Fatal("the tally template was captured; the gate did not hold")
	}
	if !strings.Contains(err.Error(), "tool-tally template") {
		t.Errorf("the refusal does not say why: %v", err)
	}
	if got := c.RefusedCaptures(); got != 1 {
		t.Errorf("refusal not counted: RefusedCaptures() = %d, want 1", got)
	}
}
