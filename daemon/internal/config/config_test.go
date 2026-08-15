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
	want := []string{"Agent/memory", "Agent/desk", "Agent/external"}
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
		want := "memory,desk,external"
		if strings.Join(got, ",") != want {
			t.Errorf("memory_root %q gave %v, want %s", root, got, want)
		}
	}
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
