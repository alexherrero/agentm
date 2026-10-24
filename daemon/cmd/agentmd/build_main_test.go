package main

import (
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/buildinfo"
)

func TestDescribeMainBehindIsLoudAndNamesTheCommit(t *testing.T) {
	got := describeMain(buildinfo.Main{Behind: 2, NewestMissing: shaB, NewestMissingSubject: "Fix the thing (#900)"})
	want := `  clone    ! the primary clone is 2 commits behind main; the newest it lacks is bbbbbbbb "Fix the thing (#900)"`
	if !strings.HasPrefix(got, want) || !strings.Contains(got, "scheduled job") {
		t.Fatalf("got %q", got)
	}
}

func TestDescribeMainLevelAndBranch(t *testing.T) {
	if got := describeMain(buildinfo.Main{}); got != "  clone    level with main\n" {
		t.Errorf("level: %q", got)
	}
	got := describeMain(buildinfo.Main{Branch: "feature", Ahead: 1})
	if strings.Contains(got, "!") || !strings.Contains(got, "on branch `feature` and 1 commit ahead of main") {
		t.Errorf("branch: %q", got)
	}
}
