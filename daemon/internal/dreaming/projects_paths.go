package dreaming

import (
	"path/filepath"
)

// What the projects pass needs to name a file: where the space is, and how to
// say a path relative to the memory root.
//
// This is what is left of the `sequence` job. That job gave every document in
// `decisions/`, `designs/`, `research/` and `drafts/` a three-digit prefix in
// creation order, so `ls` and Obsidian showed the work in the order it
// happened. The operator reversed the decision on 2026-09-18, before its first
// run: the ordering those numbers carried is going to live in sub-folders
// instead, and a number in a filename would be a second scheme to keep in
// agreement with the first. No document was ever renamed — the reversal came
// while the run was still a report.
//
// The three helpers stay because they were never about numbering: they answer
// where the projects space is and what to call a path inside it, which the
// activity readings and the `completed/` moves both still ask.

// MoveRow is one file and where it went. Named for the move rather than for the
// job that used to make them, which is gone.
type MoveRow struct {
	From string `json:"from"`
	To   string `json:"to"`
}

// ProjectsRoot is the vault's `projects/` space, resolved from the memory root
// the way every other job here resolves a sibling space, and empty when the
// vault has none.
func ProjectsRoot(root string) string {
	space := filepath.Join(vaultRootOf(root), projectsSpaceName)
	if !isDirExact(space) {
		return ""
	}
	return space
}

// relTo renders `p` relative to `root`, in POSIX form — including the `../`
// spelling a vault-root sibling like `projects/` takes from the memory root.
func relTo(root, p string) (string, error) {
	rel, err := filepath.Rel(root, p)
	if err != nil {
		return "", err
	}
	return filepath.ToSlash(rel), nil
}
