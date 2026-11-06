package execution

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestRunCommandSuccess(t *testing.T) {
	r := New()
	res, err := r.Run(context.Background(), "t1", RunConfig{
		Command: "echo hello", Timeout: 10 * time.Second,
	})
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if res.Status != "SUCCESS" {
		t.Fatalf("expected SUCCESS, got %s", res.Status)
	}
	if res.ExitCode != 0 {
		t.Fatalf("expected exit 0, got %d", res.ExitCode)
	}
	if !strings.Contains(res.Stdout, "hello") {
		t.Fatalf("stdout missing hello: %q", res.Stdout)
	}
}

func TestRunOutputHardLimit(t *testing.T) {
	r := New()
	res, err := r.Run(context.Background(), "limit", RunConfig{
		Command:        "head -c 2000000 /dev/zero | tr '\\0' 'a'",
		Timeout:        10 * time.Second,
		MaxStdoutBytes: 4 << 20,
		MaxStderrBytes: 4 << 20,
		MaxTotalBytes:  8 << 20,
	})
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(res.Stdout) > 512<<10 {
		t.Fatalf("stdout = %d bytes, want <= 512KiB", len(res.Stdout))
	}
	if !res.StdoutTruncated {
		t.Fatal("stdout must be marked truncated")
	}
}

func TestRunCommandFailure(t *testing.T) {
	r := New()
	res, err := r.Run(context.Background(), "t1", RunConfig{
		Command: "exit 3", Timeout: 10 * time.Second,
	})
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if res.Status != "FAILED" {
		t.Fatalf("expected FAILED, got %s", res.Status)
	}
	if res.ExitCode != 3 {
		t.Fatalf("expected exit 3, got %d", res.ExitCode)
	}
}

func TestRunCommandTimeout(t *testing.T) {
	r := New()
	res, err := r.Run(context.Background(), "t1", RunConfig{
		Command: "sleep 30", Timeout: 500 * time.Millisecond,
	})
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if res.Status != "TIMED_OUT" {
		t.Fatalf("expected TIMED_OUT, got %s", res.Status)
	}
}

func TestRunScript(t *testing.T) {
	r := New()
	res, err := r.Run(context.Background(), "t1", RunConfig{
		Script: "#!/bin/sh\necho script-ran", Interpreter: "shell",
		Timeout: 10 * time.Second,
	})
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if res.Status != "SUCCESS" {
		t.Fatalf("expected SUCCESS, got %s", res.Status)
	}
	if !strings.Contains(res.Stdout, "script-ran") {
		t.Fatalf("stdout missing script-ran: %q", res.Stdout)
	}
}

func TestStderrCapture(t *testing.T) {
	r := New()
	res, _ := r.Run(context.Background(), "t1", RunConfig{
		Command: "echo out; echo err >&2", Timeout: 10 * time.Second,
	})
	if !strings.Contains(res.Stdout, "out") {
		t.Fatalf("stdout missing")
	}
	if !strings.Contains(res.Stderr, "err") {
		t.Fatalf("stderr missing")
	}
}

func TestLogTruncation(t *testing.T) {
	r := New()
	res, _ := r.Run(context.Background(), "t1", RunConfig{
		Command:        "yes x | head -c 1000000",
		Timeout:        10 * time.Second,
		MaxStdoutBytes: 1000,
	})
	if !res.StdoutTruncated {
		t.Fatalf("expected stdout truncated")
	}
	if len(res.Stdout) > 1000 {
		t.Fatalf("stdout too large: %d", len(res.Stdout))
	}
}

func TestProcessGroupCleanup(t *testing.T) {
	// 验证超时后子进程被杀
	r := New()
	res, _ := r.Run(context.Background(), "t1", RunConfig{
		Command: "sleep 1 & sleep 30", Timeout: 500 * time.Millisecond,
	})
	if res.Status != "TIMED_OUT" {
		t.Fatalf("expected TIMED_OUT, got %s", res.Status)
	}
}

// TestStreamWriterChunkSplitting 验证大块输出被切分为不超过 logChunkSize 的分片，
// 避免单帧超 WebSocket 消息限制导致连接断开（Bug C）。
func TestStreamWriterChunkSplitting(t *testing.T) {
	var chunks [][]byte
	sw := &streamWriter{
		buf:     &limitedBuffer{limit: 1 << 20},
		stream:  "stdout",
		onChunk: func(_ string, c []byte) { chunks = append(chunks, c) },
	}
	big := make([]byte, 300*1024) // 300KB > 4 * 64KB
	for i := range big {
		big[i] = 'x'
	}
	n, err := sw.Write(big)
	if err != nil {
		t.Fatalf("write: %v", err)
	}
	if n != len(big) {
		t.Fatalf("write returned %d, want %d", n, len(big))
	}
	if len(chunks) < 4 {
		t.Fatalf("expected multiple chunks, got %d", len(chunks))
	}
	for i, c := range chunks {
		if len(c) > logChunkSize {
			t.Fatalf("chunk %d too large: %d > %d", i, len(c), logChunkSize)
		}
	}
	var joined []byte
	for _, c := range chunks {
		joined = append(joined, c...)
	}
	if string(joined) != string(big) {
		t.Fatalf("chunked content mismatch")
	}
}
