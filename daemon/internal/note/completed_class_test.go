package note

import "testing"

// agentm-vault § Lifecycle per space: one archive path class — `archive`,
// `_archive`, `completed` — ranked at x0.30 wherever it appears, penalized
// rather than excluded.
//
// This test was written for the narrower rule that preceded it, where only
// `projects/<slug>/completed/` earned the class. The axis-per-space landing
// widened it to the three names at any depth, so the table is rewritten to the
// new rule rather than relaxed: the rows that changed answer are named below,
// each with the directory it now covers.
func TestTheArchiveFamilyEarnsTheClass(t *testing.T) {
	cases := map[string]bool{
		// A project's own finished work, as before.
		"projects/agentm/completed/research-note.md":     true,
		"projects/agentm/completed/deep/nested/brief.md": true,
		"projects/agentm/Completed/brief.md":             true,
		// A finished project. Changed answer: the old rule matched depth 2 only,
		// so a whole project moved to `projects/completed/<slug>/` ranked at full
		// weight — the one place the design most wants quiet.
		"projects/completed/x.md":            true,
		"projects/completed/blog/2026/on.md": true,
		// The memory archive. Changed answer: it did not exist when the old rule
		// was written, and it is where every archived card now lives.
		"agent/archive/memory/semantic/a-fact.md": true,
		// The operator's own, kept exactly as they keep it.
		"personal/Home/_Archive/2019-note.md":        true,
		"projects/blog/drafts/_archive/old-draft.md": true,
		"agent/memory/semantic/completed/x.md":       true,
		// Live work earns nothing.
		"projects/agentm/decisions/a-ruling.md": false,
		"agent/memory/semantic/a-fact.md":       false,
		// A file is not a directory. `completed.md` is a note about finished
		// work, not a finished note.
		"projects/agentm/completed.md": false,
		"archive.md":                   false,
		// A word inside a directory name is not the directory name.
		"projects/agentm/research/completed-work/x.md": false,
		"projects/agentm/archived-ideas/x.md":          false,
	}
	for rel, want := range cases {
		got := false
		for _, f := range classify(rel, "", "a body", "", "", 0, false) {
			if f == ClassArchiveClass {
				got = true
			}
		}
		if got != want {
			t.Errorf("classify(%q) archive-class = %v, want %v", rel, got, want)
		}
	}
}

// Archival twice over is not twice as finished.
func TestTheArchiveClassIsEarnedOnce(t *testing.T) {
	flags := classify("projects/completed/blog/drafts/_archive/x.md", "", "a body", "", "", 0, false)
	n := 0
	for _, f := range flags {
		if f == ClassArchiveClass {
			n++
		}
	}
	if n != 1 {
		t.Errorf("a path archival at two depths earned the class %d times, want 1", n)
	}
	if m := Multiplier(flags); m != 0.30 {
		t.Errorf("Multiplier = %v, want 0.30 — compounding would put it under the decay floor", m)
	}
}

func TestTheArchiveClassIsWeightedLikeTheOtherDemotions(t *testing.T) {
	if w := Weights[ClassArchiveClass]; w != 0.30 {
		t.Errorf("Weights[ClassArchiveClass] = %v, want 0.30", w)
	}
	if m := Multiplier([]string{ClassArchiveClass}); m != 0.30 {
		t.Errorf("Multiplier(archive-class) = %v, want 0.30", m)
	}
}

// The retired spelling keeps its weight until the next reindex re-derives the
// flags, because an unlisted class multiplies by 1.0 — dropping the entry would
// rank every not-yet-reindexed completed record *up* for the length of the gap.
func TestTheRetiredCompletedFlagStillWeighs(t *testing.T) {
	if w := Weights[ClassCompleted]; w != 0.30 {
		t.Errorf("Weights[ClassCompleted] = %v, want 0.30 for stale index rows", w)
	}
}
