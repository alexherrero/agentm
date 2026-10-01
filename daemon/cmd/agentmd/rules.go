package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// cmdRules is how everything that is not this binary asks what the filing
// contract says.
//
// The Python batch layer — dreaming, lint, the frontmatter validator — used to
// be heading toward parsing the rules file itself. Two parsers of one contract
// is a drift surface, and the design's whole claim is that a type added to the
// rules exists everywhere at once. So there is one parser, it lives in Go, and
// everyone else asks it: `agentmd rules --json`, once per run, not per note.
//
// It deliberately does not need a running daemon. A lint pass or a CI gate can
// ask a built binary about a file on disk without a server, an index, or a vault.
func cmdRules(args []string) error {
	fs := newFlagSet("rules")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the parsed contract as JSON")
	file := fs.String("file", "", "parse this file instead of resolving one")
	initTo := fs.String("init", "", "write the embedded default to this path, if absent")
	history := fs.Bool("history", false,
		"list every committed text of the contract with its two hashes, newest first")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd rules [--json] [--file PATH]", extra[0])
	}

	if *initTo != "" {
		return initRules(*initTo)
	}
	if *history {
		vaultPath := ""
		if cfg, cfgErr := config.Load(*opts); cfgErr == nil {
			vaultPath = cfg.VaultPath
		}
		loaded, err := rules.Load(vaultPath)
		if err != nil {
			return fmt.Errorf("filing is halted — the rules block does not parse: %w", err)
		}
		return printContractHistory(loaded, *asJSON)
	}

	var (
		loaded *rules.Rules
		err    error
	)
	if *file != "" {
		loaded, err = rules.LoadFile(*file)
	} else {
		// A vault is resolved when one is configured, and skipped when not: the
		// resolution chain falls through to the embedded default either way, so a
		// machine with no vault can still be asked what the contract says.
		vaultPath := ""
		if cfg, cfgErr := config.Load(*opts); cfgErr == nil {
			vaultPath = cfg.VaultPath
		}
		loaded, err = rules.Load(vaultPath)
	}
	if err != nil {
		// Fail closed, and say so in the words the caller needs. A non-zero exit
		// here is what halts a filing pass upstream.
		return fmt.Errorf("filing is halted — the rules block does not parse: %w", err)
	}

	if *asJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(loaded)
	}

	where := loaded.Source
	if loaded.IsPackagedDefault {
		where = "the copy embedded in this binary — no vault instance, so edits to one will not take effect"
	}
	fmt.Printf("storage rules: OK — %s\n", where)
	fmt.Printf("  memory types : %s\n", strings.Join(loaded.TypesSorted(), ", "))
	fmt.Printf("  default type : %s\n", loaded.DefaultType)
	fmt.Printf("  record kinds : %d registered\n", len(loaded.RecordKinds))
	fmt.Printf("  deprecations : %d retired values mapped\n", len(loaded.Deprecations))
	fmt.Printf("  rules_hash   : %s\n", loaded.Hash)
	// The part an enrichment judgment reads, which is what a note's stamp and
	// the ledger carry; an edit that moves only the line above re-judges nothing.
	fmt.Printf("  judgment     : %s\n", loaded.JudgmentHash)

	classes := make([]string, 0, len(loaded.Classes))
	for c := range loaded.Classes {
		classes = append(classes, c)
	}
	sort.Strings(classes)
	fmt.Printf("  classes      : %s\n", strings.Join(classes, ", "))
	return nil
}

// initRules seeds a vault's own rules file from the embedded default.
//
// Never overwrites: the file is the operator's once it exists, and the whole
// point of the arrangement is that their edits are what runs.
func initRules(path string) error {
	if _, err := os.Stat(path); err == nil {
		return fmt.Errorf("%s already exists — it is yours to edit, and this would "+
			"overwrite it", path)
	} else if !os.IsNotExist(err) {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(path, []byte(rules.Default()), 0o644); err != nil {
		return err
	}
	fmt.Printf("wrote %s — edit it, and filing changes on the next capture\n", path)
	return nil
}

// printContractHistory lists the contract's versions with both hashes: the one
// in force, the packaged default, then each committed text, newest first. A
// row whose judgment hash differs from the next older row's is an edit that
// re-owed every judgment; one where only the contract hash moved re-owed
// nothing (task 181).
func printContractHistory(loaded *rules.Rules, asJSON bool) error {
	versions, refused, herr := contractHistory(context.Background(), loaded)
	if asJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(versions)
	}
	if herr != nil {
		fmt.Fprintln(os.Stderr, "rules:", herr)
	}
	for i, v := range versions {
		label := v.Source
		if v.Commit != "" {
			label = v.Date + " " + v.Commit[:min(len(v.Commit), 9)]
		}
		moved := ""
		if v.Commit != "" && i+1 < len(versions) && versions[i+1].Commit != "" &&
			versions[i+1].JudgmentHash != v.JudgmentHash {
			moved = "  · judgment moved"
		}
		fmt.Printf("%-24s rules_hash %s  judgment %s%s\n", label, v.Hash, v.JudgmentHash, moved)
	}
	if refused > 0 {
		fmt.Printf("%d committed text(s) today's parser refuses\n", refused)
	}
	return nil
}
