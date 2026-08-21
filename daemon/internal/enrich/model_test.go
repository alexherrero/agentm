package enrich

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// The two isolation measures are asserted on the command rather than on a
// result, and that is the whole point of this file.
//
// Both of them fail *silently*. A call that runs with hooks enabled returns a
// perfectly well-formed enrichment — one that happens to have had the vault read
// into the prompt that is rewriting the vault. A call that inherits the daemon's
// working directory returns a perfectly well-formed enrichment that had this
// repository's own CLAUDE.md folded into it. Neither produces an error, a
// warning, or a difference you could see in the output. The Python pass this is
// ported from ran two alias pilots before anyone noticed the second one, and it
// was caught by hand-reading generations rather than by anything automatic.
//
// So there is no result to assert on. The command is the artifact.

func TestTheCommandDisablesHooks(t *testing.T) {
	c := DefaultCaller("sonnet")
	cmd := c.command(context.Background(), "a prompt", t.TempDir())

	args := strings.Join(cmd.Args, " ")
	if !strings.Contains(args, `{"disableAllHooks":true}`) {
		t.Errorf("the call does not disable hooks, so this project's recall hooks "+
			"fire inside it and the vault reaches the prompt rewriting the vault.\n"+
			"args: %v", cmd.Args)
	}
	// And it is passed as the value of --settings, not merely present somewhere.
	for i, a := range cmd.Args {
		if a == "--settings" {
			if i+1 >= len(cmd.Args) || cmd.Args[i+1] != hookIsolation {
				t.Errorf("--settings is not followed by the isolation payload: %v",
					cmd.Args)
			}
			return
		}
	}
	t.Errorf("no --settings flag at all: %v", cmd.Args)
}

func TestTheCommandRunsFromANeutralDirectory(t *testing.T) {
	dir := t.TempDir()
	c := DefaultCaller("sonnet")
	cmd := c.command(context.Background(), "a prompt", dir)

	if cmd.Dir == "" {
		t.Fatal("the call inherits the daemon's working directory, so Claude Code " +
			"loads this repository's CLAUDE.md into a generation meant to be blind " +
			"to it")
	}
	if cmd.Dir != dir {
		t.Errorf("cmd.Dir = %q, want the neutral directory %q", cmd.Dir, dir)
	}
}

// Call has to actually create that directory, not just accept one. A neutral cwd
// the caller forgot to supply is the same bug with an extra step.
func TestCallCreatesItsOwnNeutralDirectoryWithNothingAboveIt(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the stub is a shell script")
	}
	stub := writeStub(t, `#!/bin/sh
printf '{"cwd":"%s"}' "$PWD"
`)
	c := DefaultCaller("sonnet")
	c.Bin = stub

	var got struct {
		Cwd string `json:"cwd"`
	}
	if err := c.CallJSON(context.Background(), "prompt", &got); err != nil {
		t.Fatalf("CallJSON: %v", err)
	}
	if got.Cwd == "" {
		t.Fatal("the subprocess reported no working directory")
	}
	// The guarantee is "no CLAUDE.md or AGENTS.md above this path". Walk up and
	// check, rather than trusting that a temp directory is clean.
	for dir := got.Cwd; ; {
		for _, name := range []string{"CLAUDE.md", "AGENTS.md"} {
			if _, err := os.Stat(filepath.Join(dir, name)); err == nil {
				t.Errorf("%s sits above the neutral cwd at %s", name, dir)
			}
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	// And it is cleaned up.
	if _, err := os.Stat(got.Cwd); !os.IsNotExist(err) {
		t.Errorf("the neutral directory survived the call: %s", got.Cwd)
	}
}

func TestTheCommandBlocksEveryToolThatReachesDiskOrNetwork(t *testing.T) {
	c := DefaultCaller("sonnet")
	cmd := c.command(context.Background(), "a prompt", t.TempDir())

	var list string
	for i, a := range cmd.Args {
		if a == "--disallowed-tools" && i+1 < len(cmd.Args) {
			list = cmd.Args[i+1]
		}
	}
	if list == "" {
		t.Fatalf("no --disallowed-tools: %v", cmd.Args)
	}
	// Enrichment is handed one note in its prompt and returns one object. Any
	// tool that opens a file or a socket is a way for it to read something it
	// was not given.
	for _, want := range []string{"Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch"} {
		if !strings.Contains(list, want) {
			t.Errorf("%s is not disallowed: %q", want, list)
		}
	}
}

// A non-zero exit with an empty stderr is how usage limits arrive. Reporting
// only stderr produced 132 journal lines reading "failed: " in the Python pass.
func TestAnEmptyStderrFailureStillSaysSomething(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the stub is a shell script")
	}
	stub := writeStub(t, `#!/bin/sh
echo "usage limit reached"
exit 1
`)
	c := DefaultCaller("sonnet")
	c.Bin = stub

	_, err := c.Call(context.Background(), "prompt")
	if err == nil {
		t.Fatal("a non-zero exit was not an error")
	}
	if !strings.Contains(err.Error(), "usage limit reached") {
		t.Errorf("the failure says nothing about why: %v", err)
	}
}

// Empty output is an error, not an empty result. A caller that reads "no output"
// as "nothing to change" writes the unenriched note and marks it done.
func TestSilenceIsAnError(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the stub is a shell script")
	}
	stub := writeStub(t, "#!/bin/sh\nexit 0\n")
	c := DefaultCaller("sonnet")
	c.Bin = stub

	if _, err := c.Call(context.Background(), "prompt"); !errors.Is(err, ErrNoResponse) {
		t.Errorf("empty output gave %v, want ErrNoResponse", err)
	}
}

func TestATimeoutIsReportedAsOne(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the stub is a shell script")
	}
	stub := writeStub(t, "#!/bin/sh\nsleep 5\n")
	c := DefaultCaller("sonnet")
	c.Bin = stub
	c.Timeout = 200 * time.Millisecond

	start := time.Now()
	_, err := c.Call(context.Background(), "prompt")
	if err == nil {
		t.Fatal("a hung call returned successfully")
	}
	if !strings.Contains(err.Error(), "timed out") {
		t.Errorf("a hung call did not report a timeout: %v", err)
	}
	// The bound that matters is timeout + the pipe-flush grace, not the child's
	// own lifetime. `claude` spawns children and a child inherits the stdout
	// pipe, so without WaitDelay this returned in 5.1s against a 200ms deadline —
	// killed on time and then blocked on a grandchild holding the pipe open. The
	// stub sleeps 5s precisely so that failure is distinguishable from success.
	bound := c.Timeout + 2*time.Second + 500*time.Millisecond
	if elapsed := time.Since(start); elapsed > bound {
		t.Errorf("the call took %s to return, over the %s bound — the process was "+
			"killed but something still held its pipes", elapsed, bound)
	}
}

// The model is told to reply with JSON and nothing else, and mostly obliges.
// When it does not, the object is extracted rather than the note being failed —
// but a brace inside a quoted string is text, and on this corpus any note about
// JSON contains one. A regex was the first attempt and this is the case that
// broke it.
func TestJSONIsExtractedFromAResponseThatWrapsIt(t *testing.T) {
	for _, tc := range []struct {
		name, raw, want string
	}{
		{"bare", `{"a":1}`, `{"a":1}`},
		{"fenced", "```json\n{\"a\":1}\n```", `{"a":1}`},
		{"prefaced", "Here you go:\n{\"a\":1}", `{"a":1}`},
		{"nested", `{"a":{"b":2}}`, `{"a":{"b":2}}`},
		{"brace in string", `{"body":"use } to close"}`, `{"body":"use } to close"}`},
		{"escaped quote then brace", `{"body":"a \" and a }"}`, `{"body":"a \" and a }"}`},
		{"array", `[{"a":1},{"a":2}]`, `[{"a":1},{"a":2}]`},
		{"trailing prose", `{"a":1}` + "\nhope that helps", `{"a":1}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := extractJSON(tc.raw)
			if err != nil {
				t.Fatalf("extractJSON(%q): %v", tc.raw, err)
			}
			if got != tc.want {
				t.Errorf("extractJSON(%q) = %q, want %q", tc.raw, got, tc.want)
			}
		})
	}
}

func TestUnparseableResponsesAreErrors(t *testing.T) {
	for _, raw := range []string{
		"",
		"I could not do that.",
		`{"a":1`,
	} {
		if _, err := extractJSON(raw); err == nil {
			t.Errorf("extractJSON(%q) returned no error", raw)
		}
	}
}

// writeStub drops an executable shell script standing in for `claude`.
func writeStub(t *testing.T, body string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "claude-stub")
	if err := os.WriteFile(p, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
	return p
}
