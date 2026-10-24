package main

import (
	"fmt"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/buildinfo"
)

// describeBuild is the build line in `agentmd status` (#819): the commit the
// running daemon was built from, beside this binary's and the primary clone's
// HEAD, with a line of its own for each way they disagree.
//
// The running process and the binary on disk are told apart because they come
// apart: a rebuild without a restart leaves the old process answering with full
// health, and the client that prints this is the new binary. The clone is
// compared on the commits that touch daemon/, because a release commit or a
// harness change moves HEAD without changing a byte of the daemon.
//
// clone is nil when no primary clone is configured.
func describeBuild(resident, local buildinfo.Info, clone *buildinfo.Clone) []string {
	parts := []string{"running " + resident.Short()}
	if local.Commit != resident.Commit || local.Modified != resident.Modified {
		parts = append(parts, "this binary "+local.Short())
	}
	if clone != nil && clone.Head != "" {
		parts = append(parts, "primary clone "+buildinfoShort(clone.Head))
	}
	if resident.Stamp == buildinfo.StampGo {
		// Go's own stamp names the enclosing clone's HEAD for a worktree build,
		// so the commit may not be the code. Say so where it is printed.
		parts = append(parts, "(Go's stamp: a worktree build names the enclosing clone)")
	}
	lines := []string{strings.Join(parts, " · ")}

	if resident.Commit == "" {
		lines = append(lines, "! the running daemon recorded no commit — it predates build stamping, "+
			"or was built outside a clone; rebuild with scripts/deploy-daemon.sh")
		return lines
	}
	if local.Commit != "" && local.Commit != resident.Commit {
		lines = append(lines, "! the running daemon is not this binary — a rebuild without a restart; "+
			"restart it: launchctl kickstart -k gui/$(id -u)/com.agentm.daemon")
	}
	if resident.Modified {
		lines = append(lines, "! the running daemon was built with uncommitted daemon/ changes")
	}
	if clone == nil {
		return lines
	}
	switch {
	case clone.Err != "":
		lines = append(lines, "! could not compare with the primary clone at "+clone.Dir+": "+clone.Err)
	case clone.DaemonAhead > 0:
		lines = append(lines, fmt.Sprintf("! the primary clone has %d commit(s) touching daemon/ that the "+
			"running daemon lacks — rebuild: scripts/deploy-daemon.sh", clone.DaemonAhead))
	case clone.Ahead > 0:
		lines[0] += fmt.Sprintf(" (%d ahead, none touch daemon/)", clone.Ahead)
	}
	if clone.Err == "" && clone.Behind > 0 {
		lines = append(lines, fmt.Sprintf("! the running daemon has %d commit(s) the primary clone lacks — "+
			"built from a branch or a worktree ahead of it", clone.Behind))
	}
	return lines
}

func buildinfoShort(sha string) string {
	return buildinfo.Info{Commit: sha}.Short()
}

// describeMain is the clone line in `agentmd status` (#815): how the primary
// clone, which every hook, skill and scheduled job runs from, stands against
// the origin/main its last fetch left. Behind is loud; a branch or commits
// ahead are said and are not an error, because a session working mid-branch
// is ordinary. Nothing here fetches.
func describeMain(m buildinfo.Main) string {
	switch {
	case m.Sentence() != "":
		return "  clone    ! " + m.Sentence() + " — every hook, skill and scheduled job runs that older code\n"
	case m.Err != "":
		return "  clone    could not compare with origin/main: " + m.Err + "\n"
	case m.Note() != "":
		return "  clone    " + m.Note() + "\n"
	default:
		return "  clone    level with main\n"
	}
}

// renderBuild indents describeBuild's lines into the status grid.
func renderBuild(lines []string) string {
	var b strings.Builder
	for i, l := range lines {
		if i == 0 {
			fmt.Fprintf(&b, "  build    %s\n", l)
			continue
		}
		fmt.Fprintf(&b, "           %s\n", l)
	}
	return b.String()
}
