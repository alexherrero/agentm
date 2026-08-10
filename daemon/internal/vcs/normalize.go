package vcs

// Unicode normalization is the one place where go-git and the git on the
// operator's PATH disagree about what a filename is.
//
// macOS writes filenames decomposed: the "ó" in "Lección" is stored as an "o"
// followed by a combining acute accent. Git's index stores the composed form.
// C-git reconciles the two with core.precomposeunicode, which it turns on by
// default on macOS: it precomposes every name it reads back from the filesystem,
// so the index entry and the directory entry compare equal and the tree reads
// clean. go-git implements none of that. It sees the composed index entry with
// no match on disk and calls it a deletion, sees the decomposed name on disk
// with no match in the index and calls it an untracked addition, and reports a
// pair of changes that no commit can ever clear.
//
// That is not a cosmetic difference here. Dirty() is what the corpus-write gate
// asks before any job rewrites the vault, and it refuses on a dirty worktree —
// so one accented filename anywhere in the corpus held the gate shut against
// every job behind it. The vault is full of accented filenames; two of them are
// what surfaced this.
//
// So this file does what core.precomposeunicode does, in the two places that
// matter: reading status, and staging paths. It is deliberately narrow. Folding
// happens only for a pair that is a normalization split and nothing else, and
// only when the file on disk carries exactly the content and mode the index
// already records. Anything else — a staged change, a real edit, a third
// spelling, an unreadable file — is reported as dirty. A gate that opens when it
// should not is a worse failure than one that stays shut, so every case this
// cannot prove benign resolves to "dirty".

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"

	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/filemode"
	"github.com/go-git/go-git/v5/plumbing/format/index"
	"golang.org/x/text/unicode/norm"
)

// resolvePrecompose reports whether this repository's paths should be folded to
// NFC before they are compared or staged.
//
// The answer follows git's own: core.precomposeunicode when the repository sets
// it, and otherwise the platform default git would have chosen — true on macOS,
// false everywhere else. It matters that this is not simply `GOOS == "darwin"`.
// On a normalization-sensitive filesystem the two spellings are two different
// files, and folding them there would hide a real deletion behind a real
// addition. Reading the config also gives a test, and an operator, a way to
// exercise the fold on a machine that is not a Mac.
func resolvePrecompose(repo *git.Repository) bool {
	fallback := runtime.GOOS == "darwin"
	if repo == nil {
		return fallback
	}
	cfg, err := repo.Config()
	if err != nil || cfg == nil {
		return fallback
	}
	switch strings.ToLower(strings.TrimSpace(
		cfg.Raw.Section("core").Option("precomposeunicode"))) {
	case "true", "yes", "on", "1":
		return true
	case "false", "no", "off", "0":
		return false
	}
	return fallback
}

// composed returns the name git's index would hold for this path.
func (r *Repo) composed(path string) string {
	if !r.precompose {
		return path
	}
	return norm.NFC.String(path)
}

// composedAll is composed over a list, for the paths a commit message names.
func (r *Repo) composedAll(paths []string) []string {
	if !r.precompose {
		return paths
	}
	out := make([]string, len(paths))
	for i, p := range paths {
		out[i] = norm.NFC.String(p)
	}
	return out
}

// statusEntry is one path and the state go-git reported for it.
type statusEntry struct {
	path string
	st   git.FileStatus
}

// foldStatus turns go-git's status map into the list of genuinely uncommitted
// paths, collapsing the composed/decomposed pairs c-git would never have
// reported in the first place.
func (r *Repo) foldStatus(status git.Status) ([]string, error) {
	groups := make(map[string][]statusEntry, len(status))
	for path, st := range status {
		if st == nil {
			continue
		}
		if st.Worktree == git.Unmodified && st.Staging == git.Unmodified {
			continue
		}
		key := r.composed(path)
		groups[key] = append(groups[key], statusEntry{path: path, st: *st})
	}

	var out []string
	for key, group := range groups {
		// One entry under a key is the ordinary case: an ASCII path, an
		// already-composed one, or a change that is simply real.
		if len(group) == 1 {
			out = append(out, group[0].path)
			continue
		}
		// More than one path folding to the same composed name can only be a
		// normalization split — normalization does not fold case or anything
		// else. Two of them is the shape c-git hides; anything else is a mess
		// this has no business calling clean.
		if len(group) != 2 {
			for _, e := range group {
				out = append(out, e.path)
			}
			continue
		}
		benign, err := r.pairIsNormalizationOnly(group[0], group[1])
		if err != nil {
			return nil, err
		}
		if !benign {
			// The note really did change. Report it once, under the name the
			// index knows it by.
			out = append(out, key)
		}
	}
	sort.Strings(out)
	return out, nil
}

// pairIsNormalizationOnly reports whether two paths that differ only in Unicode
// normalization describe one unchanged file.
//
// The shape it accepts is exact: one path present in the index and absent from
// the filesystem walk, whose index entry matches HEAD, and one path the walk
// found that the index has never heard of — and the file on disk hashes to the
// blob the index already records, with the same mode. That is a file c-git would
// have matched to its index entry and called unmodified. Every other pairing
// says no.
func (r *Repo) pairIsNormalizationOnly(a, b statusEntry) (bool, error) {
	indexed, onDisk := a, b
	if !isIndexedButUnseen(indexed.st) {
		indexed, onDisk = b, a
	}
	if !isIndexedButUnseen(indexed.st) || !isUntracked(onDisk.st) {
		return false, nil
	}

	idx, err := r.repo.Storer.Index()
	if err != nil {
		return false, fmt.Errorf("index: %w", err)
	}
	entry, err := idx.Entry(indexed.path)
	if err != nil {
		// The index does not actually hold it. Not a shape to fold.
		return false, nil
	}

	hash, mode, err := blobIdentity(filepath.Join(r.root, filepath.FromSlash(onDisk.path)))
	if err != nil {
		// Unreadable, or gone between the status walk and now. Fail closed.
		return false, nil
	}
	return hash == entry.Hash && mode == entry.Mode, nil
}

// isIndexedButUnseen matches the composed index entry the filesystem walk did not
// find. Its staging state must be unmodified: an entry that also differs from
// HEAD carries a staged change, which is uncommitted work by any reading.
func isIndexedButUnseen(st git.FileStatus) bool {
	return st.Staging == git.Unmodified && st.Worktree == git.Deleted
}

// isUntracked matches the decomposed name the walk found and the index does not
// have. go-git marks both halves of an untracked file's status.
func isUntracked(st git.FileStatus) bool {
	return st.Staging == git.Untracked && st.Worktree == git.Untracked
}

// recomposeIndexEntry refiles a freshly staged entry from the name the
// filesystem gave it to the name the index uses, dropping whatever stale entry
// stood under that name.
//
// This is the staging half of core.precomposeunicode, and it is done after the
// add rather than before it on purpose. Git folds the pathspec and then opens
// the folded name, which works only because a Mac's local volume treats the two
// spellings as one file. The vault does not live on a local volume; it lives on
// a synced mount whose behaviour here is not something to bet the write path on.
// So the file is read under the name that was actually observed, and only the
// index key is composed.
//
// The trap this avoids is the one the incident found the hard way: git's
// pathspec folding also applies to arguments, so `git rm --cached <decomposed>`
// quietly removes the composed entry instead. Naming both spellings explicitly
// is what keeps that from happening by accident.
func (r *Repo) recomposeIndexEntry(from, to string) error {
	idx, err := r.repo.Storer.Index()
	if err != nil {
		return fmt.Errorf("index: %w", err)
	}
	moved, err := idx.Entry(from)
	if err != nil {
		// Nothing was staged under that spelling, so there is nothing to refile.
		return nil
	}
	kept := make([]*index.Entry, 0, len(idx.Entries))
	for _, e := range idx.Entries {
		if e.Name == from || e.Name == to {
			continue
		}
		kept = append(kept, e)
	}
	moved.Name = to
	idx.Entries = append(kept, moved)
	return r.repo.Storer.SetIndex(idx)
}

// blobIdentity is the hash and mode git would record for the file at `abs`,
// computed the way git computes them — a symlink hashes its target, not the file
// it points at.
func blobIdentity(abs string) (plumbing.Hash, filemode.FileMode, error) {
	fi, err := os.Lstat(abs)
	if err != nil {
		return plumbing.ZeroHash, filemode.Empty, err
	}
	mode, err := filemode.NewFromOSFileMode(fi.Mode())
	if err != nil {
		return plumbing.ZeroHash, filemode.Empty, err
	}
	var content []byte
	if fi.Mode()&os.ModeSymlink != 0 {
		target, err := os.Readlink(abs)
		if err != nil {
			return plumbing.ZeroHash, filemode.Empty, err
		}
		content = []byte(target)
	} else {
		content, err = os.ReadFile(abs)
		if err != nil {
			return plumbing.ZeroHash, filemode.Empty, err
		}
	}
	return plumbing.ComputeHash(plumbing.BlobObject, content), mode, nil
}
