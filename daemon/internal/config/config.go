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

	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// SchemaVersion is stamped into the index. A mismatch rebuilds rather than
// querying through a schema that is not there.
//
// 2 adds the note's frontmatter status as its own column. The filing queue is a
// query rather than a folder, and asking it through the rank-penalty flags —
// which lump `unfiled` together with `superseded` and `expired` — counted
// retired notes as waiting ones.
//
// 3 adds the `embeddings` table for the vector arm. Bumping the version discards
// the file, which costs a lexical rebuild of seconds and a re-embed of minutes —
// the re-embed being the expensive half, and the reason a future schema change
// that does not touch vectors is worth making additively rather than by bump.
//
// 4 lets `embeddings` hold several rows per note, keyed by (doc_id, chunk_idx)
// instead of doc_id alone, so a note longer than the embedder's window is
// covered by several chunk vectors instead of one truncated from its head. Same
// reason for the bump as 3: the table is a cache, so discarding and re-embedding
// is the correct migration for a shape change, not a schema patch.
const SchemaVersion = "4"

// DefaultPort is the port the retired FastMCP daemon used. The real daemon takes
// the name and the port when that one is retired; `agentmd retire` is what makes
// it free, and serve refuses to guess around a collision.
const DefaultPort = 7821

// Config is the resolved, validated runtime configuration.
type Config struct {
	// Rules holds the filing contract, read at runtime from the vault's own
	// standards/storage-rules.md and re-readable without a restart. Ask it with
	// Get(); a non-nil error means filing halts and a caller-supplied type cannot
	// be validated, while everything that does not read the taxonomy is
	// unaffected.
	Rules *rules.Holder

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

	// --- the loud queue -----------------------------------------------------

	// UnfiledAgeRed is how old the oldest unfiled item may get before the queue
	// is red. Age-dominant by design: under a standing daily ingest fifty fresh
	// unfiled items every morning is an ordinary Tuesday, while a three-day-old
	// oldest item means filing stalled.
	UnfiledAgeRed time.Duration

	// UnfiledCountRed is the size backstop. Deliberately far above any ordinary
	// day, because size is the weaker signal — it exists to catch the one case
	// age cannot see, a runaway producer whose whole queue is fresh.
	UnfiledCountRed int

	// QueueBaseline separates the backlog the daemon inherited from the queue it
	// is responsible for. Zero means the daemon records its own on first run.
	QueueBaseline time.Time

	// HealthEvery is how often the daemon evaluates its own thresholds.
	HealthEvery time.Duration

	// ProbeEvery is how often the self-probe runs. Daily, in the sense the other
	// notification channels mean it: at most once per interval, checked at
	// startup and on a ticker, rather than pinned to a wall-clock hour.
	ProbeEvery time.Duration

	// ProbeBudget is how long the whole round trip may take before the probe
	// counts as failed. Capture indexes inline, so the honest expectation is
	// milliseconds; the budget is generous because it is a stall detector rather
	// than a benchmark.
	ProbeBudget time.Duration

	// StateDir holds the daemon's small state files — the last probe result and
	// the alert anti-fatigue record. Beside the index, and a deletable cache in
	// the same sense: losing it costs one duplicate alert and one probe.
	StateDir string

	// Email is the alert channel, read from the same plugins.autonomy.* keys the
	// existing digest email uses. Zero value means unconfigured, which is a
	// silent skip rather than an error.
	Email EmailConfig

	// --- the vector arm -----------------------------------------------------

	// EmbedEnabled is the off switch. True by default: an install with no model
	// on disk already runs lexical-only, so the switch exists for the case where
	// the weights are present and the operator wants them left alone.
	EmbedEnabled bool

	// EmbedModel names the weights. Empty means "discover whatever is installed",
	// which is the ordinary case — install.sh puts exactly one there.
	EmbedModel string

	// EmbedderURL attaches to an embedding server someone else is running instead
	// of spawning a child. The measurement harness uses it to score 84 questions
	// against one warm model rather than 84 model loads.
	EmbedderURL string

	// EmbedScope is the set of vault-relative directories the vector arm covers.
	// See defaultEmbedScope for why it is three names and not the whole tree.
	EmbedScope []string

	// EnrichEnabled turns the enrichment pass on.
	//
	// Off in the shipped configuration, and for a different reason than the two
	// ranking flags beside it: those are off because a measurement said so, and
	// this one is off because it *spends*. The eager trigger fires on real
	// captures, so switching it on with a binary update would start making model
	// calls on the operator's machine without anyone deciding to.
	EnrichEnabled bool

	// EnrichModel is the model name enrichment passes to `claude -p`. A name,
	// not a tier — tier qualification is earned by sampled audit against the
	// strong tier, which is its own mechanism and not this pass's job.
	EnrichModel string

	// CheapModel is the budget-tier candidate the qualifying audit compares
	// against EnrichModel.
	//
	// A name here turns nothing on. A job runs on this model only if a sampled
	// audit measured it agreeing with the strong tier at the pre-registered bar,
	// and three jobs are pinned strong whatever it says. Unset simply means no
	// comparison has ever been possible.
	CheapModel string

	// EnrichSampleRate is one-in-n for the sampled completeness half. The
	// faithfulness half is per note and does not consult it.
	EnrichSampleRate int

	// EnrichConcurrency bounds simultaneous eager runs. A capture burst would
	// otherwise start one subprocess per note, and the type-collapse migration
	// rewrote 9,899 notes in an afternoon.
	EnrichConcurrency int

	// AltitudeEnabled turns the `artifact` dampening on.
	//
	// Off by default for the length of the transition, and the reason is a
	// defect this flag exists to hold shut. Capture writes `altitude: artifact`
	// on every new note, and the ranker dampens only a note that says so — which
	// was the right call for the clamp reason, but means a labelled note is
	// dampened while an unlabelled one is not. On a corpus where 15,479 notes
	// predate the field and every new capture carries it, that penalizes *having
	// been captured recently* rather than *being an artifact*, and it grows
	// quietly with every capture.
	//
	// The design's model is a labelled corpus, where `canonical` is earned and
	// everything else is an artifact. Enrichment is what labels it. Until that
	// has run over the corpus, dampening the labelled minority is backwards, so
	// it stays off and turns on when the corpus is uniform and the eval can see
	// the difference.
	AltitudeEnabled bool

	// DecayEnabled turns age-based demotion on. It is off by default, and the
	// default is a measurement rather than caution.
	//
	// Scored against the frozen gold set with decay as the only variable, on one
	// corpus at one moment: R@5 fell from 0.781 to 0.750, two questions flipped to
	// a miss and none to a hit. The mechanism is not at fault — the corpus is too
	// young for it. 89% of notes are under a month old and the oldest in the
	// memory layer is 93 days, against a first band at 182, so exactly five notes
	// of 15,824 cross any band at all. Two of those five are the expected answers
	// to a temporal question ("when did I switch to Claude"), where being old is
	// what makes a note the right answer.
	//
	// Turn it on when the corpus has age spread and the eval says it helps. The
	// curve, the anchors and the exemptions are all here and tested; what is
	// missing is a corpus that can exercise them.
	DecayEnabled bool

	// MemoryRoot is the vault-relative directory the memory itself lives under —
	// `Agent` in the shipped layout, and empty in a flat one.
	//
	// Kept rather than derived-and-discarded, because more than one thing needs
	// it and the vault root is not a usable substitute for any of them. The
	// recall-access sidecar sits at `<vault>/<memory_root>/.lifecycle.json`, and
	// a reader handed the vault root instead finds nothing, reports no error, and
	// silently ranks the whole corpus off its fallback anchor.
	MemoryRoot string

	// --- the reranker ---------------------------------------------------

	// RerankEnabled is the off switch, mirroring EmbedEnabled: true by
	// default, since an install with no reranker weights on disk already
	// runs without one, so the switch exists for the case where the weights
	// are present and the operator wants them left alone.
	RerankEnabled bool

	// RerankModel names the reranker weights. Empty means "discover whatever
	// is installed" — the ordinary case once install.sh grows the reranker
	// leg.
	RerankModel string

	// RerankerURL attaches to a rerank server someone else is running
	// instead of spawning a child. Same reason EmbedderURL exists: the
	// measurement harness scores 84 questions against one warm model rather
	// than paying a model load per query.
	RerankerURL string

	// ConfigPath is the file the above was read from, for reporting.
	ConfigPath string
}

// EmailConfig is the operator's own relay, read from plugins.autonomy.*.
//
// The keys are shared with scripts/health/session_email.py on purpose. There is
// one place the operator configures where mail goes, and a second dialect of it
// would be a second thing to get wrong.
type EmailConfig struct {
	To      string
	SMTPURL string
	From    string
}

// Configured reports whether mail can actually be sent. Both the recipient and
// the relay are required; either one absent means the channel is unconfigured,
// matching session_email.py's contract exactly.
func (e EmailConfig) Configured() bool { return e.To != "" && e.SMTPURL != "" }

// Sender is the address mail goes out as, falling back to the recipient — some
// relays require a domain-verified From distinct from the auth username, and
// mail-to-self is the sane default when none is set.
func (e EmailConfig) Sender() string {
	if e.From != "" {
		return e.From
	}
	return e.To
}

// Options are the command-line overrides, applied over the config file.
type Options struct {
	ConfigPath     string
	VaultPath      string
	IndexPath      string
	StateDir       string
	Port           int
	PortSet        bool
	ReconcileEvery time.Duration
	ProbeEvery     time.Duration
}

// loadRules attaches the filing contract to a config.
//
// A parse failure is recorded, not returned. The blast radius of a typo in the
// rules file has to match what the typo actually endangers: search does not read
// the taxonomy and keeps working, ambient capture does not supply a type and
// keeps working, and only the two things that genuinely depend on the contract —
// validating a caller-supplied type, and filing — stop. A daemon that refused to
// start over a misplaced colon would take the whole memory down to protect one
// field.
func (c *Config) loadRules(now time.Time) {
	c.Rules = rules.NewHolder(c.VaultPath, now)
	c.applyDampenedSpaces()
}

// applyDampenedSpaces pushes the contract's space list into the classifier.
//
// Done here rather than threaded through note.Parse because four call sites
// parse notes and one of them is the self-probe, which has no configuration.
// When the contract will not parse, nothing is dampened: that is the safe
// direction, since the failure mode of dampening too little is a leak the
// operator can see, and of dampening too much is an answer that never arrives.
func (c *Config) applyDampenedSpaces() {
	if loaded, err := c.Rules.Get(); err == nil {
		note.SetDampenedSpaces(loaded.DampenedSpaces)
		note.SetDecayExemptSpaces(loaded.ContractExemptSpaces)
		note.SetAltitudeDampening(c.AltitudeEnabled)
	} else {
		note.SetDampenedSpaces(nil)
		note.SetDecayExemptSpaces(nil)
		note.SetAltitudeDampening(c.AltitudeEnabled)
	}
}

// defaultEmbedScope is the part of the vault the vector arm covers when the
// config does not say.
//
// It is derived from `memory_root` rather than written as a literal, for the same
// reason no vault path is ever a constant here: the root has moved twice, and a
// hardcoded `Agent/memory` would resolve to nothing on the next move — silently,
// because an empty scope embeds zero notes and a vector arm with no vectors looks
// exactly like one that is merely cold.
//
// The three names are the spaces whose notes embed whole. `memory` is atomic by
// capture doctrine. `desk` and `external` are not atomic and are included anyway,
// because the gold set's answers live there — 65 of 90 expected paths, against 25
// in `memory` — so a scope excluding them measures a vector arm that cannot reach
// most of what it is being scored on. What is deliberately absent is `_meta`,
// whose notes run to 200,000 tokens and would be embedded as a single centroid;
// that is the case a chunking policy exists for, and there is no chunking policy.
func defaultEmbedScope(memoryRoot string) []string {
	root := strings.Trim(filepath.ToSlash(strings.TrimSpace(memoryRoot)), "/")
	names := []string{"memory", "desk", "external"}
	out := make([]string, 0, len(names))
	for _, n := range names {
		if root == "" {
			out = append(out, n)
			continue
		}
		out = append(out, root+"/"+n)
	}
	return out
}

// defaultSpaces is where capture writes when the config does not say.
//
// Derived from `memory_root` for the reason defaultEmbedScope gives above, and
// for a sharper one this function learned the hard way. It used to return
// {"memory": "personal", "projects": "projects"} — the pre-migration layout,
// written as a literal. The shipped contract declares `Personal` model-exempt,
// which is the absolute privacy boundary: enrichment skips it, dreaming never
// sends it to a model, no batch call includes it. So on a vault with no
// `daemon.spaces` configured, every memory captured landed in the one directory
// no background pass may read — and looked fine doing it, because the note is
// written, the index finds it, and only the enrichment that never comes is
// missing.
//
// A literal here cannot be right for long. The root has moved twice, and both
// times this default kept pointing at where the vault used to be.
func defaultSpaces(memoryRoot string) map[string]string {
	root := strings.Trim(filepath.ToSlash(strings.TrimSpace(memoryRoot)), "/")
	under := func(rest string) string {
		if root == "" {
			return rest
		}
		return root + "/" + rest
	}
	return map[string]string{
		"memory":   under("memory"),
		"projects": under("desk/projects"),
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
		ConfigPath: cfgPath,
		// Spaces is deliberately left empty here: its default derives from
		// `memory_root`, which is not resolved until further down.
		Shard:           "date",
		EmbedEnabled:    true,
		Port:            DefaultPort,
		ReconcileEvery:  5 * time.Minute,
		UnfiledAgeRed:   DefaultUnfiledAgeRed,
		UnfiledCountRed: DefaultUnfiledCountRed,
		HealthEvery:     15 * time.Minute,
		ProbeEvery:      24 * time.Hour,
		ProbeBudget:     10 * time.Second,
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
		if _, ok := c.Spaces["memory"]; !ok {
			return nil, errors.New(`daemon.spaces must define "memory" — it is where capture writes`)
		}
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

	if b, ok := raw["daemon.decay_enabled"].(bool); ok {
		c.DecayEnabled = b
	}
	if b, ok := raw["daemon.altitude_enabled"].(bool); ok {
		c.AltitudeEnabled = b
	}
	if b, ok := raw["daemon.enrich_enabled"].(bool); ok {
		c.EnrichEnabled = b
	}
	if s := strVal(raw, "daemon.enrich_model"); s != "" {
		c.EnrichModel = s
	}
	if s := strVal(raw, "daemon.cheap_model"); s != "" {
		c.CheapModel = s
	}
	if f, ok := raw["daemon.enrich_concurrency"].(float64); ok && f >= 1 {
		c.EnrichConcurrency = int(f)
	}
	if f, ok := raw["daemon.enrich_sample_rate"].(float64); ok && f >= 0 {
		c.EnrichSampleRate = int(f)
	}
	if c.EnrichSampleRate == 0 {
		// One in twenty. Enough to move a scorecard number over a batch, few
		// enough that the sampled half is a rounding error against the per-note
		// faithfulness call it rides alongside.
		c.EnrichSampleRate = 20
	}
	if c.EnrichConcurrency == 0 {
		// Two rather than one so a second capture during a slow call is not
		// automatically deferred, and rather than many because each is a
		// subprocess with a model behind it.
		c.EnrichConcurrency = 2
	}

	c.MemoryRoot = strings.Trim(
		filepath.ToSlash(strings.TrimSpace(strVal(raw, "plugins.obsidian-vault.memory_root"))), "/")

	// Now that the root is known, a config that named no spaces gets the ones
	// derived from it.
	if len(c.Spaces) == 0 {
		c.Spaces = defaultSpaces(c.MemoryRoot)
	}

	// --- the vector arm -----------------------------------------------------
	c.EmbedModel = strVal(raw, "daemon.embed_model")
	c.EmbedderURL = strVal(raw, "daemon.embedder_url")
	if b, ok := raw["daemon.embed_enabled"].(bool); ok {
		c.EmbedEnabled = b
	}
	if arr, ok := raw["daemon.embed_scope"].([]any); ok {
		for _, v := range arr {
			if s, ok := v.(string); ok && s != "" {
				c.EmbedScope = append(c.EmbedScope, strings.Trim(filepath.ToSlash(s), "/"))
			}
		}
	}
	if len(c.EmbedScope) == 0 {
		c.EmbedScope = defaultEmbedScope(c.MemoryRoot)
	}

	// --- the reranker ---------------------------------------------------
	c.RerankModel = strVal(raw, "daemon.rerank_model")
	c.RerankerURL = strVal(raw, "daemon.reranker_url")
	c.RerankEnabled = true
	if b, ok := raw["daemon.rerank_enabled"].(bool); ok {
		c.RerankEnabled = b
	}

	for _, d := range []struct {
		key    string
		target *time.Duration
	}{
		{"daemon.reconcile_every", &c.ReconcileEvery},
		{"daemon.unfiled_age_red", &c.UnfiledAgeRed},
		{"daemon.health_every", &c.HealthEvery},
		{"daemon.probe_every", &c.ProbeEvery},
		{"daemon.probe_budget", &c.ProbeBudget},
	} {
		v := strVal(raw, d.key)
		if v == "" {
			continue
		}
		parsed, err := time.ParseDuration(v)
		if err != nil {
			return nil, fmt.Errorf("%s %q: %w", d.key, v, err)
		}
		if parsed <= 0 {
			return nil, fmt.Errorf("%s %q must be positive", d.key, v)
		}
		*d.target = parsed
	}
	if opts.ReconcileEvery > 0 {
		c.ReconcileEvery = opts.ReconcileEvery
	}
	if v := strVal(raw, "daemon.queue_baseline"); v != "" {
		parsed, err := parseDate(v)
		if err != nil {
			return nil, fmt.Errorf("daemon.queue_baseline %q: %w", v, err)
		}
		c.QueueBaseline = parsed
	}
	if n, ok := intVal(raw, "daemon.unfiled_count_red"); ok {
		if n <= 0 {
			return nil, fmt.Errorf("daemon.unfiled_count_red %d must be positive", n)
		}
		c.UnfiledCountRed = n
	}
	if opts.ProbeEvery > 0 {
		c.ProbeEvery = opts.ProbeEvery
	}

	c.StateDir = filepath.Dir(c.IndexPath)
	if opts.StateDir != "" {
		if c.StateDir, err = filepath.Abs(expandHome(opts.StateDir)); err != nil {
			return nil, fmt.Errorf("state dir: %w", err)
		}
	}

	c.Email = EmailConfig{
		To:      strings.TrimSpace(strVal(raw, "plugins.autonomy.email_to")),
		SMTPURL: strings.TrimSpace(strVal(raw, "plugins.autonomy.email_smtp_url")),
		From:    strings.TrimSpace(strVal(raw, "plugins.autonomy.email_from")),
	}

	c.loadRules(time.Now())
	if err := c.checkMemorySpaceIsReadable(); err != nil {
		return nil, err
	}

	return c, nil
}

// checkMemorySpaceIsReadable refuses a `memory` space the contract says no
// background pass may read.
//
// Refusing rather than warning, because this is the same class of thing as
// `daemon.spaces` naming no memory space at all, which already refuses: a
// configuration that cannot do the job it is for. The difference is only that
// this one still appears to work. Capture writes, the index finds it, search
// returns it, and enrichment, dreaming and every rollup skip it forever without
// a word — which is worse than a daemon that will not start, because a daemon
// that will not start gets fixed today.
//
// Silent when the contract will not parse. That case belongs to
// `health.Contract`, which reports it, and guessing at exemptions from a file
// that would not load would take the whole memory down over a misplaced colon —
// the exact trade `loadRules` refuses to make.
func (c *Config) checkMemorySpaceIsReadable() error {
	// A holder that could not parse the contract returns nil rules, which is the
	// silent case this check wants: see the doc comment above.
	loaded, _ := c.Rules.Get()
	if loaded == nil {
		return nil
	}
	dir := c.Spaces["memory"]
	if dir == "" {
		return nil
	}
	if !loaded.MayReadWithModel(dir) {
		return fmt.Errorf("daemon.spaces names %q as the memory space, and the "+
			"filing contract lists it under model_exempt_spaces — every memory "+
			"captured there would be invisible to enrichment, dreaming and every "+
			"rollup, silently. Point `memory` somewhere readable, or drop that "+
			"space from model_exempt_spaces in %s", dir, loaded.Source)
	}
	return nil
}

// Threshold defaults, stated as named constants because they are the numbers the
// operator gets paged by and they carry an argument.
//
// The age is three days, from the memory design directly: fifty fresh unfiled
// items every morning is an ordinary Tuesday, and the oldest unfiled item being
// three days old means the pipeline stalled.
//
// The count is a backstop rather than a peer. At the design's own fifty-a-day it
// would take twenty dead days to reach a thousand, by which point the age
// threshold has been red for seventeen of them — so this fires first only in the
// one case age cannot see, which is a producer writing thousands of fresh items
// at once. Setting it near a normal day's volume would page about Tuesday, which
// is how a queue alert teaches its reader to ignore it.
const (
	DefaultUnfiledAgeRed   = 72 * time.Hour
	DefaultUnfiledCountRed = 1000
)

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

// parseDate reads the date forms a person would actually type into a config
// file. A bare date means midnight UTC.
func parseDate(s string) (time.Time, error) {
	for _, layout := range []string{time.RFC3339, "2006-01-02T15:04:05", "2006-01-02"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.UTC(), nil
		}
	}
	return time.Time{}, fmt.Errorf("not a date I can read; use YYYY-MM-DD or RFC3339")
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
