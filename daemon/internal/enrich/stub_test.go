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
	"encoding/json"
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
	s := os.Getenv("ENRICH_STUB_STDOUT")
	if os.Getenv("ENRICH_STUB_PRINT_CWD") != "" {
		wd, _ := os.Getwd()
		s = fmt.Sprintf(` + "`" + `{"cwd":%q}` + "`" + `, wd)
	}
	if s != "" {
		// The real CLI, asked for --output-format json, wraps its text in an
		// envelope that carries the call's usage. So does the stub, unless a
		// test asks for the raw text to prove the Caller refuses it.
		envelope := false
		for i, a := range os.Args {
			if a == "--output-format" && i+1 < len(os.Args) && os.Args[i+1] == "json" {
				envelope = true
			}
		}
		if envelope && os.Getenv("ENRICH_STUB_RAW") == "" {
			in, _ := strconv.Atoi(os.Getenv("ENRICH_STUB_IN_TOKENS"))
			out, _ := strconv.Atoi(os.Getenv("ENRICH_STUB_OUT_TOKENS"))
			isErr := os.Getenv("ENRICH_STUB_EXIT") != "" && os.Getenv("ENRICH_STUB_EXIT") != "0"
			b, _ := json.Marshal(map[string]any{
				"type": "result", "subtype": "success", "is_error": isErr,
				"result": s, "total_cost_usd": float64(in+out) / 1e6,
				"usage": map[string]any{
					"input_tokens": in, "output_tokens": out,
					"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
				},
			})
			s = string(b)
		}
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
	// raw makes the stub print its text without the envelope.
	raw bool
	// inTokens and outTokens are the usage the envelope reports.
	inTokens, outTokens int
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
	raw := ""
	if o.raw {
		raw = "1"
	}
	set("ENRICH_STUB_RAW", raw)
	set("ENRICH_STUB_IN_TOKENS", strconv.Itoa(o.inTokens))
	set("ENRICH_STUB_OUT_TOKENS", strconv.Itoa(o.outTokens))
	c := DefaultCaller("sonnet")
	c.Bin = bin
	return c
}
