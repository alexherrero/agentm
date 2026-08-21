package enrich

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"sync"
	"testing"
	"time"
)

// A stand-in for `claude`, compiled rather than scripted.
//
// The first version of this was a `#!/bin/sh` heredoc, which meant every model
// call failed on Windows and the batch logic — pure Go, with nothing
// platform-specific in it — was never actually tested there. CI caught it: three
// batch tests failed on windows-latest for want of a shell, and the honest fix
// is a portable stub rather than a `runtime.GOOS` skip that would have left the
// gap and hidden it.
//
// Built once per package run into a temporary directory. `go` is by definition
// present, since this is running under `go test`.

var (
	stubOnce sync.Once
	stubPath string
	stubErr  error
)

// stubBinary returns the path to the compiled stub, building it on first use.
func stubBinary(t *testing.T) string {
	t.Helper()
	stubOnce.Do(func() {
		dir, err := os.MkdirTemp("", "enrich-stub-")
		if err != nil {
			stubErr = err
			return
		}
		src := filepath.Join(dir, "main.go")
		if err := os.WriteFile(src, []byte(stubSource), 0o644); err != nil {
			stubErr = err
			return
		}
		out := filepath.Join(dir, "claude-stub")
		if runtime.GOOS == "windows" {
			out += ".exe"
		}
		cmd := exec.Command("go", "build", "-o", out, src)
		if b, err := cmd.CombinedOutput(); err != nil {
			stubErr = err
			t.Logf("building the stub: %s", b)
			return
		}
		stubPath = out
	})
	if stubErr != nil {
		t.Fatalf("could not build the model stub: %v", stubErr)
	}
	return stubPath
}

// stubSource is the whole fake `claude`. It is driven by environment variables
// rather than flags, because the command line it receives is the real one the
// Caller builds and must not be disturbed to accommodate a test.
const stubSource = `package main

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

func main() {
	if ms := os.Getenv("ENRICH_STUB_SLEEP_MS"); ms != "" {
		if n, err := strconv.Atoi(ms); err == nil {
			time.Sleep(time.Duration(n) * time.Millisecond)
		}
	}
	if os.Getenv("ENRICH_STUB_PRINT_CWD") != "" {
		wd, _ := os.Getwd()
		fmt.Printf(` + "`" + `{"cwd":%q}` + "`" + `, wd)
		return
	}
	if s := os.Getenv("ENRICH_STUB_STDOUT"); s != "" {
		fmt.Print(s)
	}
	if s := os.Getenv("ENRICH_STUB_STDERR"); s != "" {
		fmt.Fprint(os.Stderr, s)
	}
	if code := os.Getenv("ENRICH_STUB_EXIT"); code != "" {
		if n, err := strconv.Atoi(code); err == nil {
			os.Exit(n)
		}
	}
}
`

// stubOpts configures one stub invocation.
type stubOpts struct {
	stdout string
	stderr string
	exit   int
	sleep  time.Duration
	cwd    bool
}

// newStubCaller returns a Caller wired to the stub, configured by opts.
//
// The environment is set on the process rather than on the command because
// `Caller.command` builds the real argument list and a test that had to reach
// into it would be testing something other than what ships.
func newStubCaller(t *testing.T, o stubOpts) *Caller {
	t.Helper()
	bin := stubBinary(t)
	set := func(k, v string) {
		t.Setenv(k, v)
	}
	set("ENRICH_STUB_STDOUT", o.stdout)
	set("ENRICH_STUB_STDERR", o.stderr)
	set("ENRICH_STUB_EXIT", strconv.Itoa(o.exit))
	set("ENRICH_STUB_SLEEP_MS", strconv.Itoa(int(o.sleep/time.Millisecond)))
	if o.cwd {
		set("ENRICH_STUB_PRINT_CWD", "1")
	} else {
		set("ENRICH_STUB_PRINT_CWD", "")
	}
	c := DefaultCaller("sonnet")
	c.Bin = bin
	return c
}
