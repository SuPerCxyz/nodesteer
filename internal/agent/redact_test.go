package agent

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/agent/execution"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// x16Markers 取自 X16 复现证据（.e2e/.../qa-resume-20261017-g8），为无害标记值。
const (
	x16Split    = "QAMARK-HALVES-8970c813-0123456789abcdef"
	x16Boundary = "QAMARK-BOUNDARY-8970c813-fedcba9876543210"
	x16Cross    = "QAMARK-CROSS-8970c813-abcdef0123456789"
)

// feedChunks 按固定大小喂给脱敏器并收尾，返回完整输出。
func feedChunks(t *testing.T, idx *secretIndex, input string, size int) string {
	t.Helper()
	if size < 1 {
		t.Fatalf("invalid chunk size %d", size)
	}
	r := newStreamRedactor(idx)
	var out strings.Builder
	for start := 0; start < len(input); start += size {
		end := min(start+size, len(input))
		out.WriteString(r.write(input[start:end], false))
		if len(r.hold) > maxRunHold+size+minFragmentLen {
			t.Fatalf("保留字节超过上界：%d", len(r.hold))
		}
	}
	out.WriteString(r.write("", true))
	return out.String()
}

// assertNoFragment 断言输出不含 Secret 的任意长度 >= minFragmentLen 的连续片段。
func assertNoFragment(t *testing.T, output, secret string) {
	t.Helper()
	if len(secret) < minFragmentLen {
		if strings.Contains(output, secret) {
			t.Fatalf("输出仍含短 Secret %q：%q", secret, output)
		}
		return
	}
	for i := 0; i+minFragmentLen <= len(secret); i++ {
		if frag := secret[i : i+minFragmentLen]; strings.Contains(output, frag) {
			t.Fatalf("输出仍含 Secret 分片（offset %d）%q：\n%q", i, frag, output)
		}
	}
}

func TestStreamRedactorSplitAndFlush(t *testing.T) {
	secret := "split-secret-value"
	idx := newSecretIndex([]string{secret, "split-secret"})
	for _, size := range []int{1, 5, 64 << 10} {
		input := strings.Repeat("x", 64<<10-5) + secret + " ordinary tail"
		got := feedChunks(t, idx, input, size)
		want := strings.Repeat("x", 64<<10-5) + redactedMarker + " ordinary tail"
		if got != want {
			t.Fatalf("chunk size %d did not preserve ordinary output", size)
		}
		assertNoFragment(t, got, secret)
	}
	r := newStreamRedactor(idx)
	var live strings.Builder
	live.WriteString(r.write("ordinary message", false))
	live.WriteString(r.write("split-sec", false))
	assertNoFragment(t, live.String(), secret)
	if got := r.write("", true); got != redactedMarker {
		t.Fatalf("interrupted secret tail exposed: %q", got)
	}
}

func TestStreamRedactorInterleavedStreams(t *testing.T) {
	idx := newSecretIndex([]string{"secret"})
	stdout, stderr := newStreamRedactor(idx), newStreamRedactor(idx)
	out := stdout.write("first sec", false)
	err := stderr.write("second se", false)
	out += stdout.write("ret done", false) + stdout.write("", true)
	err += stderr.write("cret done", false) + stderr.write("", true)
	if out != "first [REDACTED] done" || err != "second [REDACTED] done" {
		t.Fatalf("interleaved streams: %q %q", out, err)
	}
}

// TestStreamRedactorX16Variants 覆盖 X16 三个拆片形态：同分片前后段、跨流两段、大输出头尾两段。
func TestStreamRedactorX16Variants(t *testing.T) {
	t.Run("same-chunk-halves", func(t *testing.T) {
		// 复现 task_split：先去尾 8 字符，再去头 8 字符，两段落在同一 stdout 分片。
		idx := newSecretIndex([]string{x16Split})
		input := x16Split[:len(x16Split)-8] + x16Split[8:] + "\n"
		got := feedChunks(t, idx, input, 64<<10)
		assertNoFragment(t, got, x16Split)
		if strings.Count(got, redactedMarker) < 1 || !strings.HasSuffix(got, "\n") {
			t.Fatalf("split halves not masked: %q", got)
		}
		// 两段分批到达（含跨分片续接）时同样必须遮蔽。
		var out strings.Builder
		r := newStreamRedactor(idx)
		out.WriteString(r.write(x16Split[:len(x16Split)-8], false))
		out.WriteString(r.write(x16Split[8:]+"\n", false))
		out.WriteString(r.write("", true))
		assertNoFragment(t, out.String(), x16Split)
	})

	t.Run("cross-stream", func(t *testing.T) {
		// 复现 task_fail：stdout 去头段，stderr 去尾段，两流并集即完整值。
		idx := newSecretIndex([]string{x16Cross})
		outR, errR := newStreamRedactor(idx), newStreamRedactor(idx)
		out := outR.write(x16Cross[8:], false)
		errOut := errR.write(x16Cross[:len(x16Cross)-8], false)
		outTail, errTail := finishStreamPair(outR, errR)
		out, errOut = out+outTail, errOut+errTail
		assertNoFragment(t, out, x16Cross)
		assertNoFragment(t, errOut, x16Cross)
		assertNoFragment(t, out+errOut, x16Cross)
		assertNoFragment(t, errOut+out, x16Cross)
	})

	t.Run("boundary-head-tail", func(t *testing.T) {
		// 复现 1MiB 边界形态：头段 + 填充 + 尾段，按 32KiB 分片下发。
		idx := newSecretIndex([]string{x16Boundary})
		head, tail := x16Boundary[:len(x16Boundary)-19], x16Boundary[len(x16Boundary)-19:]
		filler := strings.Repeat("x", 1<<20)
		input := head + filler + tail + "\n"
		for _, size := range []int{32 << 10, 4096} {
			got := feedChunks(t, idx, input, size)
			assertNoFragment(t, got, x16Boundary)
			if !strings.Contains(got, strings.Repeat("x", 4096)) {
				t.Fatalf("filler lost for chunk size %d", size)
			}
			if !strings.HasSuffix(got, "\n") {
				t.Fatalf("trailing ordinary byte lost for chunk size %d", size)
			}
		}
	})
}

// TestStreamRedactorChunkBoundaryMatrix 覆盖值跨 chunk 边界、分片前缀/后缀、重复出现等边界。
func TestStreamRedactorChunkBoundaryMatrix(t *testing.T) {
	secret := "value-exactly-across-chunk-boundary"
	idx := newSecretIndex([]string{secret})
	inputs := map[string]string{
		"full":       "aa" + secret + "zz",
		"prefix":     "aa" + secret[:len(secret)-8] + "zz",
		"suffix":     "aa" + secret[8:] + "zz",
		"middle":     "aa" + secret[4:len(secret)-4] + "zz",
		"twice":      "aa" + secret + "|" + secret + "zz",
		"overlap":    "aa" + secret + secret[8:] + "zz",
		"head-tail":  "aa" + secret[:11] + strings.Repeat("q", 300) + secret[11:] + "zz",
		"byte-wise":  "aa" + secret + "zz",
		"no-secret":  "aa" + strings.Repeat("z", 64) + "zz",
		"empty-tail": "aa" + secret,
	}
	for name, input := range inputs {
		for _, size := range []int{1, 2, 3, 7, 8, 9, 15, 16, 17, 31, 64, 4096} {
			got := feedChunks(t, idx, input, size)
			if strings.Contains(input, secret) {
				assertNoFragment(t, got, secret)
				if !strings.Contains(got, redactedMarker) {
					t.Fatalf("%s/chunk=%d 未遮蔽完整值：%q", name, size, got)
				}
			}
			if name == "full" && got != "aa"+redactedMarker+"zz" {
				t.Fatalf("chunk=%d 普通字节被改动：%q", size, got)
			}
			if name == "no-secret" && got != input {
				t.Fatalf("无命中输入被改动：%q", got)
			}
		}
	}
	// 值恰好从每个偏移跨 chunk 边界。
	for split := 1; split < len(secret); split++ {
		input := "aa" + secret + "zz"
		r := newStreamRedactor(idx)
		out := r.write(input[:2+split], false) + r.write(input[2+split:], false) + r.write("", true)
		if out != "aa"+redactedMarker+"zz" {
			t.Fatalf("split=%d 跨边界未正确遮蔽：%q", split, out)
		}
	}
}

// TestStreamRedactorLongValue 覆盖值长于保留窗口、以及长于遮蔽区间上界的情形。
func TestStreamRedactorLongValue(t *testing.T) {
	long := "LONG-" + strings.Repeat("ab12", 1024) // 4KiB+
	if got := feedChunks(t, newSecretIndex([]string{long}), "p"+long+"q", 512); got != "p"+redactedMarker+"q" {
		t.Fatalf("长值未被整体遮蔽：%q", got)
	}
	huge := strings.Repeat("H9z", (maxRunHold+8192)/3) // 超过遮蔽区间上界
	idx := newSecretIndex([]string{huge})
	got := feedChunks(t, idx, "p"+huge+"q", 64<<10)
	assertNoFragment(t, got, huge)
	if !strings.HasPrefix(got, "p") || !strings.HasSuffix(got, "q") {
		t.Fatalf("超长值两侧普通字节丢失：%q", got[:min(len(got), 32)])
	}
}

func TestStreamRedactorMultipleSecrets(t *testing.T) {
	secrets := []string{"primary-secret-0123456789", "tok", "0123456789"}
	idx := newSecretIndex(secrets)
	input := "a tok b " + secrets[0][3:20] + " c " + secrets[0] + " d " + secrets[1] + " e 0123456789 f"
	got := feedChunks(t, idx, input, 7)
	for _, s := range secrets {
		assertNoFragment(t, got, s)
	}
	if !strings.HasPrefix(got, "a ") || !strings.HasSuffix(got, " f") {
		t.Fatalf("多值场景普通字节被改动：%q", got)
	}
}

func TestStreamRedactorBinaryOutput(t *testing.T) {
	secret := "\x00\x01\xff\xfeSECRET\x80\x81"
	idx := newSecretIndex([]string{secret})
	input := "a\x00\xffb" + secret + "c\x80\x81d"
	got := feedChunks(t, idx, input, 3)
	assertNoFragment(t, got, secret)
	if !strings.HasPrefix(got, "a\x00\xffb") || !strings.HasSuffix(got, "c\x80\x81d") {
		t.Fatalf("二进制普通字节被改动：%q", got)
	}
	// 无 Secret 时二进制输出必须逐字节直通。
	plain := feedChunks(t, newSecretIndex([]string{secret}), "a\x00\xffb\x80\x81c", 3)
	if plain != "a\x00\xffb\x80\x81c" {
		t.Fatalf("无命中二进制输出被改动：%q", plain)
	}
}

func TestStreamRedactorNoSecretsFastPath(t *testing.T) {
	for _, secrets := range [][]string{nil, {""}, {"", ""}} {
		idx := newSecretIndex(secrets)
		r := newStreamRedactor(idx)
		for _, chunk := range []string{"ordinary", "", "\x00\xff binary", strings.Repeat("z", 128)} {
			if got := r.write(chunk, false); got != chunk {
				t.Fatalf("无 Secret 时未直通：%q", got)
			}
			if r.hold != "" || r.scanned != 0 || r.safe != 0 || r.run != -1 {
				t.Fatalf("无 Secret 时产生了缓冲状态：%+v", r)
			}
		}
		if got := r.write("", true); got != "" {
			t.Fatalf("无 Secret 收尾输出非空：%q", got)
		}
		if got := redact("plain text", nil); got != "plain text" {
			t.Fatalf("redact 无 Secret 未直通：%q", got)
		}
	}
}

// TestStreamRedactorCrossStreamTail 覆盖两流各留一段（单独看均不足阈值）的跨流拼接形态。
func TestStreamRedactorCrossStreamTail(t *testing.T) {
	secret := "CROSS-TAIL-0123456789"
	idx := newSecretIndex([]string{secret})
	outR, errR := newStreamRedactor(idx), newStreamRedactor(idx)
	out := outR.write("out "+secret[:5], false)
	errOut := errR.write("err "+secret[5:], false)
	outTail, errTail := finishStreamPair(outR, errR)
	out, errOut = out+outTail, errOut+errTail
	assertNoFragment(t, out, secret)
	assertNoFragment(t, errOut, secret)
	assertNoFragment(t, out+errOut, secret)
	if !strings.HasPrefix(out, "out ") || !strings.HasPrefix(errOut, "err ") {
		t.Fatalf("跨流场景普通字节被改动：%q %q", out, errOut)
	}
	// 两流尾部无关时不应误伤。
	outR, errR = newStreamRedactor(idx), newStreamRedactor(idx)
	out = outR.write("done-abc", false)
	errOut = errR.write("done-xyz", false)
	outTail, errTail = finishStreamPair(outR, errR)
	if out+outTail != "done-abc" || errOut+errTail != "done-xyz" {
		t.Fatalf("无关尾部被误伤：%q %q", out+outTail, errOut+errTail)
	}
}

// TestRedactStreamsMatchesLivePath 断言终态字段脱敏与实时分片脱敏结果一致。
func TestRedactStreamsMatchesLivePath(t *testing.T) {
	secret := "consistency-secret-value"
	idx := newSecretIndex([]string{secret})
	stdout := "out " + secret[:12] + " mid " + secret[6:] + " end"
	stderr := "err " + secret[3:19]
	for _, size := range []int{1, 5, 32} {
		var live strings.Builder
		outR, errR := newStreamRedactor(idx), newStreamRedactor(idx)
		for start := 0; start < len(stdout); start += size {
			live.WriteString(outR.write(stdout[start:min(start+size, len(stdout))], false))
		}
		outTail, _ := finishStreamPair(outR, errR)
		liveOut := live.String() + outTail
		termOut, _ := redactStreams(idx, stdout, stderr)
		if liveOut != termOut {
			t.Fatalf("chunk=%d 实时与终态不一致：%q vs %q", size, liveOut, termOut)
		}
	}
}

func TestSecretTerminalJournalBoundaries(t *testing.T) {
	secret := "front-secret-half-back-secret-tail!"
	prefix, suffix := secret[:len(secret)/2], secret[len(secret)/2:]
	for _, tc := range []struct {
		name, command, status, stdout, stderr string
		max, retry                            int
		cancel                                bool
	}{
		{"split-streams", `printf '%s' "$PREFIX"; printf '%s' "$SUFFIX" >&2`, models.ExecStatusSuccess, "[REDACTED]", "[REDACTED]", 0, 0, false},
		{"truncated", `printf '%s' "$SECRET"`, models.ExecStatusSuccess, "[REDACTED]", "", len(prefix), 0, false},
		{"timeout", `printf '%s' "$PREFIX"; sleep 30`, models.ExecStatusTimedOut, "[REDACTED]", "", 0, 0, false},
		{"cancel", `printf '%s' "$PREFIX"; : > "$MARKER"; sleep 30`, models.ExecStatusCanceled, "[REDACTED]", "", 0, 0, true},
		{"retry", `if [ -f "$MARKER" ]; then printf '%s' "$SUFFIX"; else printf '%s' "$PREFIX"; : > "$MARKER"; exit 1; fi`, models.ExecStatusSuccess, "[REDACTED]", "", 0, 1, false},
		{"ordinary", `printf 'ordinary stdout tail'; printf 'ordinary stderr tail' >&2`, models.ExecStatusSuccess, "ordinary stdout tail", "ordinary stderr tail", 0, 0, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a := newTestAgent(t, Config{MaxLogBytes: tc.max})
			a.ready = true
			marker := filepath.Join(t.TempDir(), "started")
			p := protocol.RunExecutionPayload{ExecutionID: "redact-" + tc.name, TaskID: "redact-owned", Type: models.TaskTypeCommand,
				Command: tc.command, Timeout: 5, Retry: tc.retry, SecretValues: []string{secret},
				Environment: map[string]string{"SECRET": secret, "PREFIX": prefix, "SUFFIX": suffix, "MARKER": marker}}
			if tc.name == "timeout" {
				p.Timeout = 1
			}
			a.OnRunExecution(protocol.NewEnvelope(protocol.MsgRunExecution, p.ExecutionID, p))
			deadline := time.Now().Add(10 * time.Second)
			if tc.cancel {
				for {
					if _, err := os.Stat(marker); err == nil {
						break
					}
					if time.Now().After(deadline) {
						t.Fatal("command did not reach cancel marker")
					}
					time.Sleep(5 * time.Millisecond)
				}
				a.OnCancelExecution(protocol.NewEnvelope(protocol.MsgCancelExecution, p.ExecutionID, protocol.CancelExecutionPayload{ExecutionID: p.ExecutionID}))
			}
			var ex *LocalExecution
			for {
				ex, _ = a.store.GetExecution(a.ctx, p.ExecutionID)
				if ex != nil && ex.Status != models.ExecStatusRunning {
					break
				}
				if time.Now().After(deadline) {
					t.Fatal("execution did not finish")
				}
				time.Sleep(5 * time.Millisecond)
			}
			if ex.Status != tc.status || ex.Stdout != tc.stdout || ex.Stderr != tc.stderr {
				t.Fatalf("terminal result not safely preserved: status=%s stdout=%q stderr=%q", ex.Status, ex.Stdout, ex.Stderr)
			}
			if strings.Contains(ex.Stdout+ex.Stderr, secret) {
				t.Fatal("terminal streams can reconstruct Secret")
			}
			assertNoFragment(t, ex.Stdout, secret)
			assertNoFragment(t, ex.Stderr, secret)
			if tc.name == "truncated" && !ex.StdoutTruncated {
				t.Fatal("truncation flag lost")
			}
		})
	}
}

func TestSecretFlushAfterRunnerTermination(t *testing.T) {
	const secret = "secret-value-with-tail"
	for _, name := range []string{"timeout", "cancel"} {
		t.Run(name, func(t *testing.T) {
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			idx := newSecretIndex([]string{secret})
			r := newStreamRedactor(idx)
			var live strings.Builder
			timeout := 100 * time.Millisecond
			if name == "cancel" {
				timeout = 5 * time.Second
			}
			result, err := execution.New().Run(ctx, "flush-owned", execution.RunConfig{Command: `printf '%s' "$PREFIX"; sleep 30`, Environment: map[string]string{"PREFIX": "secret-value"}, Timeout: timeout,
				OnLogChunk: func(_ string, chunk []byte) {
					live.WriteString(r.write(string(chunk), false))
					if name == "cancel" {
						cancel()
					}
				}})
			if err != nil {
				t.Fatal(err)
			}
			if r.run < 0 || live.Len() != 0 {
				t.Fatal("incomplete Secret was not retained during execution")
			}
			live.WriteString(r.write("", true))
			if r.hold != "" || live.String() != "[REDACTED]" || idx.redact(result.Stdout) != live.String() {
				t.Fatalf("termination tail was not flushed consistently: %q", live.String())
			}
			if name == "cancel" && result.Status != models.ExecStatusCanceled || name == "timeout" && result.Status != models.ExecStatusTimedOut {
				t.Fatalf("unexpected termination: %s", result.Status)
			}
		})
	}
}

func benchmarkStream(b *testing.B, secrets []string) {
	const size = 2 << 20
	var input strings.Builder
	for input.Len() < size {
		input.WriteString("2026-10-17T04:00:00Z INFO ordinary log line without secrets\n")
	}
	chunk := 64 << 10
	idx := newSecretIndex(secrets)
	b.SetBytes(int64(input.Len()))
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		r := newStreamRedactor(idx)
		text := input.String()
		for start := 0; start < len(text); start += chunk {
			r.write(text[start:min(start+chunk, len(text))], false)
		}
		r.write("", true)
	}
}

func BenchmarkStreamRedactorNoSecrets(b *testing.B) { benchmarkStream(b, nil) }

func BenchmarkStreamRedactorWithSecrets(b *testing.B) {
	benchmarkStream(b, []string{"bench-secret-value-0123456789abcdef", "short", "another-token-0123456789"})
}

// runSecretCommand 通过真实执行链路跑一条命令，返回终态记录。
func runSecretCommand(t *testing.T, name, command string, secrets []string, env map[string]string) *LocalExecution {
	t.Helper()
	a := newTestAgent(t, Config{})
	a.ready = true
	p := protocol.RunExecutionPayload{ExecutionID: "x16-" + name, TaskID: "x16-owned", Type: models.TaskTypeCommand,
		Command: command, Timeout: 20, SecretValues: secrets, Environment: env}
	a.OnRunExecution(protocol.NewEnvelope(protocol.MsgRunExecution, p.ExecutionID, p))
	deadline := time.Now().Add(30 * time.Second)
	for {
		ex, _ := a.store.GetExecution(a.ctx, p.ExecutionID)
		if ex != nil && ex.Status != models.ExecStatusRunning {
			return ex
		}
		if time.Now().After(deadline) {
			t.Fatalf("%s: execution did not finish", name)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// TestSecretX16EvidenceScripts 用 X16 证据中的真实脚本与标记值复现三个拆片形态，
// 断言终态 stdout/stderr 单独与并集都不含 >= minFragmentLen 的 Secret 分片。
func TestSecretX16EvidenceScripts(t *testing.T) {
	head, tail := x16Boundary[:len(x16Boundary)-19], x16Boundary[len(x16Boundary)-19:]
	cases := []struct {
		name, command, secret string
		env                   map[string]string
	}{
		{"full", `printf '%s\n' "$S"`, x16Split, nil},
		{"split-halves", `printf '%s' "${S%????????}"; sleep 1.5; printf '%s\n' "${S#????????}"`, x16Split, nil},
		{"cross-stream", `printf '%s' "${S#????????}"; printf '%s\n' "${S%????????}" >&2; exit 7`, x16Cross, nil},
		{"boundary-head-tail", `printf '%s' "$HEAD"; head -c 200000 /dev/zero | tr '\0' x; printf '%s\n' "$TAIL"`, x16Boundary,
			map[string]string{"HEAD": head, "TAIL": tail}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			env := map[string]string{"S": tc.secret}
			for k, v := range tc.env {
				env[k] = v
			}
			ex := runSecretCommand(t, tc.name, tc.command, []string{tc.secret}, env)
			if ex == nil {
				t.Fatal("execution record missing")
			}
			assertNoFragment(t, ex.Stdout, tc.secret)
			assertNoFragment(t, ex.Stderr, tc.secret)
			assertNoFragment(t, ex.Stdout+ex.Stderr, tc.secret)
			assertNoFragment(t, ex.Stderr+ex.Stdout, tc.secret)
			if tc.name == "boundary-head-tail" && !strings.Contains(ex.Stdout, strings.Repeat("x", 4096)) {
				t.Fatal("filler lost")
			}
		})
	}
}

func TestSecretIndexBounds(t *testing.T) {
	secret := "0123456789abcdef"
	idx := newSecretIndex([]string{secret, secret, "short"})
	if want := len(secret) - minFragmentLen + 1; len(idx.grams) != want {
		t.Fatalf("分片索引未去重：%d != %d", len(idx.grams), want)
	}
	if len(idx.short) != 1 || idx.short[0] != "short" {
		t.Fatalf("短 Secret 未走整值匹配：%v", idx.short)
	}
	if !idx.isSecretPrefix("012345") || idx.isSecretPrefix("123456") {
		t.Fatal("Secret 前缀判定错误")
	}
	if !idx.couldStartFragment("89ab") || idx.couldStartFragment("fedc") {
		t.Fatal("分片起始判定错误")
	}
	if !idx.jointTailDanger("0123", "4567") || idx.jointTailDanger("zzzz", "yyyy") {
		t.Fatal("跨流尾部判定错误")
	}
}
