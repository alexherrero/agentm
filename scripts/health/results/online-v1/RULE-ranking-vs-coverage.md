# RULE — is it ranking, or is the note not there?

**Frozen 2026-08-31, before the probe ran.**

## The claim being tested

The arc concluded that recall's failure is **precision, not coverage** — the
right neighbourhood comes back, the specific note does not. Three things point
that way, and none of them is direct:

* the operator's own labels called 18 of 20 retrievals on-topic
* the judge called most of those same retrievals insufficient
* the `ignored` corner of the crossing was empty, so the model is not
  discarding what it gets

That is a plausible reading and it is the kind of plausible reading this arc
has already been wrong about twice. Before anything is built on it, it gets a
direct test.

## The test

For every turn the panel judged `insufficient`, re-run the same query against
the same vault at **k=50** instead of the shipped k=5, and ask the same judge
whether *that* set would have been sufficient.

The shipped path asks the daemon for k×2 and filters down to 5. So k=50 is
roughly a ten-fold deeper look at the same ranking, over the same corpus, with
the same query terms. Nothing about the retrieval changes except how far down
the list the answer is allowed to be.

## The bars, and what each outcome means

| flips to `sufficient` at k=50 | reading | what to build |
|---|---|---|
| **≥ 50%** | **ranking** — the note is there and findable, just below the cut | rerank, fusion weights, or a larger k |
| **20–50%** | mixed | split the remainder and diagnose again before building |
| **< 20%** | **coverage or vocabulary** — the note is absent, or the query cannot reach it | capture, aliasing, or query expansion |

## What I expect, written first

**I expect ranking, and I hold it loosely.** The indirect evidence is real, but
every step of it came from an instrument this arc has repeatedly found wrong,
and the operator's "on-topic" judgement was made under a reading of the
question they later revised.

The outcome I would find most informative is the middle band, because it would
mean both stories are partly true and the interesting question becomes which
turns fall where.

## What this cannot settle

A turn that stays insufficient at k=50 has two very different causes that this
probe does not separate: the note does not exist in the vault at all, or it
exists and the query's terms cannot reach it. Those need different fixes —
capture versus vocabulary — and telling them apart needs a search by content
rather than by the original query. If the answer lands under 20%, that split is
the next probe, not a conclusion to draw from this one.

The judge's own drift also applies: about five points, so a result within five
points of a bar is not a clear answer to it.
