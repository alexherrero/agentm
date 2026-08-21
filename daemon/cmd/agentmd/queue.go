package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/ledger"
)

// cmdQueue is how a stage asks what it owes, and how a human looks before
// deciding to run anything.
//
// The same seam `agentmd rules` and `agentmd ledger` opened. Discovery happens
// in whichever half of the system noticed the gap — the Python dreaming stages
// as often as the daemon — and enqueuing over this command means neither half
// has to carry its own writer for a table it does not own.
//
// It does not drain. A drain runs a stage's real work, which lives in the owner,
// and a command that drained would need to know how to do every owner's job.
// What it does is show the queue and let a discovery put something in it.
func cmdQueue(args []string) error {
	fs := newFlagSet("queue")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the answer as JSON")
	owner := fs.String("owner", "", "report on one owner (default: all of them)")
	limit := fs.Int("limit", 20, "how many items to print per owner (0 for all)")
	add := fs.String("enqueue", "",
		"record that --owner owes work on this target")
	reason := fs.String("reason", "", "why the work is owed, for --enqueue")
	revive := fs.Int64("revive", 0,
		"put a parked item back in the queue by id, with a clean attempt count")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd queue "+
			"[--owner NAME] [--enqueue TARGET --reason WHY] [--limit N] [--json]",
			extra[0])
	}

	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, cfg.DecayEnabled)
	if err != nil {
		return err
	}
	defer idx.Close()

	q, err := ledger.OpenQueue(idx.DB())
	if err != nil {
		return err
	}
	ctx := context.Background()

	if *revive > 0 {
		if err := q.Revive(ctx, *revive); err != nil {
			return err
		}
		fmt.Printf("item %d is back in the queue with no attempts against it\n", *revive)
		return nil
	}

	if *add != "" {
		if *owner == "" {
			return fmt.Errorf("--enqueue needs --owner: a work item that names no " +
				"owner is work nobody drains")
		}
		if *reason == "" {
			return fmt.Errorf("--enqueue needs --reason: an item whose reason is " +
				"blank is one nobody can act on when it surfaces in the digest")
		}
		if err := q.Enqueue(ctx, *owner, *add, *reason); err != nil {
			return err
		}
		fmt.Printf("%s owes work on %s — %s\n", *owner, *add, *reason)
		return nil
	}

	// Owners() lists anything with a row, parked included — an owner whose only
	// items are parked must still appear, or the work vanishes from the report
	// at exactly the moment somebody needs to see it.
	owners := []ledger.Stage{}
	if *owner != "" {
		owners = append(owners, *owner)
	} else if owners, err = q.Owners(ctx); err != nil {
		return err
	}

	type view struct {
		Owner     ledger.Stage      `json:"owner"`
		Depth     int               `json:"depth"`
		OldestAge string            `json:"oldest_age,omitempty"`
		Cursor    int64             `json:"cursor"`
		Items     []ledger.WorkItem `json:"items,omitempty"`
		// Parked is listed rather than counted. "Three items are parked" tells
		// nobody what stopped, and an item that failed three times and vanished
		// into a number is the silent failure the retry cap exists to prevent.
		Parked []ledger.WorkItem `json:"parked,omitempty"`
	}
	var views []view
	for _, o := range owners {
		depth, age, err := q.Depth(ctx, o)
		if err != nil {
			return err
		}
		cursor, err := q.Cursor(ctx, o)
		if err != nil {
			return err
		}
		items, err := q.Pending(ctx, o, *limit)
		if err != nil {
			return err
		}
		parked, err := q.Dead(ctx, o)
		if err != nil {
			return err
		}
		v := view{Owner: o, Depth: depth, Cursor: cursor, Items: items, Parked: parked}
		if age > 0 {
			v.OldestAge = age.Round(time.Second).String()
		}
		views = append(views, v)
	}

	if *asJSON {
		return json.NewEncoder(os.Stdout).Encode(views)
	}
	if len(views) == 0 {
		fmt.Println("no queue holds any work")
		return nil
	}
	for _, v := range views {
		fmt.Printf("%-10s depth %d", v.Owner, v.Depth)
		if v.OldestAge != "" {
			// Age before depth in the reader's attention, because the threshold
			// is on age: fifty fresh items on a Tuesday is a Tuesday, and one
			// item three days old means the drain has stalled.
			fmt.Printf(" · oldest %s", v.OldestAge)
		}
		fmt.Printf(" · cursor %d\n", v.Cursor)
		for _, it := range v.Items {
			fmt.Printf("  %-6d %s — %s", it.ID, it.Target, it.Reason)
			if it.Attempts > 0 {
				fmt.Printf(" (failed %dx: %s)", it.Attempts, it.LastErr)
			}
			fmt.Println()
		}
		if *limit > 0 && v.Depth > len(v.Items) {
			// The truncation says so. A list that stopped at twenty without
			// mentioning it reads as a queue of twenty.
			fmt.Printf("  … and %d more (pass --limit 0 for all)\n",
				v.Depth-len(v.Items))
		}
		for _, it := range v.Parked {
			fmt.Printf("  PARKED %-4d %s — failed %dx: %s\n",
				it.ID, it.Target, it.Attempts, it.LastErr)
		}
		if len(v.Parked) > 0 {
			fmt.Printf("  %d item(s) parked and no longer retried; revive one with "+
				"--revive <id> once the cause is fixed\n", len(v.Parked))
		}
	}
	return nil
}
