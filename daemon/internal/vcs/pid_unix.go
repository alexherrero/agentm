//go:build !windows

package vcs

import (
	"errors"
	"syscall"
)

// processAlive asks the kernel with a null signal. ESRCH is a dead pid;
// EPERM is a live pid owned by someone else, which is still alive. The same
// check guards the dreaming lock (internal/dreaming/lock_unix.go).
func processAlive(pid int) (alive, known bool) {
	err := syscall.Kill(pid, 0)
	if err == nil {
		return true, true
	}
	if errors.Is(err, syscall.ESRCH) {
		return false, true
	}
	if errors.Is(err, syscall.EPERM) {
		return true, true
	}
	return false, false
}
