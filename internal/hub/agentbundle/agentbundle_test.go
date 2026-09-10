package agentbundle

import (
	"bytes"
	"testing"
)

func TestParse(t *testing.T) {
	data := append([]byte("hub"), []byte(magic)...)
	data = append(data, []byte("amd64 3\nabcarm64 4\ndefg")...)
	payloads, err := parse(data)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(payloads["amd64"], []byte("abc")) || !bytes.Equal(payloads["arm64"], []byte("defg")) {
		t.Fatalf("unexpected payloads: %#v", payloads)
	}
}

func TestParseUnbundled(t *testing.T) {
	payloads, err := parse([]byte("hub"))
	if err != nil {
		t.Fatal(err)
	}
	if len(payloads) != 0 {
		t.Fatalf("unexpected payloads: %#v", payloads)
	}
}
