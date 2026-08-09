package main

import (
	"flag"
	"os"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

func newFlagSet(name string) *flag.FlagSet {
	fs := flag.NewFlagSet(name, flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	return fs
}

// bindCommon registers the flags every subcommand shares. --vault exists as an
// override for tests and one-off runs; the resolution order in package config is
// what governs in normal use, and no path here is ever a default literal.
func bindCommon(fs *flag.FlagSet) *config.Options {
	opts := &config.Options{}
	fs.StringVar(&opts.ConfigPath, "config", "",
		"kernel config to resolve the vault path from (default ~/.claude/.agentm-config.json)")
	fs.StringVar(&opts.VaultPath, "vault", "", "override the resolved vault path")
	fs.StringVar(&opts.IndexPath, "index", "", "override the index file location")
	fs.IntVar(&opts.Port, "port", config.DefaultPort, "port to serve on (0 picks a free one)")
	fs.DurationVar(&opts.ReconcileEvery, "reconcile", 0,
		"how often to re-walk the vault for changes the notifier missed")
	return opts
}

// markPortSet records whether --port was actually given, so a config-file port is
// not silently overwritten by the flag's default.
func markPortSet(fs *flag.FlagSet, opts *config.Options) {
	fs.Visit(func(f *flag.Flag) {
		if f.Name == "port" {
			opts.PortSet = true
		}
	})
}
