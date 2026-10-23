package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// A trace whose first request is the brief a session was started with.
func briefTrace(session, asked, candidates string) string {
	return "---\ntitle: " + session + "\nkind: session-trace\nstatus: active\nsession: " + session +
		"\n---\n\n## Asked\n\n" + asked + "\n\n## Outcome\n\nDone.\n\n## Candidates\n\n" + candidates + "\n"
}

const brief = "In crickets, fix the unit gate. Never merge main back into a branch; rebase it instead, and " +
	"never pass the delete-branch flag from a worktree."

// The 2026-10-07 shape (task 192 step 6): a standing paragraph pasted into the
// brief of three sessions is three copies of one text, not the same thing said
// three times. A sighting that quotes its own trace's `## Asked` does not count.
func TestThreeTracesQuotingTheirOwnBriefDoNotPromote(t *testing.T) {
	root := t.TempDir()
	line := "- explicit always/never directive — “Never merge main back into a branch; rebase it instead...”"
	for _, s := range []string{"a", "b", "c"} {
		writeRaw(t, root, "memory/episodic/"+s+".md", briefTrace(s, brief, line))
	}
	plan, err := PlanPromote(root, nil, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Promotions) != 0 {
		t.Errorf("a pasted brief promoted: %+v", plan.Promotions)
	}
	// Two of the three quoting their brief leave one independent sighting: still no promotion.
	writeRaw(t, root, "memory/episodic/c.md", briefTrace("c", "work on the vault", line))
	if plan, _ := PlanPromote(root, nil, time.Now()); len(plan.Promotions) != 0 {
		t.Errorf("one independent sighting promoted: %+v", plan.Promotions)
	}
}

// A sighting that quotes the always-load tier is the session carrying a rule it
// already loads, wherever the session was.
func TestThreeTracesQuotingAStandingRuleDoNotPromote(t *testing.T) {
	// With and without an Obsidian folder: the standards beside the memory root
	// are the vault's either way, as the miner reads them (the release review
	// of 2026-10-22 found them missed without one).
	for _, obsidian := range []bool{true, false} {
		t.Run(fmt.Sprintf("obsidian=%v", obsidian), func(t *testing.T) {
			vault := t.TempDir()
			root := filepath.Join(vault, "agent")
			dirs := []string{filepath.Join(vault, "standards")}
			if obsidian {
				dirs = append(dirs, filepath.Join(vault, ".obsidian"))
			}
			for _, d := range dirs {
				if err := os.MkdirAll(d, 0o755); err != nil {
					t.Fatal(err)
				}
			}
			if err := os.WriteFile(filepath.Join(vault, "standards", "user-preferences.md"),
				[]byte("# How I want things done\n\nKeep the plan on disk, never in the conversation.\n"), 0o644); err != nil {
				t.Fatal(err)
			}
			line := "- explicit always/never directive — “Keep the plan on disk, never in the conversation.”"
			for _, s := range []string{"a", "b", "c"} {
				writeRaw(t, root, "memory/episodic/"+s+".md", briefTrace(s, "work on the vault", line))
			}
			plan, err := PlanPromote(root, nil, time.Now())
			if err != nil {
				t.Fatal(err)
			}
			if len(plan.Promotions) != 0 {
				t.Errorf("a standing rule promoted: %+v", plan.Promotions)
			}
		})
	}
}

// The real thing: three sessions that each said it, in words no brief and no
// standing rule carries, still promote.
func TestThreeIndependentSightingsStillPromote(t *testing.T) {
	vault := t.TempDir()
	root := filepath.Join(vault, "agent")
	for _, d := range []string{filepath.Join(vault, ".obsidian"), filepath.Join(vault, "standards")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(vault, "standards", "user-preferences.md"),
		[]byte("Keep the plan on disk.\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	line := "- explicit always/never directive — “Always run the battery before a release tag.”"
	for _, s := range []string{"a", "b", "c"} {
		writeRaw(t, root, "memory/episodic/"+s+".md", briefTrace(s, brief, line))
	}
	plan, err := PlanPromote(root, nil, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Promotions) != 1 || len(plan.Promotions[0].Sources) != 3 {
		t.Errorf("three independent sightings did not promote: %+v", plan.Promotions)
	}
}

// The quote rule is reflect.py's `quoted_in`, case for case
// (scripts/test_reflect_origins.py `TheQuoteRule`).
func TestTheQuoteRuleMatchesTheMiners(t *testing.T) {
	src := candidateKey("# Global Claude Code Instructions\n\n## Commit messages\n\nDo not append a Co-Authored-By " +
		"trailer to git commit messages. The user is the sole author of intent. Always refresh a stale branch by " +
		"rebase, never by merging main back into it.\n")
	for _, tc := range []struct {
		excerpt string
		want    bool
	}{
		{"...intent. Always refresh a stale branch by rebase, never by...", true},
		{"Ok, so. Do not append a Co-Authored-By trailer to git commit", true},
		{"Do not append a trailer, and ship the release tonight.", false},
		{"ok yes", false},
	} {
		if got := quotedIn(tc.excerpt, src); got != tc.want {
			t.Errorf("quotedIn(%q) = %v, want %v", tc.excerpt, got, tc.want)
		}
	}
	if quotedIn("Do not append a Co-Authored-By trailer", "") {
		t.Error("an empty source quoted something")
	}
}
