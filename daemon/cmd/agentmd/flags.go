package main

import (
	"flag"
	"log/slog"
	"os"
	"strings"

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

// splitPositional pulls the first bare (non-flag) argument out of args and
// returns it alongside everything else, so a subcommand taking one positional
// can accept flags on either side of it.
//
// Go's flag package stops at the first non-flag argument, which means
// `gate corpus-write --json` parses no flags at all — they land in Args()
// instead, silently. Every caller writes the name first, so the parser has to
// cope with it rather than the callers having to know.
//
// A `--flag value` pair is kept together: `value` is not a positional, and
// mistaking it for one would consume the flag's argument.
func splitPositional(args []string) (positional string, rest []string) {
	rest = make([]string, 0, len(args))
	for i := 0; i < len(args); i++ {
		a := args[i]
		if strings.HasPrefix(a, "-") {
			rest = append(rest, a)
			// A flag written as `--name value` takes the next token with it;
			// `--name=value` carries its own.
			if !strings.Contains(a, "=") && takesValue(strings.TrimLeft(a, "-")) && i+1 < len(args) {
				i++
				rest = append(rest, args[i])
			}
			continue
		}
		if positional == "" {
			positional = a
			continue
		}
		rest = append(rest, a)
	}
	return positional, rest
}

// takesValue reports whether a flag name consumes the following argument.
// Booleans do not; everything else this command set defines does.
func takesValue(name string) bool {
	switch name {
	case "json", "dry-run", "verbose", "from-scratch", "help", "h", "no-embedder", "no-reranker", "reset":
		return false
	}
	return true
}

// bindEmbedderFlags registers the vector arm's overrides. Shared by every
// command that can touch it so the spelling cannot drift between them.
func bindEmbedderFlags(fs *flag.FlagSet) *embedderFlags {
	f := &embedderFlags{}
	fs.StringVar(&f.url, "embedder-url", "",
		"attach to an embedding server already running here instead of spawning one")
	fs.StringVar(&f.model, "embed-model", "",
		"which embedding model to use (default: whatever is installed)")
	fs.BoolVar(&f.off, "no-embedder", false,
		"run lexical-only even if a model is installed")
	return f
}

// bindRerankerFlags registers the cross-encoder's overrides, mirroring
// bindEmbedderFlags exactly — same three-way shape (attach / name / off) for
// the same reason: a second supervised child, resolved the same way.
func bindRerankerFlags(fs *flag.FlagSet) *rerankerFlags {
	f := &rerankerFlags{}
	fs.StringVar(&f.url, "reranker-url", "",
		"attach to a reranking server already running here instead of spawning one")
	fs.StringVar(&f.model, "rerank-model", "",
		"which reranker model to use (default: whatever is installed)")
	fs.BoolVar(&f.off, "no-reranker", false,
		"skip cross-encoder reranking even if a model is installed")
	return f
}

// quietLogger keeps the child's lifecycle chatter off a one-shot command's
// stdout, where it would land in the middle of the JSON a caller is parsing.
func quietLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelWarn}))
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
