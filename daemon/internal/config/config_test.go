package config

import (
	"strings"
	"testing"
)

// The vector arm's scope is derived from `memory_root`, never written as a
// literal. The root has moved twice, and a hardcoded `agent/memory` would resolve
// to nothing on the next move — silently, because an empty scope embeds zero
// notes and a vector arm with no vectors looks exactly like one that is cold.
func TestDefaultEmbedScopeFollowsMemoryRoot(t *testing.T) {
	got := defaultEmbedScope("agent")
	want := []string{"agent/memory", "agent/desk", "agent/external", "agent/diagnostics", "agent/inbox", "projects", "calendar", "standards/voice", "personal/ideas", "resources", "systems"}
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
		want := "memory,desk,external,diagnostics,inbox,projects,calendar,standards/voice,personal/ideas,resources,systems"
		if strings.Join(got, ",") != want {
			t.Errorf("memory_root %q gave %v, want %s", root, got, want)
		}
	}
}

// `inbox` is deliberately IN the default scope (agentm-vault plan 16): a card
// in the drop folder is dampened, not walled, so it has to come back for a
// query that matches it on both arms. Left out, the lexical arm would return a
// card the dense arm cannot see, which reads as a ranking decision and is
// really an absent vector.
func TestDefaultEmbedScopeCoversTheDropFolder(t *testing.T) {
	got := strings.Join(defaultEmbedScope("agent"), ",")
	if !strings.Contains(got, "agent/inbox") {
		t.Errorf("the drop folder is outside the vector arm's scope: %s", got)
	}
}

// The two shared spaces of 2026-09-24 are IN the default scope, named at the
// vault root like `projects` and `calendar` rather than under the memory root.
// Their notes arrive by a move from spaces already in scope — the reference
// cards and the watchlist from `projects`, the homelab notes from
// `memory/semantic` — so leaving either out would drop vectors the dense arm
// already had.
func TestDefaultEmbedScopeCoversTheResourcesAndSystemsSpaces(t *testing.T) {
	for _, root := range []string{"agent", ""} {
		scope := defaultEmbedScope(root)
		for _, want := range []string{"resources", "systems"} {
			found := false
			for _, s := range scope {
				if s == want {
					found = true
				}
				if s == root+"/"+want && root != "" {
					t.Errorf("memory_root %q: %q is a vault-root sibling, not under the memory root: %v", root, s, scope)
				}
			}
			if !found {
				t.Errorf("memory_root %q: %q is outside the vector arm's scope: %v", root, want, scope)
			}
		}
	}
}

// The idea cards are IN the default scope and the rest of `personal/` is not
// (agentm-vault part 13). The cards left `memory/semantic/`, which is in scope,
// for `personal/ideas/`; named alone, because `personal/` is the operator's and
// the carve-out is one folder, not the space.
func TestDefaultEmbedScopeCoversTheIdeaCardsAndNoOtherPersonalFolder(t *testing.T) {
	scope := defaultEmbedScope("agent")
	ideas := false
	for _, s := range scope {
		if s == "personal/ideas" {
			ideas = true
			continue
		}
		if s == "personal" || strings.HasPrefix(s, "personal/") {
			t.Errorf("%q widens the vector arm into the operator's space: %v", s, scope)
		}
	}
	if !ideas {
		t.Fatalf("personal/ideas missing from the default embed scope: %v", scope)
	}
}

// `diagnostics` is deliberately IN the default scope (filing-v2 2a): the daily
// digests and scorecards it holds lived under `desk` before the move and were
// dense-retrievable; the move must not silently drop them from the vector arm.
func TestDefaultEmbedScopeIncludesDiagnostics(t *testing.T) {
	for _, s := range defaultEmbedScope("agent") {
		if s == "agent/diagnostics" {
			return
		}
	}
	t.Fatalf("diagnostics missing from default embed scope: %v", defaultEmbedScope("agent"))
}

// The voice library is IN the default scope (agentm-vault plan 05): its rules
// were dense-retrievable under projects/ and moved to the vault-root
// `standards/voice/`; the first retrieval gate after the move flipped the
// questions that expect them to misses. The rest of `standards/` stays out.
func TestDefaultEmbedScopeIncludesTheVoiceLibraryOnly(t *testing.T) {
	scope := defaultEmbedScope("agent")
	voice := false
	for _, s := range scope {
		if s == "standards/voice" {
			voice = true
		}
		if s == "standards" {
			t.Fatalf("the rule files must not be in the vector scope: %v", scope)
		}
	}
	if !voice {
		t.Fatalf("standards/voice missing from default embed scope: %v", scope)
	}
}

// Captures into the projects space land at the vault-root `projects/` — the
// default is unprefixed because the space is a sibling of the memory root, not
// under it (filing-v2 2b, after the move).
func TestDefaultSpacesProjectsIsTheVaultRootSibling(t *testing.T) {
	got := defaultSpaces("agent")["projects"]
	if got != "projects" {
		t.Fatalf("projects space = %q, want %q", got, "projects")
	}
}

// The vault-root `projects/` space is IN the default scope, unprefixed (filing-v2
// 2b): it is a sibling of the memory root, not under it, and the project trees
// were dense-retrievable under `desk` before the merge.
func TestDefaultEmbedScopeIncludesRootProjects(t *testing.T) {
	for _, s := range defaultEmbedScope("agent") {
		if s == "projects" {
			return
		}
	}
	t.Fatalf("root Projects missing from default embed scope: %v", defaultEmbedScope("agent"))
}

// `_meta` must never be in the default scope. Its notes run to 200,000 tokens and
// would be embedded as a single centroid; that is the case a chunking policy
// exists for, and there is no chunking policy.
func TestDefaultEmbedScopeExcludesMeta(t *testing.T) {
	for _, s := range defaultEmbedScope("agent") {
		if strings.Contains(s, "_meta") || strings.Contains(s, "_vault-archive") {
			t.Errorf("default scope includes %q, which has no chunking policy", s)
		}
	}
}

// A trailing slash in the configured root must not produce a doubled separator —
// the scope is matched as a path prefix, and `agent//memory` matches nothing.
func TestDefaultEmbedScopeNormalizesRoot(t *testing.T) {
	got := defaultEmbedScope("agent/")
	for _, s := range got {
		if strings.Contains(s, "//") {
			t.Fatalf("scope %q contains a doubled separator", s)
		}
	}
	if got[0] != "agent/memory" {
		t.Fatalf("got %v, want agent/memory first", got)
	}
}
