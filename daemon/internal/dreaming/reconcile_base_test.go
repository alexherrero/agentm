package dreaming

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Reconcile pairs the engine's record of where a note is against what is on
// disk. Both sides are paths, and until the supervised first run of 2026-09-18
// they were paths from two different roots: the sidecar keys from the vault
// root (`agent/memory/semantic/a.md`) and the walk keyed from the memory root
// (`memory/semantic/a.md`). Nothing had moved and the pass reported 694 moves.
//
// The test that missed it built its `known` map by hand from the same
// memory-root rel the walk produced, so the two agreed by construction. These
// read the sidecar the daemon actually reads, in the nested layout the vault
// actually has — the only shape in which the two bases can disagree.

// nestedVault is the shipped layout: an Obsidian vault with the memory root
// inside it. In a flat one the two bases coincide and prove nothing.
func nestedVault(t *testing.T) (vault, root string) {
	t.Helper()
	vault = t.TempDir()
	if err := os.MkdirAll(filepath.Join(vault, ".obsidian"), 0o755); err != nil {
		t.Fatal(err)
	}
	root = filepath.Join(vault, "agent")
	if err := os.MkdirAll(filepath.Join(root, "memory", "semantic"), 0o755); err != nil {
		t.Fatal(err)
	}
	return vault, root
}

func writeBaseNote(t *testing.T, root, rel, body string) string {
	t.Helper()
	p := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	text := "---\ntitle: t\n---\n\n" + body + "\n"
	if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return BodyFingerprint(text)
}

// writeSidecar writes `.lifecycle.json` the way the Python arm writes it: keyed
// from the vault root.
func writeSidecar(t *testing.T, stateDir string, entries map[string]string) {
	t.Helper()
	type entry struct {
		Fingerprint string `json:"fingerprint"`
		LastAccess  string `json:"last_access"`
	}
	rec := map[string]any{"version": 2, "entries": map[string]entry{}}
	by := rec["entries"].(map[string]entry)
	for rel, print := range entries {
		by[rel] = entry{Fingerprint: print, LastAccess: "2026-09-01T00:00:00Z"}
	}
	blob, err := json.MarshalIndent(rec, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(stateDir, ".lifecycle.json"), blob, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestAVaultWhereNothingMovedReportsNoMoves(t *testing.T) {
	vault, root := nestedVault(t)
	state := t.TempDir()

	known := map[string]string{}
	for _, name := range []string{"a", "b", "c"} {
		rel := "memory/semantic/" + name + ".md"
		print := writeBaseNote(t, root, rel, "the body of "+name)
		known["agent/"+rel] = print // as the sidecar keys it
	}
	writeSidecar(t, state, known)
	_ = vault

	got, err := KnownFingerprints(state, root)
	if err != nil {
		t.Fatal(err)
	}
	onDisk, err := FingerprintsOnDisk(root)
	if err != nil {
		t.Fatal(err)
	}
	plan := PlanReconcile(got, onDisk, nil, time.Now())

	if len(plan.Repaired) != 0 {
		t.Errorf("reported %d move(s) in a vault where nothing moved: %+v",
			len(plan.Repaired), plan.Repaired)
	}
	if plan.Vanished != 0 {
		t.Errorf("%d note(s) reported vanished; they are all still there", plan.Vanished)
	}
}

func TestTheTwoSidesAgreeOnWhatToCallAPath(t *testing.T) {
	_, root := nestedVault(t)
	state := t.TempDir()
	print := writeBaseNote(t, root, "memory/semantic/a.md", "a body")
	writeSidecar(t, state, map[string]string{"agent/memory/semantic/a.md": print})

	known, err := KnownFingerprints(state, root)
	if err != nil {
		t.Fatal(err)
	}
	onDisk, err := FingerprintsOnDisk(root)
	if err != nil {
		t.Fatal(err)
	}
	for rel := range known {
		if _, ok := onDisk[rel]; !ok {
			t.Errorf("the sidecar calls it %q and the walk does not know that name; "+
				"the walk knows %v", rel, keysOf(onDisk))
		}
	}
}

func TestAMoveIsStillFollowedInANestedVault(t *testing.T) {
	_, root := nestedVault(t)
	state := t.TempDir()

	// The sidecar remembers it in its class folder; the operator moved it to
	// the archive by hand.
	print := writeBaseNote(t, root, "archive/memory/semantic/a.md", "a body")
	writeSidecar(t, state, map[string]string{"agent/memory/semantic/a.md": print})

	known, err := KnownFingerprints(state, root)
	if err != nil {
		t.Fatal(err)
	}
	onDisk, err := FingerprintsOnDisk(root)
	if err != nil {
		t.Fatal(err)
	}
	plan := PlanReconcile(known, onDisk, nil, time.Now())

	if len(plan.Repaired) != 1 {
		t.Fatalf("paired %+v, want the one move", plan.Repaired)
	}
	want := ReconcileRow{
		From: "agent/memory/semantic/a.md",
		To:   "agent/archive/memory/semantic/a.md",
	}
	if plan.Repaired[0] != want {
		t.Errorf("paired %+v, want %+v — both sides named from the vault root",
			plan.Repaired[0], want)
	}
}

// The sidecar holds a clock for everything recall serves, which is most of the
// vault. Reconcile looks only in the memory tree, so everything else must be
// out of its question rather than reported as gone.
func TestARecordOutsideTheMemoryTreeIsNotAVanishedNote(t *testing.T) {
	_, root := nestedVault(t)
	state := t.TempDir()
	print := writeBaseNote(t, root, "memory/semantic/a.md", "a body")
	writeSidecar(t, state, map[string]string{
		"agent/memory/semantic/a.md":              print,
		"projects/agentm/decisions/a-ruling.md":   "deadbeefdeadbeef",
		"agent/diagnostics/digests/2026-01-01.md": "cafecafecafecafe",
		"personal/Home/Recipes/turkey.md":         "f00df00df00df00d",
	})

	known, err := KnownFingerprints(state, root)
	if err != nil {
		t.Fatal(err)
	}
	if len(known) != 1 {
		t.Fatalf("read %d entry/entries, want only the memory note: %v", len(known), keysOf(known))
	}
	onDisk, err := FingerprintsOnDisk(root)
	if err != nil {
		t.Fatal(err)
	}
	if plan := PlanReconcile(known, onDisk, nil, time.Now()); plan.Vanished != 0 {
		t.Errorf("%d vanished; a record outside the walk is not a missing note", plan.Vanished)
	}
}

func keysOf(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
