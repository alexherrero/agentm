package vcs

import (
	"github.com/go-git/go-git/v5/config"
)

// The vault repository may carry `extensions.worktreeConfig = true`, and the
// daemon has to keep committing when it does.
//
// The Claude desktop app sets that key whenever it makes a git worktree, so it
// can give the new worktree its own `core.hooksPath`. Its own cleanup takes the
// key out again when the worktree goes, but a worktree removed by hand leaves
// it behind. That is what happened to the vault on 2026-10-02. go-git v5.19.2
// then refuses to open the repository, for two reasons of its own. It never
// reads `core.repositoryformatversion` back from the file, so it always checks
// extensions as if the format were 0. And its list of extensions allowed at
// format 0 spells the key `worktreeConfig` while it compares lowercased names,
// so the key never matches. Every open fails with "core.repositoryformatversion
// does not support extension: worktreeconfig", and the daemon came up with git
// degraded on its next restart: no commits, no undo, and the corpus-write gate
// refusing (#859).
//
// The extension changes one thing: git reads a per-worktree `config.worktree`
// file on top of the shared config. Nothing the daemon does depends on per-
// worktree config. It stages and commits in the main working tree, it runs no
// hooks, and the app writes its per-worktree settings for the new worktree,
// not the main one. So the storer hides this one key from the library, and
// every other extension still refuses: one that changes what the object store
// holds, such as `objectFormat`, is not the daemon's to ignore.
//
// The key is hidden on the way in and never removed on disk. It is the app's
// and the operator's, and CLI git, the backup job and the doctor's
// `vault-worktrees` row all still see it. The doctor reports it as a sign that
// a worktree got into the vault.

// worktreeConfigKey is the one extension the daemon reads past.
const worktreeConfigKey = "worktreeConfig"

// Config is the repository config as go-git should see it: the file as it
// stands, without `extensions.worktreeConfig`. Each call parses the file
// afresh, so trimming the returned object changes nothing else.
func (s *atomicIndexStorage) Config() (*config.Config, error) {
	cfg, err := s.Storage.Config()
	if err != nil || !hasWorktreeConfig(cfg) {
		return cfg, err
	}
	ext := cfg.Raw.Section("extensions")
	ext.RemoveOption(worktreeConfigKey)
	if len(ext.Options) == 0 && len(ext.Subsections) == 0 {
		cfg.Raw.RemoveSection("extensions")
	}
	return cfg, nil
}

// SetConfig writes a config back with the key restored, if the file on disk
// carries it. Nothing in the daemon writes the repository config today. This
// keeps a later caller that reads the trimmed config and writes it back from
// silently removing the operator's key.
func (s *atomicIndexStorage) SetConfig(cfg *config.Config) error {
	if cfg != nil && cfg.Raw != nil {
		if onDisk, err := s.Storage.Config(); err == nil && hasWorktreeConfig(onDisk) &&
			!(cfg.Raw.HasSection("extensions") && cfg.Raw.Section("extensions").HasOption(worktreeConfigKey)) {
			value := onDisk.Raw.Section("extensions").Option(worktreeConfigKey)
			cfg.Raw.Section("extensions").SetOption(worktreeConfigKey, value)
		}
	}
	return s.Storage.SetConfig(cfg)
}

func hasWorktreeConfig(cfg *config.Config) bool {
	return cfg != nil && cfg.Raw != nil && cfg.Raw.HasSection("extensions") &&
		cfg.Raw.Section("extensions").HasOption(worktreeConfigKey)
}
