//go:build darwin

package pulse

import (
	"encoding/binary"
	"syscall"
	"time"
)

// LastWake is the machine's last wake from sleep, from `kern.waketime`; zero
// when it has not slept since boot or the reading fails.
func LastWake() time.Time {
	raw, err := syscall.Sysctl("kern.waketime")
	if err != nil {
		return time.Time{}
	}
	// A struct timeval, whose seconds come first as a little-endian int64.
	// syscall.Sysctl drops a trailing NUL, so pad back what it took.
	b := []byte(raw)
	for len(b) < 8 {
		b = append(b, 0)
	}
	sec := int64(binary.LittleEndian.Uint64(b[:8]))
	if sec <= 0 {
		return time.Time{}
	}
	return time.Unix(sec, 0)
}
