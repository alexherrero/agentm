package config

import (
	"strings"
	"testing"
)

// The vector arm's scope is derived from `memory_root`, never written as a
// literal. The root has moved twice, and a hardcoded `Agent/memory` would resolve
// to nothing on the next move — silently, because an empty scope embeds zero
// notes and a vector arm with no vectors looks exactly like one that is cold.
func TestDefaultEmbedScopeFollowsMemoryRoot(t *testing.T) {
	got := defaultEmbedScope("Agent")
	want := []string{"Agent/memory", "Agent/desk", "Agent/external", "Agent/diagnostics"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("got %v, want %v", got, want)
	}
}

// A vault whose memory root is the vault root yields unprefixed names, not names
// with a stray leading slash — that is the pre-migration topology and it is still
// a correct install.
func TestDefaultEmbedScopeWithoutMemoryRoot(t *testing.T) {
	for _, root := range []string{"", "  ", "/"} {
		got := defaultEmbedScope(root)
		want := "memory,desk,external,diagnostics"
		if strings.Join(got, ",") != want {
			t.Errorf("memory_root %q gave %v, want %s", root, got, want)
		}
	}
}

// `diagnostics` is deliberately IN the default scope (filing-v2 2a): the daily
// digests and scorecards it holds lived under `desk` before the move and were
// dense-retrievable; the move must not silently drop them from the vector arm.
func TestDefaultEmbedScopeIncludesDiagnostics(t *testing.T) {
	for _, s := range defaultEmbedScope("Agent") {
		if s == "Agent/diagnostics" {
			return
		}
	}
	t.Fatalf("diagnostics missing from default embed scope: %v", defaultEmbedScope("Agent"))
}

// `_meta` must never be in the default scope. Its notes run to 200,000 tokens and
// would be embedded as a single centroid; that is the case a chunking policy
// exists for, and there is no chunking policy.
func TestDefaultEmbedScopeExcludesMeta(t *testing.T) {
	for _, s := range defaultEmbedScope("Agent") {
		if strings.Contains(s, "_meta") || strings.Contains(s, "_vault-archive") {
			t.Errorf("default scope includes %q, which has no chunking policy", s)
		}
	}
}

// A trailing slash in the configured root must not produce a doubled separator —
// the scope is matched as a path prefix, and `Agent//memory` matches nothing.
func TestDefaultEmbedScopeNormalizesRoot(t *testing.T) {
	got := defaultEmbedScope("Agent/")
	for _, s := range got {
		if strings.Contains(s, "//") {
			t.Fatalf("scope %q contains a doubled separator", s)
		}
	}
	if got[0] != "Agent/memory" {
		t.Fatalf("got %v, want Agent/memory first", got)
	}
}
