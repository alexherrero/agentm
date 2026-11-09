//go:build !darwin

package pulse

import "time"

// LastWake has no reading off macOS; the grade then measures wall time.
func LastWake() time.Time { return time.Time{} }
