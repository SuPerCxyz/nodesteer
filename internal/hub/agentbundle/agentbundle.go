// Package agentbundle reads Agent payloads appended to a Hub executable.
package agentbundle

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
)

const magic = "\nCADENTRA_AGENT_BUNDLE_V1\n"

// LoadExecutable loads the amd64/arm64 Agent payloads appended by the build
// bundle step. A normal unbundled development binary returns an empty map.
func LoadExecutable() (map[string][]byte, error) {
	executable, err := os.Executable()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(executable)
	if err != nil {
		return nil, err
	}
	return parse(data)
}

func parse(data []byte) (map[string][]byte, error) {
	start := bytes.LastIndex(data, []byte(magic))
	if start < 0 {
		return map[string][]byte{}, nil
	}
	pos := start + len(magic)
	payloads := map[string][]byte{}
	for pos < len(data) {
		lineEnd := bytes.IndexByte(data[pos:], '\n')
		if lineEnd < 0 {
			return nil, errors.New("invalid agent bundle header")
		}
		fields := strings.Fields(string(data[pos : pos+lineEnd]))
		if len(fields) != 2 || (fields[0] != "amd64" && fields[0] != "arm64") {
			return nil, errors.New("invalid agent bundle architecture")
		}
		size, err := strconv.ParseInt(fields[1], 10, 64)
		if err != nil || size < 0 || size > int64(len(data)) {
			return nil, fmt.Errorf("invalid agent bundle size for %s", fields[0])
		}
		pos += lineEnd + 1
		end := pos + int(size)
		if end < pos || end > len(data) {
			return nil, fmt.Errorf("truncated agent bundle for %s", fields[0])
		}
		payloads[fields[0]] = append([]byte(nil), data[pos:end]...)
		pos = end
	}
	return payloads, nil
}
