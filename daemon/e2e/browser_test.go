package e2e

import (
	"bytes"
	"io"
	"net/http"
	"testing"
	"time"
)

// The daemon listens on a fixed loopback port for as long as the machine is up.
// That is the point of a resident service, and it is also the whole exposure: a
// web page the operator happens to visit can make his browser talk to it.
//
// A loopback peer address does not distinguish those requests — a browser on this
// machine has one. `Origin` and `Host` do. These tests pin that, because the
// mitigation is three `if` statements and an untested `if` statement is a comment.

func TestBrowser_CrossOriginRequestIsRefused(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	env.write(t, "personal/2026/08/a-secret.md", `---
type: reference
status: active
captured: 2026-08-08T10:00:00Z
---
The account number is written down here.
`)
	d := start(t, bin, env)
	defer d.kill(t)

	// Sanity: the same call succeeds without a browser's headers.
	if hits := d.search(t, "account number", 5); len(hits.rows) == 0 {
		t.Fatal("setup: a native client should be able to search")
	}

	for _, origin := range []string{
		"http://evil.example.com",
		"https://evil.example.com",
		"null",
		"http://127.0.0.1.evil.example.com",
		"http://localhost.evil.example.com",
	} {
		code, body := d.rawMCP(t, map[string]string{"Origin": origin})
		if code != http.StatusForbidden {
			t.Errorf("Origin %q got HTTP %d, want 403 — a web page can read the vault\n  body: %s",
				origin, code, truncate(body))
		}
	}
}

// TestBrowser_RebindingHostIsRefused covers the attack that makes the Origin check
// insufficient on its own: the attacker's domain resolves to 127.0.0.1, so the
// browser considers the daemon same-origin and sends no cross-origin Origin at
// all. What it cannot hide is the hostname it was told to connect to.
func TestBrowser_RebindingHostIsRefused(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	d := start(t, bin, env)
	defer d.kill(t)

	for _, host := range []string{
		"evil.example.com",
		"evil.example.com:7821",
		"attacker.test",
	} {
		code, body := d.rawMCP(t, map[string]string{"Host": host})
		if code != http.StatusForbidden {
			t.Errorf("Host %q got HTTP %d, want 403 — DNS rebinding reaches the tools\n  body: %s",
				host, code, truncate(body))
		}
	}
}

// TestBrowser_LocalClientsStillWork is the other half. A guard that also refuses
// the legitimate client is not a fix, and the loopback forms below are all ones a
// real client or supervisor uses.
func TestBrowser_LocalClientsStillWork(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	d := start(t, bin, env)
	defer d.kill(t)

	cases := []struct {
		name    string
		headers map[string]string
	}{
		{"no Origin at all, as a native client sends", nil},
		{"an explicit loopback Origin", map[string]string{"Origin": "http://127.0.0.1:7821"}},
		{"localhost by name", map[string]string{"Origin": "http://localhost:7821"}},
		{"IPv6 loopback", map[string]string{"Origin": "http://[::1]:7821"}},
		{"a loopback Host header", map[string]string{"Host": "127.0.0.1:7821"}},
		{"localhost Host header", map[string]string{"Host": "localhost"}},
	}
	for _, tc := range cases {
		code, body := d.rawMCP(t, tc.headers)
		if code != http.StatusOK {
			t.Errorf("%s: got HTTP %d, want 200 — the guard is refusing a real client\n  body: %s",
				tc.name, code, truncate(body))
		}
	}
}

// TestBrowser_HealthIsGuardedToo — /health and /status report the vault path and
// the corpus shape, which is reconnaissance. The guard belongs on every route, not
// only the one that returns note bodies.
func TestBrowser_HealthIsGuardedToo(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	d := start(t, bin, env)
	defer d.kill(t)

	for _, path := range []string{"/health", "/status"} {
		req, err := http.NewRequest("GET", d.addr+path, nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Origin", "http://evil.example.com")
		resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != http.StatusForbidden {
			t.Errorf("%s with a hostile Origin got HTTP %d, want 403\n  body: %s",
				path, resp.StatusCode, truncate(string(body)))
		}
	}
}

// rawMCP issues one tools/list with caller-supplied headers and returns the raw
// status and body. It bypasses the `call` helper on purpose: this is a test about
// the HTTP envelope, not about the tool result.
func (p *proc) rawMCP(t *testing.T, headers map[string]string) (int, string) {
	t.Helper()
	body := []byte(`{"jsonrpc":"2.0","id":1,"method":"tools/list"}`)
	req, err := http.NewRequest("POST", p.addr+"/mcp", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		if k == "Host" {
			// Go's http.Request needs Host set on the struct, not the header map.
			req.Host = v
			continue
		}
		req.Header.Set(k, v)
	}
	resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
	if err != nil {
		t.Fatalf("request: %v", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(raw)
}

func truncate(s string) string {
	s = string(bytes.TrimSpace([]byte(s)))
	if len(s) > 200 {
		return s[:200] + "…"
	}
	return s
}
