package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/door"
)

// cmdDoor is how a writer asks whether it may write somewhere without asking.
//
// The same seam as the rules, the ledger, the queue, the registry and the tiers:
// the decision lives in one place and everybody asks it. A writer carrying its
// own copy of the permission rule would eventually disagree with the daemon
// about which paths are the operator's, and the disagreement would show up as a
// document changed without agreement.
//
// The exit status is the answer, so a shell can branch on it without parsing:
// zero for standing, two for alignment, three for a path this door does not
// govern. Two rather than one, so "you need to ask" is distinguishable from
// "the daemon broke".
func cmdDoor(args []string) error {
	fs := newFlagSet("door")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the decision as JSON")
	path := fs.String("path", "", "the vault-relative path to be written")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd door --path REL [--json]",
			extra[0])
	}
	if *path == "" {
		return fmt.Errorf("agentmd door needs --path: it answers about one place, " +
			"and a door with no path is not a question")
	}

	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}

	// Whether the file exists is the difference between creating a root document
	// — which a project may have as many of as it needs — and changing one,
	// which takes alignment. Resolved here rather than asked of the caller,
	// because a caller that answered it wrongly would get a standing verdict for
	// a write that needed agreement.
	abs := filepath.Join(cfg.VaultPath, filepath.FromSlash(cfg.MemoryRoot),
		filepath.FromSlash(*path))
	_, statErr := os.Stat(abs)
	exists := statErr == nil

	d := door.DefaultRoots().Judge(*path, exists)
	if *asJSON {
		if err := json.NewEncoder(os.Stdout).Encode(d); err != nil {
			return err
		}
	} else {
		fmt.Println(d.Permission)
		fmt.Fprintf(os.Stderr, "%s: %s\n", d.Path, d.Why)
	}

	switch d.Permission {
	case door.Standing:
		return nil
	case door.Alignment:
		return &exitError{code: 2, quiet: true,
			err: fmt.Errorf("%s needs alignment: %s", d.Path, d.Why)}
	default:
		return &exitError{code: 3, quiet: true,
			err: fmt.Errorf("%s is outside the project door", d.Path)}
	}
}
