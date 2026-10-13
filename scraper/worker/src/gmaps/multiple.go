package gmaps

import (
	"encoding/json"
	"fmt"
	"strings"

	olc "github.com/google/open-location-code/go"
)

func ParseSearchResults(raw []byte) ([]*Entry, error) {
	var data []any
	if err := json.Unmarshal(raw, &data); err != nil {
		return nil, fmt.Errorf("failed to unmarshal JSON: %w", err)
	}

	if len(data) == 0 {
		return nil, fmt.Errorf("empty JSON data")
	}

	container, ok := data[0].([]any)
	if !ok || len(container) == 0 {
		return nil, fmt.Errorf("invalid business list structure")
	}

	if len(container) < 2 {
		return nil, fmt.Errorf("missing business list")
	}
	items, ok := container[1].([]any)
	if !ok {
		return nil, fmt.Errorf("invalid business list")
	}
	if len(items) == 0 {
		return nil, fmt.Errorf("missing search metadata")
	}

	entries := make([]*Entry, 0, len(items))

	for i := 0; i < len(items); i++ {
		arr, ok := items[i].([]any)
		if !ok {
			return nil, fmt.Errorf("invalid business row")
		}

		business := getNthElementAndCast[[]any](arr, 14)
		// The first row is metadata for multi-result searches, but contains the
		// business itself for single-result searches. Never discard it blindly.
		if len(business) == 0 && i == 0 {
			if len(arr) < 11 || getNthElementAndCast[string](arr, 8) == "" || getNthElementAndCast[string](arr, 9) == "" {
				return nil, fmt.Errorf("invalid search metadata")
			}
			continue
		}
		if len(business) < 12 || getNthElementAndCast[string](business, 11) == "" {
			return nil, fmt.Errorf("invalid business details")
		}

		var entry Entry

		entry.ID = getNthElementAndCast[string](business, 0)
		entry.Title = getNthElementAndCast[string](business, 11)
		entry.Categories = toStringSlice(getNthElementAndCast[[]any](business, 13))
		entry.WebSite = getNthElementAndCast[string](business, 7, 0)

		entry.ReviewRating = getNthElementAndCast[float64](business, 4, 7)
		entry.ReviewCount = int(getNthElementAndCast[float64](business, 4, 8))

		fullAddress := getNthElementAndCast[[]any](business, 2)

		entry.Address = func() string {
			sb := strings.Builder{}

			for i, part := range fullAddress {
				if i > 0 {
					sb.WriteString(", ")
				}

				sb.WriteString(fmt.Sprintf("%v", part))
			}

			return sb.String()
		}()

		entry.Latitude = getNthElementAndCast[float64](business, 9, 2)
		entry.Longtitude = getNthElementAndCast[float64](business, 9, 3)
		entry.Phone = strings.ReplaceAll(getNthElementAndCast[string](business, 178, 0, 0), " ", "")
		entry.OpenHours = getHours(business)
		entry.Status = getNthElementAndCast[string](business, 34, 4, 4)
		entry.Timezone = getNthElementAndCast[string](business, 30)
		entry.DataID = getNthElementAndCast[string](business, 10)

		entry.PlusCode = olc.Encode(entry.Latitude, entry.Longtitude, 10)

		entries = append(entries, &entry)
	}

	return entries, nil
}

func toStringSlice(arr []any) []string {
	ans := make([]string, 0, len(arr))
	for _, v := range arr {
		ans = append(ans, fmt.Sprintf("%v", v))
	}

	return ans
}
