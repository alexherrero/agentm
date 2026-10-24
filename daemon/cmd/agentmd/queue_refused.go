package main

import (
	"os"
	"path/filepath"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
)

// queueSplit is the filing queue with the cards the night cannot judge taken
// out (#793).
//
// A card the post-gates refused at the current pass is skipped free by the
// refusal gate every night until its text, the filing contract or the gate's
// wording changes. It awaits a first judgment it will never get, so counting it
// held the queue-age alert red for days with nothing for the night to do: one
// card, refused on 2026-09-30, kept it red for 3d18h. Those cards are counted
// apart, on their own line, and leave the population the alert reads. A card
// whose text or pass changes no longer matches its refusal and rejoins on the
// next status read.
type queueSplit struct {
	// Unfiled and Oldest are the judgeable queue; Since and OldestSince the part
	// of it captured at or after the baseline.
	Unfiled     int
	Oldest      time.Time
	Since       int
	OldestSince time.Time
	// Refused and OldestRefused are the cards that stand refused.
	Refused       int
	OldestRefused time.Time
}

// splitRefused reads the refusal record and the queue and splits one by the
// other. The second return is false when nothing stands refused against a
// card in the queue, and the index's own counts then stand as they are.
//
// Cheap in the ordinary case: the record is one small file, and a card's bytes
// are read only when it has a refusal row to compare against.
func splitRefused(cfg *config.Config, idx *index.Index, baseline time.Time) (queueSplit, bool) {
	refusals, err := enrich.NewRefusals(enrichStateDir(cfg))
	if err != nil {
		return queueSplit{}, false
	}
	rows := map[string]bool{}
	for _, row := range refusals.Standings() {
		rows[row.Rel] = true
	}
	if len(rows) == 0 {
		return queueSplit{}, false
	}
	waiting, err := idx.AwaitingList()
	if err != nil {
		return queueSplit{}, false
	}
	keyer := enrichFingerprint(cfg, nil)
	// The index compares capture dates at whole seconds (UnfiledSince), and
	// the persisted baseline carries nanoseconds; split at the same second.
	from := baseline.UTC().Truncate(time.Second)
	var out queueSplit
	for _, w := range waiting {
		if rows[w.Rel] && standsRefused(cfg, refusals, keyer, w.Rel) {
			out.Refused++
			out.OldestRefused = earlier(out.OldestRefused, w.Captured)
			continue
		}
		out.Unfiled++
		out.Oldest = earlier(out.Oldest, w.Captured)
		if !w.Captured.Before(from) {
			out.Since++
			out.OldestSince = earlier(out.OldestSince, w.Captured)
		}
	}
	return out, out.Refused > 0
}

// standsRefused asks the refusal gate's own question of a card on disk: would
// the night skip it as already refused?
func standsRefused(cfg *config.Config, refusals *enrich.Refusals, keyer *enrich.Fingerprint, rel string) bool {
	raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(rel)))
	if err != nil {
		return false
	}
	_, ok := refusals.Standing(rel, keyer.Key(string(raw)), enrich.GatesVersion)
	return ok
}

func earlier(a, b time.Time) time.Time {
	if a.IsZero() || b.Before(a) {
		return b
	}
	return a
}
