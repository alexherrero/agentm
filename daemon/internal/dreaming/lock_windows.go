//go:build windows

package dreaming

import "os"

// processAlive: on Windows FindProcess opens a handle, which fails for a pid
// that no longer exists.
func processAlive(pid int) (alive, known bool) {
	p, err := os.FindProcess(pid)
	if err != nil {
		return false, true
	}
	p.Release()
	return true, true
}
