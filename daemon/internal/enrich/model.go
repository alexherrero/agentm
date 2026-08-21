// Package enrich turns a raw capture into a memory.
//
// Capture writes the file and returns; enrichment runs afterwards, out of band,
// and does the work that needs judgment — distilling the body into real prose,
// correcting the title and slug while nothing links to them, assigning type and
// altitude, filling tags and aliases. The model is never on the critical path: a
// failure here leaves the note `unfiled` and the nightly pass picks it up.
//
// This file is only the call. The gates around it are the rest of the package,
// and there are eleven of them for a reason — the model's writing becomes the
// corpus, so every field it touches is checked by something that is not a model.
package enrich

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// Caller runs one model call.
//
// It shells out to `claude -p` rather than speaking to an API, because that is
// what this machine has credentials for: `--bare` would close the last hole in
// the isolation below, but it skips the keychain read and demands an
// `ANTHROPIC_API_KEY` that does not exist in this environment.
type Caller struct {
	// Bin is the executable. Overridable so a test can point at a stub without
	// a real model, and so a machine with a non-standard install still works.
	Bin string
	// Model is the model name passed through to `claude -p`. A name, not a
	// tier: tier qualification is earned by sampled audit against the strong
	// tier, and that machinery is not this pass's job.
	Model string
	// Timeout bounds a single call.
	Timeout time.Duration
	// MaxTurns caps the conversation.
	//
	// Not 1. Tool use is already blocked by the disallowed-tools list, and the
	// Python pass this is ported from found that a batch whose honest answer is
	// "skip all of these" reliably needs a second turn — capping at one turned
	// 132 of those into hard errors instead of into skips.
	MaxTurns int
	// SystemPrompt is prepended to every call.
	SystemPrompt string
}

// DefaultCaller is the shipped configuration.
func DefaultCaller(model string) *Caller {
	if model == "" {
		model = "sonnet"
	}
	return &Caller{
		Bin:          "claude",
		Model:        model,
		Timeout:      120 * time.Second,
		MaxTurns:     4,
		SystemPrompt: defaultSystemPrompt,
	}
}

const defaultSystemPrompt = "You rewrite notes in a personal memory vault. " +
	"You reply with JSON and nothing else — no preamble, no code fence, no commentary."

// disallowedTools is everything that would let the call reach the disk or the
// network. Enrichment reads one note, which is handed to it in the prompt, and
// returns one object. It has no business opening anything.
var disallowedTools = []string{
	"Bash", "Read", "Write", "Edit", "Glob", "Grep",
	"WebFetch", "WebSearch", "Task", "ToolSearch",
}

// hookIsolation is the flag that keeps this call out of the vault it is writing
// to.
//
// Without it, this project's own recall hooks fire inside the subprocess: the
// model's session queries the daemon that spawned it, and the vault ends up in
// the prompt that is rewriting the vault. That is a correctness problem before
// it is a performance one, and it produces plausible output rather than an
// error, which is why `command` asserts on it and a test asserts on `command`.
const hookIsolation = `{"disableAllHooks":true}`

// command builds the invocation.
//
// Split out from Call so a test can inspect what would run. Both isolation
// measures below fail silently — a contaminated generation looks exactly like a
// clean one — so they are asserted on the command rather than inferred from a
// result.
func (c *Caller) command(ctx context.Context, prompt, cwd string) *exec.Cmd {
	args := []string{
		"-p", prompt,
		"--model", c.Model,
		// Load-bearing. See hookIsolation.
		"--settings", hookIsolation,
		"--system-prompt", c.SystemPrompt,
		"--disallowed-tools", strings.Join(disallowedTools, ","),
		"--max-turns", fmt.Sprint(c.MaxTurns),
	}
	cmd := exec.CommandContext(ctx, c.Bin, args...)
	// Killing the process is not enough to unblock the read.
	//
	// `claude` spawns children, and a child inherits the write end of the pipe
	// Go creates for stdout. When the context fires, CommandContext kills
	// `claude` — but Wait still blocks until every holder of that pipe closes it,
	// so a hung grandchild holds this call for its own lifetime rather than for
	// the timeout. Measured at 5.1s against a 200ms deadline before WaitDelay was
	// set. The grace period gives a well-behaved process time to flush, then
	// forces the pipes shut.
	cmd.WaitDelay = 2 * time.Second
	// The second load-bearing measure. Claude Code auto-loads any CLAUDE.md or
	// AGENTS.md it finds above its working directory, so inheriting the daemon's
	// cwd would feed this repository's own instructions into a generation that is
	// supposed to be blind to them. The Python pass this is ported from ran two
	// alias pilots before someone noticed, and both were very likely contaminated
	// that way. A fresh temporary directory has no such file above it.
	cmd.Dir = cwd
	return cmd
}

// ErrNoResponse is returned when the model produced nothing usable. It is an
// error rather than an empty result on purpose: a caller that treats "no output"
// as "nothing to change" writes the unenriched note and marks it done.
var ErrNoResponse = errors.New("enrich: model returned no usable response")

// Call runs one enrichment call and returns the raw response text.
func (c *Caller) Call(ctx context.Context, prompt string) (string, error) {
	// A fresh directory per call rather than one for the daemon's lifetime: the
	// guarantee wanted here is "nothing above this path", and a long-lived
	// directory is somewhere another process can drop a file.
	cwd, err := os.MkdirTemp("", "agentm-neutral-cwd-")
	if err != nil {
		return "", fmt.Errorf("enrich: neutral working directory: %w", err)
	}
	defer os.RemoveAll(cwd)

	ctx, cancel := context.WithTimeout(ctx, c.Timeout)
	defer cancel()

	cmd := c.command(ctx, prompt, cwd)
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return "", fmt.Errorf("enrich: call timed out after %s: %w", c.Timeout, ctx.Err())
		}
		// A non-zero exit with an empty stderr is common enough here — usage
		// limits come back that way — that reporting only stderr produced 132
		// journal lines reading "failed: " and nothing else in the Python pass.
		detail := strings.TrimSpace(stderr.String())
		if detail == "" {
			detail = strings.TrimSpace(stdout.String())
		}
		if detail == "" {
			detail = "(no output)"
		}
		return "", fmt.Errorf("enrich: %s exited %w: %s", c.Bin, err, truncate(detail, 400))
	}

	out := strings.TrimSpace(stdout.String())
	if out == "" {
		return "", ErrNoResponse
	}
	return out, nil
}

// CallJSON runs one call and unmarshals the response into v.
//
// The model is asked for JSON and nothing else, and mostly obliges; when it
// wraps the object in a fence or a sentence, the outermost balanced object is
// extracted rather than the call being failed. What is *not* tolerated is a
// partial parse: a response that yields an object missing the fields the caller
// needs is the caller's problem to reject, and this returns the error rather
// than a half-filled struct.
func (c *Caller) CallJSON(ctx context.Context, prompt string, v any) error {
	raw, err := c.Call(ctx, prompt)
	if err != nil {
		return err
	}
	obj, err := extractJSON(raw)
	if err != nil {
		return err
	}
	if err := json.Unmarshal([]byte(obj), v); err != nil {
		return fmt.Errorf("enrich: response is not the expected shape: %w", err)
	}
	return nil
}

// extractJSON pulls the outermost balanced JSON object or array out of a
// response, ignoring braces inside strings.
//
// A regex was the first attempt and it was wrong on the first note whose body
// contained a `}` inside a quoted string, which on this corpus is any note about
// JSON. Balance-counting with string awareness is barely longer and is right.
func extractJSON(s string) (string, error) {
	start := strings.IndexAny(s, "{[")
	if start < 0 {
		return "", fmt.Errorf("%w: no JSON in %q", ErrNoResponse, truncate(s, 200))
	}
	open := s[start]
	close := byte('}')
	if open == '[' {
		close = ']'
	}

	depth := 0
	inString := false
	escaped := false
	for i := start; i < len(s); i++ {
		ch := s[i]
		switch {
		case escaped:
			escaped = false
		case ch == '\\' && inString:
			escaped = true
		case ch == '"':
			inString = !inString
		case inString:
			// Braces inside a string are text.
		case ch == open:
			depth++
		case ch == close:
			depth--
			if depth == 0 {
				return s[start : i+1], nil
			}
		}
	}
	return "", fmt.Errorf("%w: unbalanced JSON in %q", ErrNoResponse, truncate(s, 200))
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
