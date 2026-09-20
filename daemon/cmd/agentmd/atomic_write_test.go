package main

import (
	"os"
	"path/filepath"
	"runtime"
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
	writes, refused := 0, 0
	for i := 0; time.Now().Before(deadline); i++ {
		body := after
		if i%2 == 1 {
			body = before
		}
		if err := atomicWrite(dest, body); err != nil {
			// On Windows a rename over a destination somebody holds open can
			// still be refused after the bounded retry. That is a *reported*
			// failure, which the applier records against a journal entry it
			// wrote first — the thing this test is about is that no reader
			// ever sees a half-written card, and a refused write cannot
			// produce one. Anywhere else a refusal is a real failure.
			if runtime.GOOS == "windows" {
				refused++
				continue
			}
			t.Fatal(err)
		}
		writes++
	}
	if writes == 0 {
		t.Fatalf("no write landed in the window (%d refused) — the probe proved "+
			"nothing", refused)
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
	// cannot open in Obsidian is not an improvement on a torn one. Windows has
	// no POSIX mode — `os.Chmod` there moves the read-only bit and nothing
	// else — so the assertion is about the platforms where a mode means
	// something, and what is checked there instead is that the file is
	// writable.
	st, err := os.Stat(dest)
	if err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS == "windows" {
		if st.Mode().Perm()&0o200 == 0 {
			t.Errorf("the card landed read-only: %v", st.Mode().Perm())
		}
	} else if got := st.Mode().Perm(); got != 0o644 {
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
