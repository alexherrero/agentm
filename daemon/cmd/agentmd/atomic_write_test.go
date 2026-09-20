package main

import (
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// The night rewrites cards a phone may still be syncing through Drive
// (agentm-vault plan 16). `os.WriteFile` truncates first and fills after, so a
// reader that opens the file in between sees an empty or half-written note.
// These pin the replacement: a reader sees the old bytes or the new ones, and
// never a prefix of either.

func TestAtomicWriteIsNeverObservedHalfDone(t *testing.T) {
	dir := t.TempDir()
	dest := filepath.Join(dir, "a-card.md")
	before := "---\ntitle: before\n---\n\n" + strings.Repeat("old ", 20000)
	after := "---\ntitle: after\n---\n\n" + strings.Repeat("new ", 20000)
	if err := os.WriteFile(dest, []byte(before), 0o644); err != nil {
		t.Fatal(err)
	}

	stop := make(chan struct{})
	var wg sync.WaitGroup
	wg.Add(1)
	var bad []string
	var mu sync.Mutex
	go func() {
		defer wg.Done()
		for {
			select {
			case <-stop:
				return
			default:
			}
			raw, err := os.ReadFile(dest)
			if err != nil {
				// The file never stops existing: a rename replaces it in one
				// step, where a truncate-and-fill leaves a window with nothing
				// in it.
				mu.Lock()
				bad = append(bad, "the card vanished mid-write: "+err.Error())
				mu.Unlock()
				return
			}
			if s := string(raw); s != before && s != after {
				mu.Lock()
				bad = append(bad, "a reader saw neither version whole "+
					"(len "+itoa(len(raw))+")")
				mu.Unlock()
				return
			}
		}
	}()

	deadline := time.Now().Add(300 * time.Millisecond)
	for i := 0; time.Now().Before(deadline); i++ {
		body := after
		if i%2 == 1 {
			body = before
		}
		if err := atomicWrite(dest, body); err != nil {
			t.Fatal(err)
		}
	}
	close(stop)
	wg.Wait()
	mu.Lock()
	defer mu.Unlock()
	for _, b := range bad {
		t.Error(b)
	}
}

func TestAtomicWriteLeavesNoTemporaryFileBehind(t *testing.T) {
	dir := t.TempDir()
	dest := filepath.Join(dir, "a-card.md")
	if err := atomicWrite(dest, "---\ntitle: a\n---\n\nbody\n"); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	if len(names) != 1 || names[0] != "a-card.md" {
		t.Errorf("the directory holds %v, want just the card — a leftover "+
			"temporary file in the drop folder is a card the review pass would "+
			"list", names)
	}
	// 0644, the same mode the vault's other writers use: a card the operator
	// cannot open in Obsidian is not an improvement on a torn one.
	st, err := os.Stat(dest)
	if err != nil {
		t.Fatal(err)
	}
	if got := st.Mode().Perm(); got != 0o644 {
		t.Errorf("mode %v, want 0644", got)
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}
