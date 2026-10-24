package main

import (
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/buildinfo"
)

const (
	shaA = "aaaaaaaabcdefabcdefabcdefabcdefabcdefabc"
	shaB = "bbbbbbbbfedcbafedcbafedcbafedcbafedcbafe"
)

func stamped(sha string) buildinfo.Info {
	return buildinfo.Info{Commit: sha, Stamp: buildinfo.StampScript}
}

func TestDescribeBuildLevelIsOneQuietLine(t *testing.T) {
	got := describeBuild(stamped(shaA), stamped(shaA), &buildinfo.Clone{Head: shaA})
	want := []string{"running aaaaaaaa · primary clone aaaaaaaa"}
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestDescribeBuildCloneAheadWithoutDaemonChangesIsNotAWarning(t *testing.T) {
	// A release commit moves HEAD and changes nothing compiled in.
	got := describeBuild(stamped(shaA), stamped(shaA), &buildinfo.Clone{Head: shaB, Ahead: 1})
	if len(got) != 1 || !strings.HasSuffix(got[0], "(1 ahead, none touch daemon/)") {
		t.Fatalf("got %q", got)
	}
}

func TestDescribeBuildCloneAheadOnDaemonSaysRebuild(t *testing.T) {
	got := strings.Join(describeBuild(stamped(shaA), stamped(shaA),
		&buildinfo.Clone{Head: shaB, Ahead: 3, DaemonAhead: 2}), "\n")
	if !strings.Contains(got, "2 commit(s) touching daemon/") || !strings.Contains(got, "deploy-daemon.sh") {
		t.Fatalf("got %q", got)
	}
}

func TestDescribeBuildRebuildWithoutRestartNamesBoth(t *testing.T) {
	got := strings.Join(describeBuild(stamped(shaA), stamped(shaB), nil), "\n")
	if !strings.Contains(got, "this binary bbbbbbbb") || !strings.Contains(got, "kickstart") {
		t.Fatalf("got %q", got)
	}
}

func TestDescribeBuildUnstampedDaemonIsReported(t *testing.T) {
	got := strings.Join(describeBuild(buildinfo.Info{Stamp: buildinfo.StampNone}, stamped(shaA), nil), "\n")
	if !strings.Contains(got, "running unknown") || !strings.Contains(got, "recorded no commit") {
		t.Fatalf("got %q", got)
	}
}

func TestDescribeBuildBranchAheadOfCloneIsReportedNotRebuild(t *testing.T) {
	got := strings.Join(describeBuild(stamped(shaB), stamped(shaB),
		&buildinfo.Clone{Head: shaA, Behind: 2}), "\n")
	if !strings.Contains(got, "2 commit(s) the primary clone lacks") || strings.Contains(got, "deploy-daemon.sh") {
		t.Fatalf("got %q", got)
	}
}

func TestDescribeBuildGoStampSaysWhatItMeans(t *testing.T) {
	got := describeBuild(buildinfo.Info{Commit: shaA, Stamp: buildinfo.StampGo},
		buildinfo.Info{Commit: shaA, Stamp: buildinfo.StampGo}, nil)
	if !strings.Contains(got[0], "worktree build names the enclosing clone") {
		t.Fatalf("got %q", got)
	}
}
