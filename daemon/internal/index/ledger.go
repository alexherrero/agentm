package index

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// The recall ledger, from the arm that had never written to it.
//
// One JSONL row per genuine recall, at `$AGENTM_RECALL_HISTORY` else
// `~/.cache/agentm/telemetry/recall-history.jsonl`. Until now every row in that
// file came from `recall.py`'s prompt-submit hook, so the file answered "how
// often does the prompt hook fire" and was read as "how often is this memory
// used" — which was the same number only because no other surface wrote.
//
// A recall over MCP or the CLI is a use of the memory by any reading of the
// word, and the `surface:` field is what keeps the two legible apart now that
// both are in one file.
//
// The query is hashed, never stored. That is this ledger's standing contract
// and it is the same hash the Python side writes: the file is a count of what
// was reached, not a log of what was asked.

// recallRow is the row shape, matching `recall_counter.record_recall`'s. Only
// the fields this arm can honestly fill: `hits` is the Python arm's per-slug
// ranking evidence, and inventing an empty one here would say "recorded, no
// evidence" where the truth is "not that arm".
type recallRow struct {
	TS        string   `json:"ts"`
	QueryHash string   `json:"query_hash"`
	HitSlugs  []string `json:"hit_slugs"`
	HitCount  int      `json:"hit_count"`
	Surface   string   `json:"surface"`
}

// recallHistoryPath mirrors `dreaming.RecallHistoryPath` and
// `recall_counter.default_history_path`. Three copies of one path is a drift
// surface; it is duplicated here rather than imported because `dreaming`
// imports this package and the edge may not run the other way.
// `scripts/check-one-way-imports.py` is what holds that direction.
func recallHistoryPath() string {
	if v := strings.TrimSpace(os.Getenv("AGENTM_RECALL_HISTORY")); v != "" {
		return v
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".cache", "agentm", "telemetry", "recall-history.jsonl")
}

// hashQuery is `recall_counter._hash_query`: sha256 of the trimmed, lowercased
// text, first 16 hex characters.
func hashQuery(text string) string {
	sum := sha256.Sum256([]byte(strings.ToLower(strings.TrimSpace(text))))
	return hex.EncodeToString(sum[:])[:16]
}

// recordLedger appends one row for a recall this daemon served.
//
// Append-only here, deliberately: the Python side owns the retention sweep, and
// two processes read-modify-writing one file is how rows get lost. An append
// under `O_APPEND` of a line under the pipe-buffer size is atomic enough for
// the one writer-per-process case this is.
//
// Best-effort in the same sense as the clock: a search that refused to answer
// because a telemetry line could not be written would be worse than the missing
// line.
func (x *Index) recordLedger(surface, text string, rows []Result) {
	if !note.MovesClock(surface) {
		return
	}
	path := recallHistoryPath()
	if path == "" {
		return
	}
	slugs := make([]string, 0, len(rows))
	for _, r := range rows {
		base := filepath.Base(filepath.FromSlash(r.Path))
		slugs = append(slugs, strings.TrimSuffix(base, ".md"))
	}
	sort.Strings(slugs)
	blob, err := json.Marshal(recallRow{
		TS:        time.Now().UTC().Format(time.RFC3339),
		QueryHash: hashQuery(text),
		HitSlugs:  slugs,
		HitCount:  len(slugs),
		Surface:   note.NormalizeSurface(surface),
	})
	if err != nil {
		return
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.Write(append(blob, '\n'))
}
