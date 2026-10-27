package dreaming

import (
	"path/filepath"
	"strings"
	"testing"
)

// A tracker's derived lines move with its folder (task 193 step 5, LD-3): the
// links line and the artifacts line both name files inside the folder by path,
// and the mover's rewrite carries them into `completed/tasks/`.
func TestTheMoverCarriesATrackersArtifactsLine(t *testing.T) {
	root, space := projectVault(t)
	closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	dir := filepath.Join(space, "agentm", "tasks", "001-done")
	writeAt(t, dir, "runs/a.md", "# Run a\n")
	base := "projects/agentm/tasks/001-done/"
	writeAt(t, dir, "tracker.md", "---\nkind: tracker\ntitle: 001-done\nproject: agentm\ntask: 001-done\nstatus: done\n"+
		"opened: 2026-07-01\nupdated: 2026-08-01\nclosed: 2026-08-01\n---\n\n"+
		"[["+base+"plan|Plan]] · [["+base+"progress|Progress log]]\n\n"+
		"Artifacts: [["+base+"runs/a|a]]\n\n"+
		"## Objective\n\nShip.\n\n## State\n\nShipped.\n\n## Next\n\nNothing.\n\n## Outcome\n\nIt shipped.\n")

	plan, _ := PlanTaskMoves(root, taskRules(t), taskNow, 0)
	moved := ""
	for _, f := range plan.folders {
		for _, in := range f {
			if strings.HasSuffix(in.To, "completed/tasks/001-done/tracker.md") {
				moved = string(in.After)
			}
		}
	}
	done := "projects/agentm/completed/tasks/001-done/"
	for _, want := range []string{
		"[[" + done + "plan|Plan]] · [[" + done + "progress|Progress log]]\n\n",
		"Artifacts: [[" + done + "runs/a|a]]\n\n## Objective",
	} {
		if !strings.Contains(moved, want) {
			t.Errorf("the moved tracker does not read %q:\n%s", want, moved)
		}
	}
}
