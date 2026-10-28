package dreaming

import (
	"path"

	"github.com/alexherrero/agentm/daemon/internal/linkrewrite"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// withMoveLinks adds the link repair to a job's planned moves (task 193 LD-7):
// every link that names a note one of `intents` moves, by its path, is pointed
// at where the note goes, the way the task mover repairs the links into a task
// folder. The lifecycle job's archive move and the refile job's re-file use it.
//
// The moved notes' own relative links out are recomputed from their new
// folder, inside the move's own intent. A note that links a moved one is
// edited by a new intent of `job`, journaled like the move it follows, and a
// note the job already changes tonight has the repair folded into that change,
// so one note never carries two intents. The repair leaves `personal/`,
// `standards/` and the entity builder's pages alone, as the mover's does, and
// a basename link, which still resolves after the move.
//
// It returns the intents and the notes it edited, with their link counts.
func withMoveLinks(root string, contract *rules.Rules, job, what string,
	intents []Intent) ([]Intent, []EditedNote, int) {
	vault := vaultRootOf(root)
	memRel := memoryRootRel(root, vault)
	vrel := func(rel string) string { return path.Join(memRel, rel) }
	var moves []linkrewrite.Move
	from, to := map[string]bool{}, map[string]bool{}
	for _, in := range intents {
		if in.To == "" || in.Delete {
			continue
		}
		m := linkrewrite.Move{From: vrel(in.Rel), To: vrel(in.To)}
		moves = append(moves, m)
		from[m.From], to[m.To] = true, true
	}
	if len(moves) == 0 {
		return intents, nil, 0
	}
	rw := linkrewrite.New(moves, nil)
	exists := afterRun(vault, from, to)
	links := 0
	byRel := map[string]int{}
	for i := range intents {
		in := &intents[i]
		byRel[in.Rel] = i
		if in.To == "" || in.Delete || in.After == nil {
			continue
		}
		if text, n := rw.Text(string(in.After), vrel(in.Rel), vrel(in.To), exists); n > 0 {
			in.After = []byte(text)
			links += n
		}
	}
	notes, err := vaultNotes(vault, contract)
	if err != nil {
		return intents, nil, links
	}
	edits, edited, n := linkEdits(vault, root, notes, from, rw, exists, job, what)
	links += n
	for _, e := range edits {
		i, ok := byRel[e.Rel]
		if !ok {
			intents = append(intents, e)
			continue
		}
		// The job already changes this note tonight: the repair rides in that
		// change, made to the text the job would write.
		in := &intents[i]
		if in.Delete || in.After == nil {
			continue
		}
		self := vrel(in.Rel)
		if text, k := rw.Text(string(in.After), self, self, exists); k > 0 {
			in.After = []byte(text)
		}
	}
	return intents, edited, links
}
