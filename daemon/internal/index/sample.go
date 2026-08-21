package index

import (
	"context"
	"fmt"
	"math/rand"
	"sort"
)

// UnfiledSample draws n notes at random from the whole unfiled queue.
//
// The cursor-ordered page is the right thing for a drain, which wants to make
// steady progress through the queue. It is the wrong thing for a *proof*: the
// front of this queue is overwhelmingly `_inbox/` mining stubs, so a batch taken
// from there would measure the pass against the least representative notes the
// corpus has and tell you nothing about how it handles real prose.
//
// Seeded, and the seed is returned rather than hidden. A sample nobody can
// reproduce cannot be compared against a later one, and comparing two batches is
// the only way the pre-registered thresholds ever get replaced by measured ones.
func (x *Index) UnfiledSample(_ context.Context, n int, seed int64) ([]string, error) {
	if n < 1 {
		return nil, nil
	}
	x.mu.Lock()
	defer x.mu.Unlock()

	rows, err := x.db.Query(`
		SELECT path FROM docmeta
		WHERE status IN (`+unfiledPlaceholders+`)
		ORDER BY path`, unfiledArgs...)
	if err != nil {
		return nil, fmt.Errorf("listing unfiled notes: %w", err)
	}
	defer rows.Close()

	// The whole queue, then a sample from it. 8,407 paths is a few hundred
	// kilobytes, and `ORDER BY RANDOM()` would be both slower and unseedable —
	// SQLite's RANDOM() takes no seed, so the sample could never be repeated.
	var all []string
	for rows.Next() {
		var p string
		if err := rows.Scan(&p); err != nil {
			return nil, err
		}
		all = append(all, p)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(all) <= n {
		return all, nil
	}

	rng := rand.New(rand.NewSource(seed))
	idx := rng.Perm(len(all))[:n]
	sort.Ints(idx)
	out := make([]string, 0, n)
	for _, i := range idx {
		out = append(out, all[i])
	}
	return out, nil
}
