// Package config resolves the daemon's runtime settings, of which exactly one is
// load-bearing enough to have its own doctrine: the vault path.
//
// Vault paths are resolved, never recalled. An absolute path like
// /Users/<name>/Library/CloudStorage/GoogleDrive-<id>/…/Obsidian/Agent encodes a
// username, a drive ID, and a mount point, all of which change across
// installations and over time — and a stale path is worse than a missing one,
// because it reads as valid. So the daemon reads it from the kernel config at
// every start and reports which source it came from.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// SchemaVersion is stamped into the index. A mismatch rebuilds rather than
// querying through a schema that is not there.
const SchemaVersion = "1"

// DefaultPort is the port the retired FastMCP daemon used. The real daemon takes
// the name and the port when that one is retired; `agentmd retire` is what makes
// it free, and serve refuses to guess around a collision.
const DefaultPort = 7821

// Config is the resolved, validated runtime configuration.
type Config struct {
	// VaultPath is the absolute vault root. Resolved, never a literal.
	VaultPath string
	// VaultSource records how VaultPath was found, so a surprising path is
	// diagnosable without re-deriving the resolution order by hand.
	VaultSource string

	// IndexPath is the single FTS5 file. It lives outside the vault: it is a
	// cache, and a database on a synced path is a known corruption pattern.
	IndexPath string

	Port int

	// Spaces maps a logical space name to a vault-relative directory. This is
	// the seam the later Agent/memory + Agent/desk migration turns on — a config
	// change, not a rewrite. Defaults describe the CURRENT layout.
	Spaces map[string]string

	// Shard is "date" (memory/<YYYY>/<MM>/<slug>.md) or "flat".
	Shard string

	// PhonePaths are vault-relative prefixes whose changes are attributed to the
	// phone. Empty until Syncthing lands; the mechanism ships ready for it.
	PhonePaths []string

	// ReconcileEvery is how often the daemon re-walks the vault to catch changes
	// the filesystem notifier missed. Not belt-and-braces: fsnotify is
	// unreliable on network and cloud-sync mounts, which is where this vault
	// currently lives.
	ReconcileEvery time.Duration

	// ConfigPath is the file the above was read from, for reporting.
	ConfigPath string
}

// Options are the command-line overrides, applied over the config file.
type Options struct {
	ConfigPath     string
	VaultPath      string
	IndexPath      string
	Port           int
	PortSet        bool
	ReconcileEvery time.Duration
}

// The six types that ship at cutover. Everything else retired with the machinery
// that produced it. A type is added when a query class needs to rank by it.
var Types = []string{"preference", "workflow", "idea", "fix", "convention", "reference"}

// DefaultType is what an unlabelled capture lands as. Capture is never blocked
// on a caller getting the taxonomy right — re-typing is a frontmatter edit with
// no file move, so a wrong default is cheap and a refused capture is not.
const DefaultType = "preference"

func defaultSpaces() map[string]string {
	// The current vault layout. After the migration this becomes
	// {"memory": "Agent/memory", "desk": "Agent/desk"} in the config file.
	return map[string]string{
		"memory":   "personal",
		"projects": "projects",
	}
}

// Load resolves configuration from, in descending precedence: explicit flags,
// $MEMORY_VAULT_PATH (the documented per-invocation escape hatch), and the
// kernel config file's plugins.obsidian-vault.vault_path.
func Load(opts Options) (*Config, error) {
	cfgPath := opts.ConfigPath
	if cfgPath == "" {
		cfgPath = DefaultConfigPath()
	}

	raw := map[string]any{}
	readErr := error(nil)
	if blob, err := os.ReadFile(cfgPath); err == nil {
		if err := json.Unmarshal(blob, &raw); err != nil {
			return nil, fmt.Errorf("config %s is not valid JSON: %w", cfgPath, err)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		readErr = err
	}

	c := &Config{
		ConfigPath:     cfgPath,
		Spaces:         defaultSpaces(),
		Shard:          "date",
		Port:           DefaultPort,
		ReconcileEvery: 5 * time.Minute,
	}

	// --- the vault path -----------------------------------------------------
	switch {
	case opts.VaultPath != "":
		c.VaultPath, c.VaultSource = opts.VaultPath, "--vault flag"
	case os.Getenv("MEMORY_VAULT_PATH") != "":
		c.VaultPath, c.VaultSource = os.Getenv("MEMORY_VAULT_PATH"), "$MEMORY_VAULT_PATH"
	default:
		if v := strVal(raw, "plugins.obsidian-vault.vault_path"); v != "" {
			c.VaultPath = v
			c.VaultSource = cfgPath + " (plugins.obsidian-vault.vault_path)"
		}
	}
	if c.VaultPath == "" {
		hint := ""
		if readErr != nil {
			hint = fmt.Sprintf(" (reading %s failed: %v)", cfgPath, readErr)
		}
		return nil, fmt.Errorf(
			"no vault path: set plugins.obsidian-vault.vault_path in %s, "+
				"or pass --vault, or set $MEMORY_VAULT_PATH%s", cfgPath, hint)
	}
	abs, err := filepath.Abs(expandHome(c.VaultPath))
	if err != nil {
		return nil, fmt.Errorf("vault path %q: %w", c.VaultPath, err)
	}
	c.VaultPath = abs
	info, err := os.Stat(c.VaultPath)
	if err != nil {
		return nil, fmt.Errorf("vault path %s (from %s): %w", c.VaultPath, c.VaultSource, err)
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("vault path %s is not a directory", c.VaultPath)
	}

	// --- everything else ----------------------------------------------------
	if v := strVal(raw, "daemon.index_path"); v != "" {
		c.IndexPath = v
	}
	if opts.IndexPath != "" {
		c.IndexPath = opts.IndexPath
	}
	if c.IndexPath == "" {
		c.IndexPath = DefaultIndexPath()
	}
	if c.IndexPath, err = filepath.Abs(expandHome(c.IndexPath)); err != nil {
		return nil, fmt.Errorf("index path: %w", err)
	}
	if within(c.IndexPath, c.VaultPath) {
		return nil, fmt.Errorf(
			"index path %s is inside the vault; the index is a deletable cache and "+
				"must not live in the tree it indexes (or in git, or on a synced mount)",
			c.IndexPath)
	}

	if n, ok := intVal(raw, "daemon.port"); ok {
		c.Port = n
	}
	if opts.PortSet {
		c.Port = opts.Port
	}
	if c.Port < 0 || c.Port > 65535 {
		return nil, fmt.Errorf("port %d out of range", c.Port)
	}

	if m, ok := raw["daemon.spaces"].(map[string]any); ok && len(m) > 0 {
		c.Spaces = map[string]string{}
		for k, v := range m {
			if s, ok := v.(string); ok {
				c.Spaces[k] = strings.Trim(filepath.ToSlash(s), "/")
			}
		}
	}
	if _, ok := c.Spaces["memory"]; !ok {
		return nil, errors.New(`daemon.spaces must define "memory" — it is where capture writes`)
	}

	if v := strVal(raw, "daemon.shard"); v != "" {
		c.Shard = v
	}
	if c.Shard != "date" && c.Shard != "flat" {
		return nil, fmt.Errorf(`daemon.shard is %q; expected "date" or "flat"`, c.Shard)
	}

	if arr, ok := raw["daemon.phone_paths"].([]any); ok {
		for _, v := range arr {
			if s, ok := v.(string); ok && s != "" {
				c.PhonePaths = append(c.PhonePaths, strings.Trim(filepath.ToSlash(s), "/"))
			}
		}
	}

	if v := strVal(raw, "daemon.reconcile_every"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return nil, fmt.Errorf("daemon.reconcile_every %q: %w", v, err)
		}
		c.ReconcileEvery = d
	}
	if opts.ReconcileEvery > 0 {
		c.ReconcileEvery = opts.ReconcileEvery
	}

	return c, nil
}

// SpaceDir returns the vault-relative directory for a space name.
func (c *Config) SpaceDir(space string) (string, error) {
	if space == "" {
		space = "memory"
	}
	dir, ok := c.Spaces[space]
	if !ok {
		names := make([]string, 0, len(c.Spaces))
		for k := range c.Spaces {
			names = append(names, k)
		}
		return "", fmt.Errorf("unknown space %q; configured: %s", space, strings.Join(names, ", "))
	}
	return dir, nil
}

// IsPhonePath reports whether a vault-relative path is attributed to the phone.
func (c *Config) IsPhonePath(rel string) bool {
	rel = filepath.ToSlash(rel)
	for _, p := range c.PhonePaths {
		if rel == p || strings.HasPrefix(rel, p+"/") {
			return true
		}
	}
	return false
}

// DefaultConfigPath is the kernel config the installer writes.
func DefaultConfigPath() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ".agentm-config.json"
	}
	return filepath.Join(home, ".claude", ".agentm-config.json")
}

// DefaultIndexPath keeps the index in per-user state, outside the vault.
func DefaultIndexPath() string {
	if v := os.Getenv("AGENTM_STATE_DIR"); v != "" {
		return filepath.Join(v, "index.db")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "agentm-index.db"
	}
	if runtime.GOOS == "darwin" {
		return filepath.Join(home, "Library", "Application Support", "agentm", "index.db")
	}
	if v := os.Getenv("XDG_STATE_HOME"); v != "" {
		return filepath.Join(v, "agentm", "index.db")
	}
	return filepath.Join(home, ".local", "state", "agentm", "index.db")
}

func expandHome(p string) string {
	if p == "~" || strings.HasPrefix(p, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, strings.TrimPrefix(strings.TrimPrefix(p, "~"), "/"))
		}
	}
	return p
}

func within(path, dir string) bool {
	rel, err := filepath.Rel(dir, path)
	if err != nil {
		return false
	}
	return rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

func strVal(raw map[string]any, key string) string {
	if v, ok := raw[key].(string); ok {
		return v
	}
	return ""
}

func intVal(raw map[string]any, key string) (int, bool) {
	switch v := raw[key].(type) {
	case float64:
		return int(v), true
	case int:
		return v, true
	}
	return 0, false
}
