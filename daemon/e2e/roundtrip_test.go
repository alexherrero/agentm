// Package e2e holds the only test that is allowed to mark this daemon done.
//
// Principle 3, from `wiki/designs/agentm-rescope-principles.md`: nothing is
// saved until a fresh session can ask and get it back. Every test in this file
// spawns the real binary as a real OS process, talks to it over the real MCP
// surface, kills it, spawns a *different* process, and asks sideways. Nothing
// here calls an internal Go function — the system this replaces had 2,964 green
// unit tests while returning zero results on every interactive prompt, and every
// one of them passed by reaching past the wiring that was broken.
//
// The queries below are deliberately not the notes' own words. A test that asks
// verbatim proves the index can echo, which is not the property under test.
package e2e

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	// Imported for the contract test below, and for a second reason worth stating:
	// this package otherwise talks to the daemon only as a built binary over HTTP,
	// so it would have no compile-time dependency on the code it is testing — and
	// `go test ./...` would serve cached e2e results after an internal/ change.
	// A suite that can be served from cache while the behaviour under it changed is
	// the same failure as a suite that never asked. These imports close that.
	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// TestMeasuredConstants pins the index's parameters to the literals in
// scripts/health/results/week1/RANK-PENALTY-NOTES.md.
//
// Hand-written values from the report, not recomputed from the implementation: a
// check that derives its expectation from the code it checks verifies only that
// they agree with each other.
func TestMeasuredConstants(t *testing.T) {
	if got := note.Weights[note.ClassFragment]; got != 0.30 {
		t.Errorf("fragment weight = %v, report specifies 0.30", got)
	}
	if got := note.Weights[note.ClassStatus]; got != 0.60 {
		t.Errorf("status weight = %v, report specifies 0.60", got)
	}
	if got := note.Weights[note.ClassStaging]; got != 0.30 {
		t.Errorf("staging weight = %v, report specifies 0.30", got)
	}
	// The status gate: a fragment-shaped note that filing promoted carries the
	// class but no weight. Its presence in this map at all would demote 1,288
	// notes, 229 of them in personal/preferences/.
	if w, ok := note.Weights[note.ClassFragmentPromoted]; ok {
		t.Errorf("fragment-promoted carries weight %v; the status gate requires it "+
			"to be absent so filing overrides the miner's fingerprint", w)
	}
	if note.Overfetch != 200 {
		t.Errorf("over-fetch = %d, report specifies ~200; re-ranking only k cannot "+
			"promote the note the fragments were hiding", note.Overfetch)
	}
	if got := note.Multiplier([]string{note.ClassFragment, note.ClassStatus}); got != 0.18 {
		t.Errorf("compounded fragment+status multiplier = %v, want 0.18 "+
			"(multiplicative, so classes compose without zeroing a score)", got)
	}
	if got := note.Multiplier([]string{note.ClassFragmentPromoted}); got != 1.0 {
		t.Errorf("promoted fragment multiplier = %v, want 1.0", got)
	}
	// The taxonomy now comes from the filing contract rather than a constant, so
	// this asks the shipped contract the same question it used to ask the
	// constant: six types ship, and the growth rule is what keeps it six.
	shipped, err := rules.Load("")
	if err != nil {
		t.Fatalf("the shipped filing contract does not parse: %v", err)
	}
	if len(shipped.MemoryTypes) != 6 {
		t.Errorf("%d types ship, the design collapses the taxonomy to 6", len(shipped.MemoryTypes))
	}
}

// ---------------------------------------------------------------------------
// THE test
// ---------------------------------------------------------------------------

// TestRoundTrip_FreshProcessAsksSideways is the definition of done.
//
// Capture a fact through the MCP surface. Kill the process that captured it.
// Start a new one. Ask for the fact in words the note does not contain. Get it
// back. If this test fails, the daemon does not work, regardless of what every
// other check in the repository says.
func TestRoundTrip_FreshProcessAsksSideways(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	// --- process A: capture -------------------------------------------------
	a := start(t, bin, env)
	res := a.capture(t, captureArgs{
		Title: "Edit, not Write, for existing files",
		Text: "Output tokens cost roughly five times what input costs, and " +
			"rewriting a whole file re-emits every unchanged line as output. " +
			"Reach for a targeted patch when the file already exists; reserve " +
			"a full rewrite for brand-new files.",
		Type:   "preference",
		Status: "active",
		Tags:   []string{"tokens", "tooling"},
		// `clobber` appears nowhere in the title or body. It is here to prove
		// the aliases column is indexed and searchable — if that column is
		// dropped or left out of the MATCH, the alias probe below returns zero.
		Aliases: []string{"prefer a targeted patch", "don't clobber a file to change one line"},
	})
	path := res.str(t, "path")
	if path == "" {
		t.Fatal("memory_capture returned no path")
	}
	onDisk := filepath.Join(env.vault, path)
	if _, err := os.Stat(onDisk); err != nil {
		t.Fatalf("capture reported %s but nothing is on disk: %v", path, err)
	}
	a.kill(t)

	// --- process B: a genuinely fresh process --------------------------------
	b := start(t, bin, env)
	defer b.kill(t)

	// Three sideways probes, the shape a driver actually issues: short, high
	// signal, none of them the note's title or a verbatim span of its body.
	// FTS5 is an implicit AND across terms, so this is also a fair test of
	// whether the shipped query semantics can serve a paraphrased question.
	for _, q := range []string{
		"rewrite whole file",
		"output tokens cost",
		"targeted patch",
		"clobber", // alias-only vocabulary
	} {
		hits := b.search(t, q, 5)
		if !hits.contains(path) {
			t.Errorf("query %q did not return %s\n  got: %s", q, path, hits.summary())
		}
	}
}

// TestRoundTrip_IndexIsADeletableCache is principle 2 as an executable claim:
// delete the index, lose nothing. The files are truth.
func TestRoundTrip_IndexIsADeletableCache(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	a := start(t, bin, env)
	res := a.capture(t, captureArgs{
		Title:  "Wake on the check-suite, not on the push",
		Text:   "The full matrix triggers on pull_request rather than on a push to main, so a green push proves nothing about the other two operating systems.",
		Type:   "workflow",
		Status: "active",
	})
	path := res.str(t, "path")
	a.kill(t)

	if err := os.Remove(env.index); err != nil {
		t.Fatalf("removing the index: %v", err)
	}
	// SQLite's sidecar files are part of the cache and go with it.
	for _, suffix := range []string{"-wal", "-shm"} {
		_ = os.Remove(env.index + suffix)
	}

	b := start(t, bin, env)
	defer b.kill(t)
	hits := b.search(t, "pull_request matrix", 5)
	if !hits.contains(path) {
		t.Errorf("after deleting the index, %q lost %s\n  got: %s",
			"pull_request matrix", path, hits.summary())
	}
}

// TestRoundTrip_FragmentsAreDemotedNotExcluded pins both halves of the measured
// rank penalty (scripts/health/results/week1/RANK-PENALTY-NOTES.md).
//
// Half one: a miner fragment loses to a real note they both match.
// Half two: that same fragment is still returned when it is the only match.
// Exclusion is the sin that left recall returning nothing for four months.
func TestRoundTrip_FragmentsAreDemotedNotExcluded(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	// The shape of the real problem, in miniature. Miner fragments are short and
	// quote the operator's own words, so BM25 — which rewards term density in
	// short documents — puts a wall of them above the one filed note that actually
	// answers the question. A corpus with a single fragment in it cannot show this,
	// because the filed note wins on length normalization alone and the penalty
	// never has to do anything.
	//
	// Twelve fragments is also what makes this a test of the over-fetch window:
	// the filed note sits well below rank five on raw score, so re-ranking only
	// the top k cannot reach it, which is the entire reason the window is 200.
	for i := 1; i <= 12; i++ {
		env.write(t, fmt.Sprintf("personal/_inbox/workflow-syncthing-%03d.md", i), `---
type: workflow
status: inbox
mining_confidence: 0.31
captured: 2026-08-04T11:00:00Z
---
User stated: the phone never runs git.
`)
	}
	// The filed note: longer, says it once, and is the note a person wants back.
	target := "personal/2026/08/syncthing-excludes-the-git-directory.md"
	env.write(t, target, `---
type: convention
status: active
captured: 2026-08-04T10:00:00Z
slug: syncthing-excludes-the-git-directory
title: How the phone stays in sync without git
---
Syncthing keeps the phone's working tree in step with the laptop, and the
repository directory itself stays out of the sync set. The phone never runs git;
it sees and edits plain files exactly as it does today, and Obsidian on Android
notices nothing about the transport underneath it. The daemon on the laptop picks
up whatever changed and commits it with an attribution recording where the edit
came from, which is what keeps the history readable once two machines are writing
into the same tree. Backup and multi-machine access fall out of that for free:
push to a bare repository on any box on the home network, several remotes if you
want them, and no new design to argue about.
`)
	// A fragment that is the only thing in the corpus matching its subject.
	onlyMatch := "personal/_inbox/idea-kombucha-772.md"
	env.write(t, onlyMatch, `---
type: idea
status: inbox
mining_confidence: 0.22
captured: 2026-08-04T12:00:00Z
---
User stated: the second ferment of a kombucha needs airtight bottles or it will
never carbonate.
`)

	d := start(t, bin, env)
	defer d.kill(t)

	hits := d.search(t, "phone never runs git", 5)
	ri := hits.rank(target)
	if ri < 0 {
		t.Errorf("twelve miner fragments buried the filed note out of the top 5 — "+
			"this is the junk-competition failure the penalty exists to fix\n  got: %s",
			hits.summary())
	} else if ri != 0 {
		t.Errorf("the filed note came back at #%d, behind %d fragment(s)\n  got: %s",
			ri+1, ri, hits.summary())
	}

	// Never exclude. This is the sin that left recall returning nothing for four
	// months: a penalized note that is the best thing the corpus has still comes
	// back first.
	only := d.search(t, "kombucha carbonate", 5)
	if !only.contains(onlyMatch) {
		t.Errorf("a penalized note that is the only match was excluded\n  got: %s", only.summary())
	}
}

// TestRoundTrip_PromotedFragmentKeepsItsScore pins the status gate on the shape
// rule. 229 of the 232 notes in personal/preferences/ are fragment-shaped *and*
// filed, because the promotion pipeline promoted fragment bodies verbatim.
// Filing overrides the miner's fingerprint; skipping this gate demotes them all.
func TestRoundTrip_PromotedFragmentKeepsItsScore(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	// Same body shape, same subject, same length. The only difference is status.
	env.write(t, "personal/preferences/prefers-tabs-in-makefiles.md", `---
type: preference
status: active
captured: 2026-08-04T10:00:00Z
---
User stated: a Makefile recipe has to be indented with a hard tab, and an editor
that helpfully expands it to spaces breaks the build in a way that reads as a
syntax error somewhere else entirely.
`)
	env.write(t, "personal/_inbox/preference-makefiles-108.md", `---
type: preference
status: inbox
mining_confidence: 0.28
captured: 2026-08-04T10:05:00Z
---
User stated: a Makefile recipe has to be indented with a hard tab, and an editor
that helpfully expands it to spaces breaks the build in a way that reads as a
syntax error somewhere else entirely.
`)

	d := start(t, bin, env)
	defer d.kill(t)

	hits := d.search(t, "Makefile hard tab", 5)
	promoted := "personal/preferences/prefers-tabs-in-makefiles.md"
	unfiled := "personal/_inbox/preference-makefiles-108.md"
	pi, ui := hits.rank(promoted), hits.rank(unfiled)
	if pi < 0 {
		t.Fatalf("the promoted note did not come back\n  got: %s", hits.summary())
	}
	if ui >= 0 && ui < pi {
		t.Errorf("the unfiled twin outranked the promoted note\n  got: %s", hits.summary())
	}

	// The assertion that actually pins the gate: the promoted note's served score
	// must be its unmodified BM25 score.
	//
	// Ranking alone cannot detect a missing gate, and it took a surviving mutant to
	// notice. Remove the gate and this note is multiplied by 0.30 while its unfiled
	// twin is multiplied by 0.30 x 0.60 — so the promoted note still ranks first,
	// the order looks correct, and 229 notes in personal/preferences/ have
	// quietly sunk beneath every unpenalized note in the corpus. The damage is
	// invisible in a two-note corpus and total in the real one.
	row := hits.row(promoted)
	score, raw := floatOf(row, "score"), floatOf(row, "raw_score")
	if score != raw {
		t.Errorf("promoted fragment-shaped note was demoted: score %.4f != raw %.4f (classes %q).\n"+
			"Filing is the signal that overrides the miner's fingerprint — this is the "+
			"gate that protects 229 of the 232 notes in personal/preferences/",
			score, raw, hits.penaltyOf(promoted))
	}
	if got := hits.penaltyOf(promoted); got != note.ClassFragmentPromoted {
		t.Errorf("promoted note classified %q, want %q — the class must still be "+
			"reported so the decision is visible, just not weighted",
			got, note.ClassFragmentPromoted)
	}
	// And the unfiled twin must be demoted, or the gate is simply off everywhere.
	twin := hits.row(unfiled)
	if s, r := floatOf(twin, "score"), floatOf(twin, "raw_score"); s == r && r != 0 {
		t.Errorf("the unfiled twin was not demoted at all: score %.4f == raw %.4f", s, r)
	}
}

// TestRoundTrip_AliasesRankAboveBodyMentions covers the column the design asks for
// even though it measures as a no-op today: only 5.5% of the corpus has anything
// in aliases or tags, so it cannot move a score yet. Dreaming's alias backfill is
// what fills it, and this is the test that will notice if the column silently
// stops working before then.
//
// It has to assert rank rather than mere findability. Frontmatter is indexed into
// the body column too, so an alias is discoverable even with the dedicated column
// empty — which is precisely why the first version of this test proved nothing.
func TestRoundTrip_AliasesRankAboveBodyMentions(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	const filler = `The daemon writes the file first and updates the index second,
because the file is truth and the index is a cache that can be rebuilt from it.
Capture never waits on judgment, so it works with the laptop closed to the network
and stays safe to call at any volume the miner cares to reach.`

	// Enough documents that "clobber" is genuinely rare. BM25's IDF term is
	// log((N-n+0.5)/(n+0.5)), so in a two-document corpus where both match, it goes
	// negative and every score collapses to roughly zero — a ranking test on a
	// corpus that small measures nothing. Ten notes that do not contain the term
	// give it something to be rare against.
	for i := 1; i <= 10; i++ {
		env.write(t, fmt.Sprintf("personal/2026/08/filler-%02d.md", i), `---
type: reference
status: active
captured: 2026-08-05T09:00:00Z
---
`+filler+"\n")
	}

	// Two notes carrying the same rare term the same number of times, in documents
	// of the same length, differing only in which frontmatter key holds it:
	// `aliases` feeds the dedicated column, `slug` does not. Holding length fixed
	// is the point — an earlier version of this test compared an alias against a
	// prose mention and the prose won on BM25's length normalization, which says
	// nothing about whether the column works.
	aliased := "personal/2026/08/a-note.md"
	env.write(t, aliased, `---
type: preference
status: active
captured: 2026-08-05T10:00:00Z
aliases: [clobber]
---
`+filler+"\n")

	notAliased := "personal/2026/08/b-note.md"
	env.write(t, notAliased, `---
type: preference
status: active
captured: 2026-08-05T10:00:00Z
slug: clobber
---
`+filler+"\n")

	d := start(t, bin, env)
	defer d.kill(t)

	hits := d.search(t, "clobber", 5)
	if !hits.contains(aliased) || !hits.contains(notAliased) {
		t.Fatalf("both notes should match a term they both contain\n  got: %s", hits.summary())
	}
	aScore := floatOf(hits.row(aliased), "score")
	bScore := floatOf(hits.row(notAliased), "score")
	if aScore <= bScore {
		t.Errorf("the aliased note scored %.4f against %.4f for an identical note "+
			"carrying the term outside the aliases/tags column — that column is not "+
			"contributing its weight, so dreaming's alias backfill will land somewhere "+
			"inert\n  got: %s", aScore, bScore, hits.summary())
	}
}

// TestRoundTrip_StemmingFindsMorphologicalVariants pins porter stemming, the one
// tokenizer knob measured to move hit@5 at all (+5.7 points).
func TestRoundTrip_StemmingFindsMorphologicalVariants(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	target := "personal/2026/08/the-promotion-pipeline.md"
	env.write(t, target, `---
type: reference
status: active
captured: 2026-08-05T10:00:00Z
---
The promotion pipeline promotes fragment bodies verbatim, which is why so many
filed notes still carry the miner's fingerprint.
`)

	d := start(t, bin, env)
	defer d.kill(t)

	// The note says "promotes" and "promotion". Nothing in it says "promoting".
	// Without porter stemming this is a vocabulary miss and returns nothing.
	for _, q := range []string{"promoting fragment", "pipelines promoted"} {
		if hits := d.search(t, q, 5); !hits.contains(target) {
			t.Errorf("query %q missed %s — morphological variants are counting as "+
				"vocabulary misses, which is what porter stemming is for\n  got: %s",
				q, target, hits.summary())
		}
	}
}

// TestRoundTrip_TemporalBounds covers the second-weakest stratum. Episodic
// questions are time questions and the driver previously had no way to say one.
func TestRoundTrip_TemporalBounds(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	env.write(t, "personal/2026/06/vault-root-renamed.md", `---
type: convention
status: active
captured: 2026-06-17T09:00:00Z
---
The vault root was renamed this month, so a cached absolute path literal is now
wrong in a way that still reads as valid.
`)
	env.write(t, "personal/2026/08/vault-moves-to-local-disk.md", `---
type: convention
status: active
captured: 2026-08-02T09:00:00Z
---
The vault root moves off the synced mount onto local disk inside a private git
repository, because a database on a synced path is a known corruption pattern.
`)

	d := start(t, bin, env)
	defer d.kill(t)

	all := d.search(t, "vault root", 5)
	if len(all.rows) != 2 {
		t.Fatalf("unbounded search should see both notes, saw %d\n  got: %s", len(all.rows), all.summary())
	}

	julyOn := d.searchBounded(t, "vault root", 5, "2026-07-01", "")
	if julyOn.contains("personal/2026/06/vault-root-renamed.md") {
		t.Errorf("after:2026-07-01 returned a June note\n  got: %s", julyOn.summary())
	}
	if !julyOn.contains("personal/2026/08/vault-moves-to-local-disk.md") {
		t.Errorf("after:2026-07-01 dropped the August note\n  got: %s", julyOn.summary())
	}

	preJuly := d.searchBounded(t, "vault root", 5, "", "2026-07-01")
	if !preJuly.contains("personal/2026/06/vault-root-renamed.md") {
		t.Errorf("before:2026-07-01 dropped the June note\n  got: %s", preJuly.summary())
	}
	if preJuly.contains("personal/2026/08/vault-moves-to-local-disk.md") {
		t.Errorf("before:2026-07-01 returned an August note\n  got: %s", preJuly.summary())
	}
}

// TestRoundTrip_CaptureDateSurvivesAnEdit pins the one frontmatter field the design
// calls immutable. Every other field is editable — filing changes status, re-typing
// changes type — but the capture date records an event in the daemon's own life
// rather than a claim about the world, and the shard a note is born into is the one
// it dies in.
//
// The failure this prevents is quiet: with almost no notes in the corpus carrying a
// `captured:` field, dates come from filesystem mtime, so recomputing on every
// write would move a note's capture date whenever anyone edited it, and a
// cloud-sync client rewriting mtimes would shift every temporal bound at once.
func TestRoundTrip_CaptureDateSurvivesAnEdit(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	// No `captured:` field, so the date can only come from the filesystem.
	rel := "personal/2026/08/dated-by-mtime.md"
	env.write(t, rel, "---\ntype: idea\nstatus: unfiled\n---\nA distinctive marmalade fact.\n")

	d := start(t, bin, env)
	defer d.kill(t)

	first := d.search(t, "marmalade", 5)
	if !first.contains(rel) {
		t.Fatalf("setup: the note was not indexed\n  got: %s", first.summary())
	}
	before, _ := first.row(rel)["captured"].(string)
	if src, _ := first.row(rel)["captured_source"].(string); src != "mtime" {
		t.Fatalf("setup: expected an mtime-dated note, got source %q", src)
	}

	// Edit it, which moves the mtime.
	time.Sleep(1100 * time.Millisecond)
	env.write(t, rel, "---\ntype: idea\nstatus: unfiled\n---\nA distinctive marmalade fact, revised.\n")

	deadline := time.Now().Add(20 * time.Second)
	for {
		hits := d.search(t, "marmalade revised", 5)
		if hits.contains(rel) {
			after, _ := hits.row(rel)["captured"].(string)
			if after != before {
				t.Errorf("editing a note moved its capture date from %s to %s; the "+
					"capture date is immutable and determines the shard", before, after)
			}
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("the edit was never re-indexed\n  got: %s", hits.summary())
		}
		time.Sleep(250 * time.Millisecond)
	}
}

// TestRoundTrip_KnowsWhatItDoesNotKnow guards the negative stratum. The measured
// cost of the OR rewrite was 18.8 points here: a system that never returns an
// empty set hands the agent five plausible notes and it names one.
func TestRoundTrip_KnowsWhatItDoesNotKnow(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	// The corpus deliberately shares vocabulary with the question below. A negative
	// test whose query has no token anywhere in the corpus is not a test of
	// anything: it comes back empty under any query semantics, including the OR
	// rewrite that loses 18.8 points on exactly this stratum. The realistic and
	// dangerous case is the one where the corpus has adjacent words and nothing
	// more — the operator asks about hummingbird migration, and the vault happens
	// to contain notes about a data migration and about flight rules.
	env.write(t, "personal/2026/08/the-git-transport-migration.md", `---
type: convention
status: active
captured: 2026-08-04T10:00:00Z
---
The git transport migration moves the vault onto local disk. Nothing in the
daemon's first build depends on that landing first.
`)
	env.write(t, "personal/2026/08/routes-through-the-promotion-door.md", `---
type: convention
status: active
captured: 2026-08-04T10:00:00Z
---
Everything reaching the operator's spaces routes through the promotion door, and
the door refuses to land a change whose link check comes back red.
`)

	d := start(t, bin, env)
	defer d.kill(t)

	hits := d.search(t, "hummingbird migration routes", 5)
	if len(hits.rows) != 0 {
		t.Errorf("a question the corpus cannot answer returned %d results — an index "+
			"that never comes back empty hands the agent plausible notes and it names "+
			"one, which cost 18.8 points of correct rejection when measured\n  got: %s",
			len(hits.rows), hits.summary())
	}
	// And it says so, rather than returning silence the agent has to interpret.
	if hits.note == "" {
		t.Error("an empty result set came back with no explanatory note for the driver")
	}
	// The corpus really does contain those words separately, so the emptiness above
	// is the AND semantics working rather than an accident of an unrelated corpus.
	for _, q := range []string{"migration", "routes"} {
		if len(d.search(t, q, 5).rows) == 0 {
			t.Fatalf("corpus setup is wrong: %q should match something", q)
		}
	}
}

// TestRoundTrip_CaptureNeverWaitsOnJudgment is the build's most important
// constraint, as an executable claim. Capture makes something exist and findable
// without a model call, without the network, and fast enough that no caller is
// ever tempted to make it conditional.
func TestRoundTrip_CaptureNeverWaitsOnJudgment(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	// Every outbound HTTP path in the process is pointed at a closed port, and
	// the credentials a model call would need are removed. If capture reaches
	// for judgment, it fails or hangs here instead of passing quietly.
	d := start(t, bin, env,
		"ANTHROPIC_API_KEY=",
		"HTTP_PROXY=http://127.0.0.1:1",
		"HTTPS_PROXY=http://127.0.0.1:1",
		"ALL_PROXY=http://127.0.0.1:1",
		"NO_PROXY=",
	)
	defer d.kill(t)

	started := time.Now()
	res := d.capture(t, captureArgs{
		Title:  "Capture is one transaction",
		Text:   "Write the file, upsert the index, done. No model call and no network on this path.",
		Type:   "convention",
		Status: "active",
	})
	elapsed := time.Since(started)
	if elapsed > 2*time.Second {
		t.Errorf("capture took %v; it is on no model's critical path and should be milliseconds", elapsed)
	}
	path := res.str(t, "path")
	if _, err := os.Stat(filepath.Join(env.vault, path)); err != nil {
		t.Fatalf("offline capture did not land on disk: %v", err)
	}
	// Findable immediately, in the same process, with no filing step in between.
	if hits := d.search(t, "upsert the index", 5); !hits.contains(path) {
		t.Errorf("captured note was not findable immediately\n  got: %s", hits.summary())
	}
}

// TestRoundTrip_WatcherPicksUpAnOutsideEdit covers the phone's path: a file that
// appears in the vault without the daemon writing it still becomes findable.
func TestRoundTrip_WatcherPicksUpAnOutsideEdit(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	d := start(t, bin, env)
	defer d.kill(t)

	env.write(t, "personal/2026/08/edited-on-the-phone.md", `---
type: idea
status: unfiled
captured: 2026-08-08T07:30:00Z
---
An index that has to be rebuilt by hand is a database pretending to be a cache.
`)

	target := "personal/2026/08/edited-on-the-phone.md"
	deadline := time.Now().Add(30 * time.Second)
	for {
		if d.search(t, "database pretending", 5).contains(target) {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("the watcher never indexed %s\n  got: %s",
				target, d.search(t, "database pretending", 5).summary())
		}
		time.Sleep(250 * time.Millisecond)
	}
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

var (
	buildOnce sync.Once
	buildPath string
	buildErr  error
)

// buildDaemon compiles the real binary, once per test run, with cgo off — the
// same way it ships.
func buildDaemon(t *testing.T) string {
	t.Helper()
	buildOnce.Do(func() {
		dir, err := os.MkdirTemp("", "agentmd-build-")
		if err != nil {
			buildErr = err
			return
		}
		name := "agentmd"
		if runtime.GOOS == "windows" {
			// Windows will not execute a file without the extension, and every
			// test here runs the binary rather than calling into it.
			name += ".exe"
		}
		buildPath = filepath.Join(dir, name)
		cmd := exec.Command("go", "build", "-o", buildPath, "./cmd/agentmd")
		cmd.Dir = repoRoot(t)
		cmd.Env = append(os.Environ(), "CGO_ENABLED=0")
		if out, err := cmd.CombinedOutput(); err != nil {
			buildErr = fmt.Errorf("go build: %v\n%s", err, out)
		}
	})
	if buildErr != nil {
		t.Fatal(buildErr)
	}
	return buildPath
}

func repoRoot(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	return filepath.Dir(wd) // e2e/ -> daemon/
}

type vaultEnv struct {
	vault  string
	index  string
	config string
}

// newVault builds a throwaway vault plus the config file the daemon resolves its
// vault path from. Nothing here touches the operator's real vault.
func newVault(t *testing.T) *vaultEnv {
	t.Helper()
	root := t.TempDir()
	env := &vaultEnv{
		vault:  filepath.Join(root, "vault"),
		index:  filepath.Join(root, "state", "index.db"),
		config: filepath.Join(root, "config.json"),
	}
	for _, d := range []string{env.vault, filepath.Dir(env.index)} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	cfg := map[string]any{
		"schema_version":                    2,
		"plugins.obsidian-vault.vault_path": env.vault,
		"storage.backend":                   "vault",
	}
	blob, _ := json.MarshalIndent(cfg, "", "  ")
	if err := os.WriteFile(env.config, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	return env
}

// setConfigKey rewrites one key in the throwaway kernel config, so a test can
// exercise a setting whose real value is empty until a later migration.
func (v *vaultEnv) setConfigKey(t *testing.T, key string, value any) {
	t.Helper()
	blob, err := os.ReadFile(v.config)
	if err != nil {
		t.Fatal(err)
	}
	var cfg map[string]any
	if err := json.Unmarshal(blob, &cfg); err != nil {
		t.Fatal(err)
	}
	cfg[key] = value
	out, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(v.config, out, 0o644); err != nil {
		t.Fatal(err)
	}
}

func (v *vaultEnv) write(t *testing.T, rel, body string) {
	t.Helper()
	p := filepath.Join(v.vault, rel)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

type proc struct {
	cmd  *exec.Cmd
	addr string
	log  *bytes.Buffer
	mu   sync.Mutex
	done bool
}

var addrRe = regexp.MustCompile(`listening (http://[0-9.]+:\d+)`)

// start launches the daemon on an ephemeral port and waits for it to be serving.
func start(t *testing.T, bin string, env *vaultEnv, extraEnv ...string) *proc {
	t.Helper()
	p := &proc{log: &bytes.Buffer{}}
	p.cmd = exec.Command(bin, "serve",
		"--config", env.config,
		"--index", env.index,
		"--port", "0",
		"--reconcile", "1s",
		// No model. `serve` otherwise discovers whatever embedder is installed
		// and spawns it, so this suite would load a 333MB model per daemon it
		// starts — slow everywhere, and on a developer machine it competes with
		// whatever real work is running for the same GPU. Nothing here exercises
		// the vector arm; the tests that do live in internal/embed and drive an
		// httptest server.
		"-no-embedder",
	)
	p.cmd.Env = append(os.Environ(), extraEnv...)
	stdout, err := p.cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	p.cmd.Stderr = p.log
	if err := p.cmd.Start(); err != nil {
		t.Fatal(err)
	}

	// Read stdout until the daemon announces its address.
	type result struct{ addr string }
	found := make(chan result, 1)
	go func() {
		buf := make([]byte, 4096)
		var acc bytes.Buffer
		for {
			n, err := stdout.Read(buf)
			if n > 0 {
				acc.Write(buf[:n])
				p.mu.Lock()
				p.log.Write(buf[:n])
				p.mu.Unlock()
				if m := addrRe.FindStringSubmatch(acc.String()); m != nil {
					found <- result{m[1]}
					// Keep draining so the pipe never blocks the daemon.
					go io.Copy(io.Discard, stdout)
					return
				}
			}
			if err != nil {
				close(found)
				return
			}
		}
	}()

	select {
	case r, ok := <-found:
		if !ok {
			t.Fatalf("daemon exited before it started serving\n%s", p.logs())
		}
		p.addr = r.addr
	case <-time.After(60 * time.Second):
		_ = p.cmd.Process.Kill()
		t.Fatalf("daemon never announced an address\n%s", p.logs())
	}
	return p
}

func (p *proc) logs() string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.log.String()
}

func (p *proc) kill(t *testing.T) {
	t.Helper()
	if p.done {
		return
	}
	p.done = true
	if p.cmd.Process != nil {
		_ = p.cmd.Process.Kill()
	}
	_ = p.cmd.Wait()
}

type captureArgs struct {
	Title   string   `json:"title,omitempty"`
	Text    string   `json:"text"`
	Type    string   `json:"type,omitempty"`
	Status  string   `json:"status,omitempty"`
	Tags    []string `json:"tags,omitempty"`
	Aliases []string `json:"aliases,omitempty"`
	Source  string   `json:"source,omitempty"`
}

type toolResult map[string]any

func (r toolResult) str(t *testing.T, key string) string {
	t.Helper()
	s, _ := r[key].(string)
	return s
}

func (p *proc) capture(t *testing.T, args captureArgs) toolResult {
	t.Helper()
	raw, err := json.Marshal(args)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	_ = json.Unmarshal(raw, &m)
	return p.call(t, "memory_capture", m)
}

type hitList struct {
	rows []map[string]any
	note string
}

func (h hitList) rank(path string) int {
	for i, r := range h.rows {
		if s, _ := r["path"].(string); s == path {
			return i
		}
	}
	return -1
}

func (h hitList) contains(path string) bool { return h.rank(path) >= 0 }

func (h hitList) row(path string) map[string]any {
	if i := h.rank(path); i >= 0 {
		return h.rows[i]
	}
	return nil
}

func floatOf(row map[string]any, key string) float64 {
	if row == nil {
		return 0
	}
	f, _ := row[key].(float64)
	return f
}

func (h hitList) penaltyOf(path string) string {
	for _, r := range h.rows {
		if s, _ := r["path"].(string); s == path {
			pen, _ := r["penalty"].(string)
			return pen
		}
	}
	return ""
}

func (h hitList) summary() string {
	if len(h.rows) == 0 {
		return fmt.Sprintf("0 results (note: %q)", h.note)
	}
	var b strings.Builder
	for i, r := range h.rows {
		path, _ := r["path"].(string)
		score, _ := r["score"].(float64)
		pen, _ := r["penalty"].(string)
		fmt.Fprintf(&b, "\n    %d. %-64s score=%.4f", i+1, path, score)
		if pen != "" {
			fmt.Fprintf(&b, " penalty=%s", pen)
		}
	}
	return b.String()
}

func (p *proc) search(t *testing.T, query string, k int) hitList {
	t.Helper()
	return p.searchBounded(t, query, k, "", "")
}

func (p *proc) searchBounded(t *testing.T, query string, k int, after, before string) hitList {
	t.Helper()
	args := map[string]any{"query": query, "k": k}
	if after != "" {
		args["after"] = after
	}
	if before != "" {
		args["before"] = before
	}
	res := p.call(t, "memory_search", args)
	out := hitList{}
	out.note, _ = res["note"].(string)
	rows, _ := res["results"].([]any)
	for _, r := range rows {
		if m, ok := r.(map[string]any); ok {
			out.rows = append(out.rows, m)
		}
	}
	return out
}

// call issues one MCP tools/call over the daemon's HTTP surface and returns the
// tool's structured result. Deliberately the same path a Claude Code session
// uses — a test that reaches past this proves nothing about whether the tool is
// reachable, which was the original failure.
func (p *proc) call(t *testing.T, tool string, args map[string]any) toolResult {
	t.Helper()
	body, err := json.Marshal(map[string]any{
		"jsonrpc": "2.0",
		"id":      1,
		"method":  "tools/call",
		"params":  map[string]any{"name": tool, "arguments": args},
	})
	if err != nil {
		t.Fatal(err)
	}
	req, err := http.NewRequest("POST", p.addr+"/mcp", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		t.Fatalf("%s: %v\n%s", tool, err, p.logs())
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		t.Fatalf("%s: HTTP %d: %s", tool, resp.StatusCode, raw)
	}

	var env struct {
		Error *struct {
			Message string `json:"message"`
		} `json:"error"`
		Result struct {
			IsError           bool           `json:"isError"`
			StructuredContent map[string]any `json:"structuredContent"`
			Content           []struct {
				Text string `json:"text"`
			} `json:"content"`
		} `json:"result"`
	}
	if err := json.Unmarshal(raw, &env); err != nil {
		t.Fatalf("%s: undecodable response: %v\n%s", tool, err, raw)
	}
	if env.Error != nil {
		t.Fatalf("%s: JSON-RPC error: %s", tool, env.Error.Message)
	}
	if env.Result.IsError {
		msg := ""
		if len(env.Result.Content) > 0 {
			msg = env.Result.Content[0].Text
		}
		t.Fatalf("%s: tool error: %s", tool, msg)
	}
	if env.Result.StructuredContent != nil {
		return env.Result.StructuredContent
	}
	// Fall back to the text block for clients that only read content[].
	if len(env.Result.Content) > 0 {
		var m map[string]any
		if err := json.Unmarshal([]byte(env.Result.Content[0].Text), &m); err == nil {
			return m
		}
	}
	t.Fatalf("%s: response carried no structured result: %s", tool, raw)
	return nil
}
