package monitoring

import (
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"time"
)

// CompareHTTP evaluates a fresh HTTP observation without inventing values.
// Expiry timestamps use RFC3339, including their explicit timezone.
func CompareHTTP(observation HTTPObservation, now time.Time, maxAge time.Duration, operator, expected string) (bool, error) {
	if maxAge <= 0 || observation.ObservedAt.IsZero() || observation.ObservedAt.After(now) || now.Sub(observation.ObservedAt) > maxAge {
		return false, errors.New("observation is stale or invalid")
	}
	if operator == "status_eq" {
		code, err := strconv.Atoi(expected)
		if err != nil || code < 100 || code > 599 || observation.StatusCode == 0 {
			return false, errors.New("invalid HTTP status comparison")
		}
		return observation.StatusCode == code, nil
	}
	if !observation.HasValue {
		return false, errors.New("observation value is missing")
	}
	switch operator {
	case "eq":
		switch value := observation.Value.(type) {
		case string:
			return value == expected, nil
		case bool:
			v, err := strconv.ParseBool(expected)
			if err != nil {
				return false, errors.New("invalid boolean comparison")
			}
			return value == v, nil
		case json.Number:
			a, errA := value.Float64()
			b, errB := strconv.ParseFloat(expected, 64)
			if errA != nil || errB != nil || math.IsNaN(a) || math.IsNaN(b) || math.IsInf(a, 0) || math.IsInf(b, 0) || math.Abs(a) >= 1<<53 || math.Abs(b) >= 1<<53 {
				return false, errors.New("invalid numeric comparison")
			}
			return a == b, nil
		}
	case "lt", "gt":
		value, ok := observation.Value.(json.Number)
		if !ok {
			return false, errors.New("numeric comparison requires a number")
		}
		a, errA := value.Float64()
		b, errB := strconv.ParseFloat(expected, 64)
		if errA != nil || errB != nil || math.IsNaN(a) || math.IsNaN(b) || math.IsInf(a, 0) || math.IsInf(b, 0) || math.Abs(a) >= 1<<53 || math.Abs(b) >= 1<<53 {
			return false, errors.New("invalid numeric comparison")
		}
		if operator == "lt" {
			return a < b, nil
		}
		return a > b, nil
	case "expires_within":
		value, ok := observation.Value.(string)
		if !ok {
			return false, errors.New("expiry comparison requires a timestamp")
		}
		expires, err := time.Parse(time.RFC3339, value)
		window, durationErr := time.ParseDuration(expected)
		if err != nil || durationErr != nil || window < 0 {
			return false, errors.New("invalid expiry comparison")
		}
		return !expires.After(now.Add(window)), nil
	}
	return false, errors.New("unsupported comparison")
}
