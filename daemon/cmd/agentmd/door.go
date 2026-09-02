package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

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
	atVault := fs.Bool("at-vault-root", false,
		"judge against the vault-level authority table (five spaces + session "+
			"grants) instead of the memory-root project door")
	var grants grantFlags
	fs.Var(&grants, "grant",
		"a project slug this session holds a grant for (repeatable; vault-root mode only)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd door --path REL "+
			"[--at-vault-root] [--grant SLUG] [--json]", extra[0])
	}
	if *path == "" {
		return fmt.Errorf("agentmd door needs --path: it answers about one place, " +
			"and a door with no path is not a question")
	}
	if len(grants) > 0 && !*atVault {
		return fmt.Errorf("--grant is a vault-level concept; pass --at-vault-root with it")
	}

	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}

	// Whether the file exists is the difference between creating a root document
	// — which a project may have as many of as it needs — and changing one,
	// which takes alignment. Resolved here rather than asked of the caller,
	// because a caller that answered it wrongly would get a standing verdict for
	// a write that needed agreement. Vault-root mode resolves against the vault
	// itself; the classic mode keeps its memory-root anchor.
	base := filepath.Join(cfg.VaultPath, filepath.FromSlash(cfg.MemoryRoot))
	if *atVault {
		base = cfg.VaultPath
	}
	abs := filepath.Join(base, filepath.FromSlash(*path))
	_, statErr := os.Stat(abs)
	exists := statErr == nil

	var d door.Decision
	if *atVault {
		d = door.DefaultAuthority().JudgeSpace(*path, exists, door.NewGrants(grants...))
	} else {
		d = door.DefaultRoots().Judge(*path, exists)
	}
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

// grantFlags collects repeated --grant values.
type grantFlags []string

func (g *grantFlags) String() string { return strings.Join(*g, ",") }
func (g *grantFlags) Set(v string) error {
	*g = append(*g, v)
	return nil
}
